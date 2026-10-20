#!/usr/bin/env python
"""
Daily inference for the asset fusion model (LSTM + news sentiment + GAT).

What it does, in order:
  1. Downloads fusion_weights.pt + config.json from the Hugging Face Hub.
  2. Loads recent prices and news from the database.
  3. Scores headlines that have no sentiment yet with FinBERT (CPU, time-boxed)
     and writes the scores back to the news table.
  4. Builds the latest technical + sentiment features, the 60-day correlation
     graph and the model inputs for the newest trading day.
  5. Runs the fusion model and upserts one row per asset into `predictions`.

Environment variables
  DATABASE_URL   required  postgres:// or postgresql:// URL
  HF_REPO        required  e.g. "your-user/asset-fusion"
  HF_TOKEN       optional  needed if the repo is private
  HF_REVISION    optional  branch / tag / commit of the model repo
  FINBERT_MODEL  optional  default "ProsusAI/finbert" (a local directory also works)
  MARKET_TZ      optional  e.g. "Asia/Kolkata", used to bucket timestamptz news by local date
  SCORE_BUDGET_SEC  optional  max seconds spent on FinBERT scoring (default 360)

Usage
  python pipeline/predict.py                 # score, predict, write to the database
  python pipeline/predict.py --dry-run       # no database writes; prints predictions
  python pipeline/predict.py --csv out.csv   # also save predictions to a CSV
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import logging
import os
import random
import sys
import time

import numpy as np
import pandas as pd
import pandas_ta_classic as ta
import torch
import torch.nn as nn
import torch.nn.functional as Fn
from huggingface_hub import hf_hub_download
from psycopg2.extras import execute_values
from sqlalchemy import bindparam, create_engine, text
from torch_geometric.nn import GATConv

log = logging.getLogger("predict")

# --------------------------------------------------------------------------------------
# Schema: same aliases as the training notebook ({alias: actual column name}).
# Only the columns the features actually use are selected.
# --------------------------------------------------------------------------------------
PRICES_TABLE = "prices"
PRICE_COLS = {"symbol": "asset_id", "date": "date", "high": "high", "low": "low", "close": "close"}
NEWS_TABLE = "news_items"
NEWS = {"id": "id", "asset_id": "asset_id", "headline": "headline", "date": "published_at"}
ASSETS_TABLE = "assets"
ASSET_COLS = {"id": "id", "name": "name"}        # assets.id must match prices.asset_id
PRED_TABLE = "predictions"

SENT_COLUMNS = [("sentiment_label", "text"), ("sentiment_score", "double precision"),
                ("p_positive", "double precision"), ("p_negative", "double precision"),
                ("p_neutral", "double precision")]

# Constants that define how the model was trained (kept in sync with the notebook).
CLIP = 5.0                      # scaled features are clipped to [-5, 5]
SENT_DAYS = 3                   # sent_3d = mean(P(pos) - P(neg)) over 3 calendar days
TECH_FEATURES = ["rsi14", "macd_pct", "macd_signal_pct", "macd_hist_pct", "close_sma10",
                 "close_sma30", "sma10_sma30", "atr_pct", "ret_1d", "ret_5d", "ret_20d", "vol_20"]
MIN_ROWS = 80                   # fewer price rows than this and an asset can't have a full window

# Summary-text settings (from the notebook).
TREND_DAYS, FLAT_PCT = 14, 1.0
NEWS_DAYS, TILT = 7, 0.10


@contextlib.contextmanager
def stage(name: str):
    t = time.monotonic()
    log.info("%s ...", name)
    yield
    log.info("%s done in %.1fs", name, time.monotonic() - t)


# --------------------------------------------------------------------------------------
# Model (identical to the training notebook so fusion_weights.pt loads with strict=True)
# --------------------------------------------------------------------------------------
class GAT(nn.Module):
    def __init__(self, in_dim, hidden=8, heads=4, out_dim=16, dropout=0.1):
        super().__init__()
        self.g1 = GATConv(in_dim, hidden, heads=heads, edge_dim=1, dropout=dropout, fill_value=1.0)
        self.g2 = GATConv(hidden * heads, out_dim, heads=1, edge_dim=1, dropout=dropout, fill_value=1.0)
        self.dropout = dropout

    def forward(self, x, edge_index, edge_attr):
        x = Fn.elu(self.g1(x, edge_index, edge_attr))
        x = Fn.dropout(x, self.dropout, self.training)
        return self.g2(x, edge_index, edge_attr)


class FusionModel(nn.Module):
    def __init__(self, n_feat, s_dim=2, lstm_hidden=64, lstm_layers=2, gnn_dim=16, head_hidden=32,
                 dropout=0.2, gat_hidden=8, gat_heads=4):
        super().__init__()
        self.lstm = nn.LSTM(n_feat, lstm_hidden, lstm_layers, batch_first=True, dropout=dropout)
        self.gat = GAT(n_feat, hidden=gat_hidden, heads=gat_heads, out_dim=gnn_dim)
        mk = lambda d: nn.Sequential(nn.Linear(d, head_hidden), nn.ReLU(), nn.Dropout(dropout),
                                     nn.Linear(head_hidden, 2))   # -> (return score, direction logit)
        self.heads = nn.ModuleList([mk(lstm_hidden), mk(s_dim), mk(gnn_dim)])
        self.gate = nn.Sequential(nn.Linear(lstm_hidden + s_dim + gnn_dim, head_hidden), nn.ReLU(),
                                  nn.Linear(head_hidden, 3))

    def forward(self, xw, s, gx, edge_index, edge_attr, rows):
        out, _ = self.lstm(xw)
        h = out[:, -1]
        g = self.gat(gx, edge_index, edge_attr)[rows]
        scores = torch.stack([self.heads[0](h), self.heads[1](s), self.heads[2](g)], dim=1)   # (B, 3, 2)
        w = torch.softmax(self.gate(torch.cat([h, s, g], dim=1)), dim=-1)                     # [lstm, sent, gnn]
        ret, logit = (w.unsqueeze(-1) * scores).sum(dim=1).unbind(-1)
        return {"ret": ret, "logit": logit, "w": w, "scores": scores}


def load_model(repo: str, token: str | None, revision: str | None):
    """Download weights + config from the Hub and return (model, config)."""
    cfg_path = hf_hub_download(repo, "config.json", token=token, revision=revision)
    w_path = hf_hub_download(repo, "fusion_weights.pt", token=token, revision=revision)
    with open(cfg_path) as f:
        cfg = json.load(f)

    a = cfg["architecture"]
    model = FusionModel(
        n_feat=len(cfg["features"]), s_dim=a["sentiment_dim"], lstm_hidden=a["lstm_hidden"],
        lstm_layers=a["lstm_layers"], gnn_dim=a["gnn_dim"], head_hidden=a["head_hidden"],
        dropout=a["dropout"], gat_hidden=a["gat"]["hidden"], gat_heads=a["gat"]["heads"])
    state = torch.load(w_path, map_location="cpu", weights_only=True)
    model.load_state_dict(state, strict=True)       # fails loudly if the architecture/PyG version differs
    return model.eval(), cfg


# --------------------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------------------
def make_engine(url: str):
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            url = "postgresql+psycopg2://" + url[len(prefix):]
            break
    return create_engine(url, pool_pre_ping=True)


def load_prices(engine, assets, since: pd.Timestamp) -> pd.DataFrame:
    sel = ", ".join(f"{src} AS {alias}" for alias, src in PRICE_COLS.items())
    q = text(f"SELECT {sel} FROM {PRICES_TABLE} "
             f"WHERE {PRICE_COLS['date']} >= :since AND {PRICE_COLS['symbol']} IN :ids"
             ).bindparams(bindparam("ids", expanding=True))
    with engine.connect() as conn:
        df = pd.read_sql(q, conn, params={"since": since.date(), "ids": [int(a) for a in assets]})
    df["date"] = pd.to_datetime(df["date"])
    df["symbol"] = df["symbol"].astype("int64")
    for c in ("high", "low", "close"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return (df.drop_duplicates(["symbol", "date"]).sort_values(["symbol", "date"]).reset_index(drop=True))


def load_asset_names(engine, assets) -> dict[int, str]:
    """{asset_id: display name} from the assets table (blank/NULL names are left out)."""
    q = text(f"SELECT {ASSET_COLS['id']} AS id, {ASSET_COLS['name']} AS name FROM {ASSETS_TABLE} "
             f"WHERE {ASSET_COLS['id']} IN :ids").bindparams(bindparam("ids", expanding=True))
    with engine.connect() as conn:
        df = pd.read_sql(q, conn, params={"ids": [int(a) for a in assets]})
    return {int(i): str(n).strip() for i, n in zip(df["id"], df["name"]) if pd.notna(n) and str(n).strip()}


def ensure_sentiment_columns(engine) -> None:
    """Add the sentiment columns only if missing (avoids taking a table lock on every run)."""
    with engine.connect() as conn:
        have = set(pd.read_sql(text("SELECT column_name FROM information_schema.columns "
                                    "WHERE table_name = :t"), conn, params={"t": NEWS_TABLE})["column_name"])
    missing = [(c, t) for c, t in SENT_COLUMNS if c not in have]
    if missing:
        with engine.begin() as conn:
            for col, typ in missing:
                conn.execute(text(f"ALTER TABLE {NEWS_TABLE} ADD COLUMN IF NOT EXISTS {col} {typ}"))
        log.info("added columns to %s: %s", NEWS_TABLE, [c for c, _ in missing])


def _raw_execute_values(engine, sql, rows, template=None, page_size=1000):
    raw = engine.raw_connection()
    try:
        with raw.cursor() as cur:
            execute_values(cur, sql, rows, template=template, page_size=page_size)
        raw.commit()
    except Exception:
        raw.rollback()
        raise
    finally:
        raw.close()


def write_sentiment(engine, rows) -> None:
    sql = f"""
        UPDATE {NEWS_TABLE} AS n
        SET sentiment_label = v.label, sentiment_score = v.score,
            p_positive = v.p_pos, p_negative = v.p_neg, p_neutral = v.p_neu
        FROM (VALUES %s) AS v(id, label, score, p_pos, p_neg, p_neu)
        WHERE n.{NEWS['id']} = v.id
    """
    tmpl = "(%s::bigint, %s::text, %s::float8, %s::float8, %s::float8, %s::float8)"
    _raw_execute_values(engine, sql, rows, template=tmpl)


def upsert_predictions(engine, out: pd.DataFrame) -> None:
    with engine.begin() as conn:
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {PRED_TABLE} (
                asset_id integer NOT NULL, as_of date NOT NULL, direction text,
                expected_change_pct double precision, confidence double precision,
                w_price double precision, w_news double precision, w_graph double precision, summary text)"""))
        conn.execute(text(f"CREATE UNIQUE INDEX IF NOT EXISTS {PRED_TABLE}_asset_asof_uq "
                          f"ON {PRED_TABLE} (asset_id, as_of)"))
    cols = ["asset_id", "as_of", "direction", "expected_change_pct", "confidence",
            "w_price", "w_news", "w_graph", "summary"]
    sql = f"""INSERT INTO {PRED_TABLE} ({", ".join(cols)}) VALUES %s
              ON CONFLICT (asset_id, as_of) DO UPDATE SET
              {", ".join(f"{c} = EXCLUDED.{c}" for c in cols[2:])}"""
    _raw_execute_values(engine, sql, [tuple(r) for r in out[cols].itertuples(index=False, name=None)],
                        page_size=500)


# --------------------------------------------------------------------------------------
# Price features
# --------------------------------------------------------------------------------------
def add_features(g: pd.DataFrame) -> pd.DataFrame:
    """Same indicators as the notebook, minus the training targets."""
    g = g.sort_values("date").copy()
    if len(g) < MIN_ROWS:                       # too short for the indicators; leave features NaN
        for f in TECH_FEATURES:
            g[f] = np.nan
        return g
    c, h, l = g["close"], g["high"], g["low"]

    g["rsi14"] = ta.rsi(c, length=14)

    macd = ta.macd(c, fast=12, slow=26, signal=9)
    g["macd_pct"] = macd["MACD_12_26_9"] / c
    g["macd_signal_pct"] = macd["MACDs_12_26_9"] / c
    g["macd_hist_pct"] = macd["MACDh_12_26_9"] / c

    sma10, sma30 = ta.sma(c, length=10), ta.sma(c, length=30)
    g["close_sma10"] = c / sma10 - 1
    g["close_sma30"] = c / sma30 - 1
    g["sma10_sma30"] = sma10 / sma30 - 1

    g["atr_pct"] = ta.atr(h, l, c, length=14) / c

    g["ret_1d"] = c.pct_change(1)
    g["ret_5d"] = c.pct_change(5)
    g["ret_20d"] = c.pct_change(20)
    g["vol_20"] = g["ret_1d"].rolling(20).std()
    return g


# --------------------------------------------------------------------------------------
# News sentiment (FinBERT)
# --------------------------------------------------------------------------------------
class FinBert:
    def __init__(self, name: str):
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        self.tok = AutoTokenizer.from_pretrained(name)
        self.model = AutoModelForSequenceClassification.from_pretrained(name).eval()
        self.id2label = {int(k): v.lower() for k, v in self.model.config.id2label.items()}
        by_label = {l: i for i, l in self.id2label.items()}
        self.pos, self.neg, self.neu = by_label["positive"], by_label["negative"], by_label["neutral"]

    @torch.inference_mode()
    def score(self, texts, batch_size=64, max_length=128) -> np.ndarray:
        order = np.argsort([len(t) for t in texts])          # similar lengths -> less padding
        probs = np.zeros((len(texts), len(self.id2label)), dtype=np.float32)
        for i in range(0, len(texts), batch_size):
            idx = order[i:i + batch_size]
            enc = self.tok([texts[j] for j in idx], padding=True, truncation=True,
                           max_length=max_length, return_tensors="pt")
            probs[idx] = torch.softmax(self.model(**enc).logits, dim=-1).numpy()
        return probs


def score_new_headlines(engine, since: pd.Timestamp, finbert_name: str, deadline: float,
                        dry_run: bool) -> pd.DataFrame:
    """Score headlines with no sentiment yet (newest first, stopping at `deadline`).
    Returns the newly scored rows; writes them back to the DB unless dry_run."""
    q = text(f"""
        SELECT {NEWS['id']} AS id, {NEWS['asset_id']} AS asset_id, {NEWS['headline']} AS headline,
               {NEWS['date']} AS published
        FROM {NEWS_TABLE}
        WHERE sentiment_label IS NULL
          AND {NEWS['headline']} IS NOT NULL AND btrim({NEWS['headline']}) <> ''
          AND {NEWS['date']} >= :since
        ORDER BY {NEWS['date']} DESC""")
    with engine.connect() as conn:
        todo = pd.read_sql(q, conn, params={"since": since.to_pydatetime()})
    log.info("%d unscored headlines inside the feature window", len(todo))
    if todo.empty:
        return pd.DataFrame(columns=["id", "asset_id", "published", "sentiment_label",
                                     "p_positive", "p_negative"])

    with stage("loading FinBERT"):
        fb = FinBert(finbert_name)

    done, CHUNK = [], 256
    for start in range(0, len(todo), CHUNK):
        if time.monotonic() > deadline:
            log.warning("scoring budget reached: scored %d of %d headlines; the rest are left for the next run",
                        start, len(todo))
            break
        part = todo.iloc[start:start + CHUNK]
        probs = fb.score(part["headline"].tolist())
        top = probs.argmax(axis=1)
        res = part[["id", "asset_id", "published"]].copy()
        res["sentiment_label"] = [fb.id2label[int(k)] for k in top]
        res["p_positive"], res["p_negative"] = probs[:, fb.pos], probs[:, fb.neg]
        done.append(res)
        if not dry_run:
            write_sentiment(engine, [
                (int(i), fb.id2label[int(k)], float(p[k]), float(p[fb.pos]), float(p[fb.neg]), float(p[fb.neu]))
                for i, k, p in zip(part["id"], top, probs)])
        if (start // CHUNK) % 10 == 0:
            log.info("  scored %d / %d", min(start + CHUNK, len(todo)), len(todo))
    return pd.concat(done, ignore_index=True) if done else pd.DataFrame(columns=todo.columns)


def load_scored_headlines(engine, since: pd.Timestamp) -> pd.DataFrame:
    q = text(f"""
        SELECT {NEWS['id']} AS id, {NEWS['asset_id']} AS asset_id, {NEWS['date']} AS published,
               sentiment_label, p_positive, p_negative
        FROM {NEWS_TABLE}
        WHERE sentiment_label IS NOT NULL AND {NEWS['date']} >= :since""")
    with engine.connect() as conn:
        return pd.read_sql(q, conn, params={"since": since.to_pydatetime()})


def correlation_graph(R: np.ndarray, t: int, corr_win: int, thr: float):
    """Edges between assets whose |corr| of the `corr_win` daily returns ending at date index t exceeds thr."""
    win = R[t - corr_win + 1:t + 1].T                        # (N, corr_win)
    ok = ~np.isnan(win).any(axis=1)                          # asset has a full window
    w = np.nan_to_num(win)
    w = w - w.mean(axis=1, keepdims=True)
    norm = np.sqrt((w ** 2).sum(axis=1))
    corr = (w @ w.T) / (norm[:, None] * norm[None, :] + 1e-12)
    corr[~(ok[:, None] & ok[None, :])] = 0.0
    np.fill_diagonal(corr, 0.0)                              # GAT adds its own self-loops
    corr = corr.astype(np.float32)
    src, dst = np.nonzero(np.abs(corr) > thr)
    return torch.from_numpy(np.stack([src, dst])).long(), torch.from_numpy(corr[src, dst]).unsqueeze(1)


def bucket_days(published: pd.Series, tz: str | None) -> pd.Series:
    ts = pd.to_datetime(published)
    if ts.dtype == object:                       # mixed UTC offsets (DST) come back as object
        ts = pd.to_datetime(published, utc=True)
    if getattr(ts.dt, "tz", None) is not None:
        ts = ts.dt.tz_convert(tz or "UTC").dt.tz_localize(None)
    return ts.dt.normalize()


def sentiment_features(scored: pd.DataFrame, assets, cal: pd.DatetimeIndex) -> pd.DataFrame:
    """Per (asset, calendar day): sent_3d = mean(P(pos)-P(neg)) over the last 3 days (0 if no news)
    and news_n_3d = headline count over those days."""
    daily = (scored.groupby(["asset_id", "day"])
                   .agg(s=("score", "sum"), n=("score", "size")).reset_index())
    parts = []
    for aid in assets:
        g = daily[daily["asset_id"] == aid].set_index("day")[["s", "n"]].reindex(cal, fill_value=0)
        s3 = g["s"].rolling(SENT_DAYS, min_periods=1).sum()
        n3 = g["n"].rolling(SENT_DAYS, min_periods=1).sum()
        parts.append(pd.DataFrame({"symbol": aid, "date": cal,
                                   "sent_3d": np.where(n3 > 0, s3 / n3, 0.0),
                                   "news_n_3d": n3.values}))
    return pd.concat(parts, ignore_index=True)


# --------------------------------------------------------------------------------------
# Human-readable summary (verbatim from the notebook)
# --------------------------------------------------------------------------------------
BRANCH = {"price": "the price-history branch",
          "news": "the news-sentiment branch",
          "graph": "the cross-asset graph branch"}

TREND_T = {
    "rising": ["{a} rose {pa:.1f}% over the past 14 trading days.",
               "The price of {a} has been rising, up {pa:.1f}% across the last 14 trading days.",
               "Over the last 14 trading days, {a} gained {pa:.1f}%."],
    "falling": ["{a} fell {pa:.1f}% over the past 14 trading days.",
                "The price of {a} has been falling, down {pa:.1f}% across the last 14 trading days.",
                "Over the last 14 trading days, {a} lost {pa:.1f}%."],
    "flat": ["{a} was roughly flat over the past 14 trading days, moving {p:+.1f}%.",
             "The price of {a} has been largely unchanged over the last 14 trading days ({p:+.1f}%).",
             "Over the last 14 trading days, {a} changed by only {p:+.1f}%."],
}
NEWS_T = {
    "none": ["No headlines were recorded for this asset in the last 7 days.",
             "There were no headlines for this asset in the last 7 days.",
             "This asset had no headlines in the last 7 days."],
    "positive": ["Of {n} {hl} in the last 7 days, {pp:.0f}% were positive and {nn:.0f}% negative.",
                 "Headlines over the last 7 days ({n} in total) skewed positive: {pp:.0f}% positive versus {nn:.0f}% negative.",
                 "In the last 7 days, {pp:.0f}% of the {n} {hl} were positive and {nn:.0f}% negative."],
    "negative": ["Of {n} {hl} in the last 7 days, {nn:.0f}% were negative and {pp:.0f}% positive.",
                 "Headlines over the last 7 days ({n} in total) skewed negative: {nn:.0f}% negative versus {pp:.0f}% positive.",
                 "In the last 7 days, {nn:.0f}% of the {n} {hl} were negative and {pp:.0f}% positive."],
    "mixed": ["Headlines over the last 7 days were mixed: {pp:.0f}% positive and {nn:.0f}% negative across {n} {hl}.",
              "Of {n} {hl} in the last 7 days, {pp:.0f}% were positive and {nn:.0f}% negative.",
              "The last 7 days brought {n} {hl}, {pp:.0f}% positive and {nn:.0f}% negative."],
}
FINAL_T = [
    "The model points {d} for the next day with {band} confidence ({c:.2f}), relying most on {b} ({w:.0%} of the weight).",
    "Its next-day call is {d} at {band} confidence ({c:.2f}), and {b} received the largest weight ({w:.0%}).",
    "{B} had the largest say ({w:.0%}) in a {band}-confidence ({c:.2f}) {d} call for the next day.",
]


def trend_label(pct):
    return "flat" if abs(pct) < FLAT_PCT else ("rising" if pct > 0 else "falling")


def confidence_band(c):
    return "low" if c < 0.55 else ("moderate" if c <= 0.65 else "high")


def build_summary(asset_id, trend_pct, n_pos, n_neg, n_total, direction, confidence, weights, seed=None,
                  name=None):
    rng = random.Random(seed if seed is not None else asset_id)
    label = name or f"Asset {asset_id}"                  # fall back to the id if the assets table has no name
    parts = []
    if trend_pct is not None:
        parts.append(rng.choice(TREND_T[trend_label(trend_pct)]).format(
            a=label, p=trend_pct, pa=abs(trend_pct)))
    if n_total == 0:
        parts.append(rng.choice(NEWS_T["none"]))
    else:
        pp, nn = 100 * n_pos / n_total, 100 * n_neg / n_total
        diff = (n_pos - n_neg) / n_total
        key = "positive" if diff >= TILT else ("negative" if diff <= -TILT else "mixed")
        parts.append(rng.choice(NEWS_T[key]).format(
            n=n_total, hl="headline" if n_total == 1 else "headlines", pp=pp, nn=nn))
    top = max(weights, key=weights.get)
    b = BRANCH[top]
    parts.append(rng.choice(FINAL_T).format(
        d=direction, band=confidence_band(confidence), c=confidence,
        b=b, B=b[0].upper() + b[1:], w=weights[top]))
    return " ".join(parts)


# --------------------------------------------------------------------------------------
# Main pipeline
# --------------------------------------------------------------------------------------
def run(args) -> pd.DataFrame:
    t_start = time.monotonic()
    db_url, repo = os.environ.get("DATABASE_URL"), os.environ.get("HF_REPO")
    if not db_url or not repo:
        sys.exit("DATABASE_URL and HF_REPO must be set")
    torch.set_num_threads(int(os.environ.get("TORCH_THREADS", os.cpu_count() or 1)))
    torch.set_grad_enabled(False)

    # ---- 1) model + training-time config from the Hub ----
    with stage("downloading model from the Hub"):
        model, cfg = load_model(repo, os.environ.get("HF_TOKEN"), os.environ.get("HF_REVISION"))
    feats = list(cfg["features"])
    window = int(cfg["architecture"]["window"])
    corr_win = int(cfg["graph"]["corr_window"])
    corr_thr = float(cfg["graph"]["abs_corr_threshold"])
    ret_std = float(cfg["ret_std"])
    assets = np.sort(np.array(cfg["assets"], dtype="int64"))
    N = len(assets)
    unknown = set(feats) - set(TECH_FEATURES) - {"sent_3d"}
    if unknown:
        sys.exit(f"config.json lists features this script cannot compute: {sorted(unknown)}")
    mean = np.asarray(cfg["scaler_mean"], dtype=np.float64)
    scale = np.asarray(cfg["scaler_scale"], dtype=np.float64)

    engine = make_engine(db_url)

    # ---- 2) prices ----
    with stage("loading prices"):
        today = pd.Timestamp.today().normalize()
        since = today - pd.Timedelta(days=args.lookback_days)
        df = load_prices(engine, assets, since)
    if df.empty:
        sys.exit("no price rows returned")
    dates = pd.DatetimeIndex(np.sort(df["date"].unique()))
    D = len(dates)
    min_t = max(window, corr_win + 1) - 1        # first date index with a full feature window + correlation window
    if D <= min_t:
        sys.exit(f"only {D} price dates loaded; need at least {min_t + 1} (raise --lookback-days)")
    data_date = dates[-1]
    log.info("%d price rows | %d/%d assets | %s -> %s", len(df), df["symbol"].nunique(), N,
             dates[0].date(), data_date.date())

    # Every asset is predicted from its own newest bar, as long as that bar is recent enough.
    skipped: dict[str, list[int]] = {}

    def skip(reason, mask):
        if mask.any():
            skipped.setdefault(reason, []).extend(int(a) for a in assets[mask])

    last_date = df.groupby("symbol")["date"].max().reindex(assets)
    age = (today - last_date).dt.days
    t_last = dates.get_indexer(pd.DatetimeIndex(last_date))     # date-axis position of each asset's newest bar
    nodata = last_date.isna().to_numpy()
    stale = (age > args.max_stale_days).to_numpy()
    skip("no price rows", nodata)
    skip(f"newest bar older than {args.max_stale_days} days", stale & ~nodata)
    usable = ~nodata & ~stale
    short = usable & (t_last < min_t)
    skip("too little price history", short)
    usable &= ~short
    if not usable.any():
        sys.exit(f"no asset has a recent bar and enough history: {skipped}")

    names = load_asset_names(engine, assets)
    if set(int(a) for a in assets[usable]) - set(names):
        log.warning("no name in %s for assets %s; using 'Asset <id>' for them", ASSETS_TABLE,
                    sorted(set(int(a) for a in assets[usable]) - set(names)))

    # ---- 3) news: score what's new, then build the sentiment index ----
    d0 = int(t_last[usable].min()) - window + 1        # first date index any prediction window needs
    cal = pd.date_range(dates[d0] - pd.Timedelta(days=SENT_DAYS - 1), data_date, freq="D")
    news_since = cal[0] - pd.Timedelta(days=1)          # 1-day margin for timezone bucketing
    if not args.dry_run:
        ensure_sentiment_columns(engine)
    scored_db = load_scored_headlines(engine, news_since)
    deadline = time.monotonic() + args.score_budget_sec
    new_scored = score_new_headlines(engine, news_since, os.environ.get("FINBERT_MODEL", "ProsusAI/finbert"),
                                     deadline, args.dry_run)
    scored = (pd.concat([scored_db, new_scored], ignore_index=True)
                .drop_duplicates("id", keep="last"))
    scored = scored.dropna(subset=["asset_id"]).copy()
    scored["asset_id"] = scored["asset_id"].astype("int64")
    scored["day"] = bucket_days(scored["published"], os.environ.get("MARKET_TZ") or None) if len(scored) \
        else pd.Series(pd.DatetimeIndex([]), dtype="datetime64[ns]")
    scored["score"] = scored["p_positive"].astype(float) - scored["p_negative"].astype(float)
    sent = sentiment_features(scored, assets, cal)

    # ---- 4) features for every date from d0 on (one array, sliced per prediction date below) ----
    with stage("building features"):
        full = pd.concat([add_features(g) for _, g in df.groupby("symbol")], ignore_index=True)
        full = full.merge(sent[["symbol", "date", "sent_3d", "news_n_3d"]], on=["symbol", "date"], how="left")
        full[["sent_3d", "news_n_3d"]] = full[["sent_3d", "news_n_3d"]].fillna(0)
        full = full.replace([np.inf, -np.inf], np.nan)

        T = D - d0
        di = dates.get_indexer(full["date"]) - d0
        ai = pd.Index(assets).get_indexer(full["symbol"])
        keep = (di >= 0) & (ai >= 0)
        raw = full.loc[keep, feats].to_numpy(dtype=np.float64)
        Xs = np.clip((raw - mean) / scale, -CLIP, CLIP)

        Xn = np.zeros((T, N, len(feats)), dtype=np.float32)
        present = np.zeros((T, N), dtype=bool)
        nanrow = np.zeros((T, N), dtype=bool)              # NaN features = not enough price history yet
        Xn[di[keep], ai[keep]] = np.nan_to_num(Xs, nan=0.0)   # warm-up NaN -> train mean, as in training
        present[di[keep], ai[keep]] = True
        nanrow[di[keep], ai[keep]] = np.isnan(Xs).any(axis=1)

        # Sentiment inputs per (date, asset): [sent_3d, log1p(news count)]
        Sd = np.zeros((T, N, 2), dtype=np.float32)
        si = dates[d0:].get_indexer(pd.DatetimeIndex(sent["date"]))
        sa = pd.Index(assets).get_indexer(sent["symbol"])
        m_ = (si >= 0) & (sa >= 0)
        Sd[si[m_], sa[m_], 0] = sent["sent_3d"].to_numpy()[m_]
        Sd[si[m_], sa[m_], 1] = np.log1p(sent["news_n_3d"].to_numpy()[m_])

        close = df.pivot(index="date", columns="symbol", values="close").reindex(index=dates, columns=assets)
        R = close.pct_change(fill_method=None).to_numpy()

    # ---- 5) + 6) one pass per distinct "newest bar" date: graph, fusion model, rows ----
    as_of = args.as_of or dt.date.today()
    rows = []
    with stage("running the fusion model"):
        for t in sorted(set(t_last[usable])):
            d, k = dates[t], t - d0
            group = np.where(usable & (t_last == t))[0]
            nan_window = nanrow[k - window + 1:k + 1].any(axis=0)
            skip("too little price history", np.isin(np.arange(N), group[nan_window[group]]))
            idx = group[~nan_window[group]]
            if len(idx) == 0:
                continue
            gaps = [int(assets[a]) for a in idx if not present[k - window + 1:k + 1, a].all()]
            if gaps:
                log.warning("assets with missing dates inside the %d-day window ending %s: %s",
                            window, d.date(), gaps)

            edge_index, edge_attr = correlation_graph(R, t, corr_win, corr_thr)
            log.info("%s: predicting %d assets | graph has %d undirected edges (of %d possible)",
                     d.date(), len(idx), edge_index.shape[1] // 2, N * (N - 1) // 2)
            xw = torch.from_numpy(np.ascontiguousarray(Xn[k - window + 1:k + 1][:, idx].transpose(1, 0, 2)))
            out = model(xw, torch.from_numpy(Sd[k][idx]), torch.from_numpy(Xn[k]), edge_index, edge_attr,
                        torch.as_tensor(idx))
            p_up = torch.sigmoid(out["logit"]).numpy()
            ret = out["ret"].numpy() * ret_std                                   # raw next-day return
            W = out["w"].numpy()                                                 # price, news, graph

            recent = scored[(scored["day"] > d - pd.Timedelta(days=NEWS_DAYS)) & (scored["day"] <= d)]
            counts = (recent.groupby(["asset_id", "sentiment_label"]).size().unstack(fill_value=0)
                            .reindex(columns=["positive", "negative", "neutral"], fill_value=0))
            for j, a in enumerate(idx):
                asset = int(assets[a])
                if not np.isfinite([p_up[j], ret[j], *W[j]]).all():
                    skipped.setdefault("non-finite model output", []).append(asset)
                    continue
                ser = close[asset].dropna()
                trend = float(100 * (ser.iloc[-1] / ser.iloc[-1 - TREND_DAYS] - 1)) if len(ser) > TREND_DAYS else None
                n_pos, n_neg, n_neu = counts.loc[asset].astype(int).tolist() if asset in counts.index else (0, 0, 0)
                direction = "up" if p_up[j] > 0.5 else "down"
                conf = float(max(p_up[j], 1 - p_up[j]))
                wts = {"price": float(W[j, 0]), "news": float(W[j, 1]), "graph": float(W[j, 2])}
                rows.append({
                    "asset_id": asset, "as_of": as_of, "direction": direction,
                    "expected_change_pct": float(100 * ret[j]), "confidence": conf,
                    "w_price": wts["price"], "w_news": wts["news"], "w_graph": wts["graph"],
                    "summary": build_summary(asset, trend, n_pos, n_neg, n_pos + n_neg + n_neu,
                                             direction, conf, wts, seed=f"{asset}|{as_of}",
                                             name=names.get(asset)),
                })
    for reason, ids in skipped.items():
        log.warning("skipped (%s): %s", reason, sorted(set(ids)))
    if not rows:
        sys.exit("no predictions produced")
    preds = pd.DataFrame(rows).sort_values("asset_id").reset_index(drop=True)

    # ---- 7) write ----
    if args.dry_run:
        log.info("dry run: nothing written to the database")
    else:
        with stage(f"writing {len(preds)} predictions to {PRED_TABLE}"):
            upsert_predictions(engine, preds)
    if args.csv:
        preds.to_csv(args.csv, index=False)
        log.info("wrote %s", args.csv)

    total = time.monotonic() - t_start
    log.info("newest price date %s | as_of %s | %d/%d assets predicted | total %.1fs",
             data_date.date(), as_of, len(preds), N, total)
    if total > 600:
        log.warning("run took longer than the 10-minute target")
    return preds


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true", help="don't write sentiment scores or predictions to the DB")
    p.add_argument("--csv", help="also write predictions to this CSV file")
    p.add_argument("--as-of", type=dt.date.fromisoformat, help="prediction date (default: today)")
    p.add_argument("--lookback-days", type=int, default=540,
                   help="calendar days of price history to load; indicators (EMA/RSI) need a long warm-up")
    p.add_argument("--score-budget-sec", type=float, default=float(os.environ.get("SCORE_BUDGET_SEC", 360)),
                   help="stop FinBERT scoring after this many seconds")
    p.add_argument("--max-stale-days", type=int, default=5,
                   help="skip an asset whose newest price bar is older than this many calendar days")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    pd.set_option("display.max_colwidth", None)
    preds = run(args)
    if args.dry_run:
        print(preds.round(4).to_string(index=False))


if __name__ == "__main__":
    main()
