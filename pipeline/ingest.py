#!/usr/bin/env python3
"""Market data ingestion: assets, daily prices and news into Supabase Postgres.

Usage:
    export DATABASE_URL='postgresql://user:pass@host:5432/postgres'
    python pipeline/ingest.py                         # full 3-year backfill
    python pipeline/ingest.py --since 2025-01-01      # incremental run
    python pipeline/ingest.py --only TCS.NS,bitcoin,120716 --verbose

Optional env:
    COINGECKO_API_KEY   CoinGecko demo key (sent as x-cg-demo-api-key). Not required; CoinGecko is
                        only used as a fallback when yfinance returns no crypto data.

All writes are upserts / ON CONFLICT DO NOTHING, so re-running is safe.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import random
import re
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Iterable, Iterator

import requests
import yfinance as yf
from sqlalchemy import (
    BigInteger,
    Column,
    Date,
    DateTime,
    Float,
    Integer,
    MetaData,
    Numeric,
    Table,
    Text,
    create_engine,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert

log = logging.getLogger("ingest")

HISTORY_DAYS = 3 * 365 + 1
COINGECKO_FREE_MAX_DAYS = 365
BATCH_SIZE = 1000

# Minimum seconds between requests, per upstream source.
MIN_INTERVAL = {"yfinance": 1.0, "coingecko": 6.0, "mfapi": 1.0, "gnews": 2.0}

# --------------------------------------------------------------------------
# Table definitions (Core, used only to build INSERT ... ON CONFLICT)
# --------------------------------------------------------------------------
md = MetaData()

assets_t = Table(
    "assets", md,
    Column("id", Integer, primary_key=True),
    Column("symbol", Text), Column("name", Text), Column("type", Text),
    Column("exchange", Text), Column("currency", Text),
)
prices_t = Table(
    "prices", md,
    Column("asset_id", Integer, primary_key=True),
    Column("date", Date, primary_key=True),
    Column("open", Numeric), Column("high", Numeric), Column("low", Numeric),
    Column("close", Numeric), Column("volume", Numeric),
)
news_t = Table(
    "news_items", md,
    Column("id", BigInteger, primary_key=True),
    Column("asset_id", Integer), Column("source", Text), Column("headline", Text),
    Column("url", Text), Column("published_at", DateTime(timezone=True)),
    Column("sentiment_label", Text), Column("sentiment_score", Float),
)

# --------------------------------------------------------------------------
# Asset universe
# --------------------------------------------------------------------------


@dataclass
class Asset:
    symbol: str
    name: str
    type: str
    exchange: str | None
    currency: str | None
    source: str  # yfinance | coingecko | mfapi
    news_query: str | None = None  # defaults to name
    id: int | None = None
    price_ticker: str | None = None  # yfinance ticker when it differs from `symbol`
    fallback: str | None = None  # source to try if `source` returns no price data


def build_assets() -> list[Asset]:
    out: list[Asset] = []
    stocks = [
        ("RELIANCE.NS", "Reliance Industries"),
        ("TCS.NS", "Tata Consultancy Services"),
        ("HDFCBANK.NS", "HDFC Bank"),
        ("INFY.NS", "Infosys"),
        ("ICICIBANK.NS", "ICICI Bank"),
        ("SBIN.NS", "State Bank of India"),
        ("ITC.NS", "ITC"),
        ("LT.NS", "Larsen & Toubro"),
        ("BHARTIARTL.NS", "Bharti Airtel"),
        ("HINDUNILVR.NS", "Hindustan Unilever"),
        ("AXISBANK.NS", "Axis Bank"),
        ("KOTAKBANK.NS", "Kotak Mahindra Bank"),
        ("MARUTI.NS", "Maruti Suzuki"),
        ("SUNPHARMA.NS", "Sun Pharmaceutical Industries"),
        ("NTPC.NS", "NTPC Limited"),
    ]
    for sym, name in stocks:
        q = "ITC Ltd" if sym == "ITC.NS" else None  # "ITC" alone is too noisy
        out.append(Asset(sym, name, "stock", "NSE", "INR", "yfinance", q))

    # symbol = CoinGecko id (kept as the stable key in `assets`); prices come from yfinance,
    # with CoinGecko (free plan: last 365 days only) as a fallback.
    for cid, name, q, ticker in [
        ("bitcoin", "Bitcoin", None, "BTC-USD"),
        ("ethereum", "Ethereum", None, "ETH-USD"),
        ("solana", "Solana", "Solana crypto", "SOL-USD"),
        ("ripple", "XRP", "XRP Ripple", "XRP-USD"),
    ]:
        out.append(Asset(cid, name, "crypto", None, "USD", "yfinance", q,
                         price_ticker=ticker, fallback="coingecko"))

    out.append(Asset("GC=F", "Gold", "commodity", "COMEX", "USD", "yfinance", "gold price"))
    out.append(Asset("SI=F", "Silver", "commodity", "COMEX", "USD", "yfinance", "silver price"))
    out.append(Asset("^NSEI", "Nifty 50", "index", "NSE", "INR", "yfinance"))

    # name is filled from mfapi meta.scheme_name at runtime
    for code in ("120716", "122639", "118778"):
        out.append(Asset(code, "", "fund", None, "INR", "mfapi"))
    return out


# --------------------------------------------------------------------------
# HTTP / retry / throttling helpers
# --------------------------------------------------------------------------


class PermanentError(Exception):
    """Non-retryable failure (e.g. HTTP 404)."""

    def __init__(self, msg: str, status: int | None = None):
        super().__init__(msg)
        self.status = status


class RetryableHTTP(Exception):
    def __init__(self, msg: str, retry_after: float | None = None):
        super().__init__(msg)
        self.retry_after = retry_after


class EmptyResult(Exception):
    """Upstream returned nothing; treated as retryable on full backfills."""


class Throttle:
    def __init__(self, intervals: dict[str, float]):
        self.intervals = intervals
        self._last: dict[str, float] = {}

    def wait(self, key: str) -> None:
        gap = self.intervals.get(key, 1.0)
        last = self._last.get(key)
        if last is not None:
            remaining = gap - (time.monotonic() - last)
            if remaining > 0:
                time.sleep(remaining + random.uniform(0, 0.3))
        self._last[key] = time.monotonic()


THROTTLE = Throttle(MIN_INTERVAL)
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0 (compatible; market-ingest/1.0)"})


def with_retries(
    fn: Callable[[], Any],
    *,
    what: str,
    source: str,
    attempts: int = 4,
    base_delay: float = 2.0,
    max_delay: float = 60.0,
) -> Any:
    """Call fn() with a polite delay before each attempt and exponential backoff."""
    for attempt in range(1, attempts + 1):
        THROTTLE.wait(source)
        try:
            return fn()
        except PermanentError:
            raise
        except Exception as exc:  # noqa: BLE001 - deliberately broad
            if attempt == attempts:
                raise
            delay = min(max_delay, base_delay * 2 ** (attempt - 1))
            if isinstance(exc, RetryableHTTP) and exc.retry_after:
                delay = max(delay, min(exc.retry_after, 120.0))
            delay += random.uniform(0, 1)
            log.warning("%s failed (attempt %d/%d): %s; retrying in %.1fs",
                        what, attempt, attempts, exc, delay)
            time.sleep(delay)
    raise RuntimeError("unreachable")


def http_get(
    url: str,
    *,
    source: str,
    what: str,
    params: dict | None = None,
    headers: dict | None = None,
    attempts: int = 4,
    base_delay: float = 2.0,
) -> requests.Response:
    def _do() -> requests.Response:
        r = SESSION.get(url, params=params, headers=headers, timeout=30)
        if r.status_code == 429 or r.status_code >= 500:
            ra = r.headers.get("Retry-After")
            raise RetryableHTTP(f"HTTP {r.status_code}",
                                float(ra) if ra and ra.isdigit() else None)
        if r.status_code >= 400:
            raise PermanentError(f"HTTP {r.status_code}: {r.text[:200]}", r.status_code)
        return r

    return with_retries(_do, what=what, source=source, attempts=attempts, base_delay=base_delay)


# --------------------------------------------------------------------------
# Value helpers
# --------------------------------------------------------------------------

PriceRow = tuple[date, Decimal | None, Decimal | None, Decimal | None, Decimal | None, Decimal | None]


def num(x: Any, ndigits: int) -> Decimal | None:
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return Decimal(str(round(f, ndigits)))


def chunks(seq: list, n: int) -> Iterator[list]:
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


# --------------------------------------------------------------------------
# Fetchers: prices
# --------------------------------------------------------------------------


def fetch_yfinance(symbol: str, start: date, strict_empty: bool) -> list[PriceRow]:
    def _do() -> list[PriceRow]:
        df = yf.Ticker(symbol).history(start=start.isoformat(), auto_adjust=False, actions=False)
        if df is None or df.empty:
            if strict_empty:
                raise EmptyResult("yfinance returned no rows")
            return []
        df = df.dropna(subset=["Close"])
        idx = df.index.tz_localize(None) if getattr(df.index, "tz", None) is not None else df.index
        rows: dict[date, PriceRow] = {}
        for ts, (_, r) in zip(idx, df.iterrows()):
            d = ts.date()
            rows[d] = (d, num(r.get("Open"), 4), num(r.get("High"), 4), num(r.get("Low"), 4),
                       num(r.get("Close"), 4), num(r.get("Volume"), 2))
        return sorted(rows.values(), key=lambda t: t[0])

    try:
        return with_retries(_do, what=f"yfinance {symbol}", source="yfinance", attempts=3)
    except EmptyResult:
        return []


def fetch_coingecko(coin_id: str, start: date) -> list[PriceRow]:
    url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
    headers = {}
    if os.environ.get("COINGECKO_API_KEY"):
        headers["x-cg-demo-api-key"] = os.environ["COINGECKO_API_KEY"]

    wanted_days = max((date.today() - start).days + 1, 2)
    days_to_try = [wanted_days]
    if wanted_days > COINGECKO_FREE_MAX_DAYS:
        days_to_try.append(COINGECKO_FREE_MAX_DAYS)

    payload = None
    for i, days in enumerate(days_to_try):
        try:
            r = http_get(
                url, source="coingecko", what=f"coingecko {coin_id} ({days}d)",
                params={"vs_currency": "usd", "days": days, "interval": "daily"},
                headers=headers, attempts=5, base_delay=10.0,
            )
            payload = r.json()
            if i > 0:
                log.warning("CoinGecko free API caps history at %d days; %s limited to that "
                            "(wanted %d)", COINGECKO_FREE_MAX_DAYS, coin_id, wanted_days)
            break
        except PermanentError as e:
            if i < len(days_to_try) - 1 and e.status in (400, 401, 403):
                log.info("CoinGecko rejected %d days for %s (%s); falling back to %d",
                         days, coin_id, e, COINGECKO_FREE_MAX_DAYS)
                continue
            raise
    assert payload is not None

    def by_day(points: list[list[float]]) -> dict[date, float]:
        out: dict[date, float] = {}
        for ts_ms, val in points or []:
            d = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).date()
            out[d] = val  # last point of a day wins
        return out

    closes = by_day(payload.get("prices", []))
    vols = by_day(payload.get("total_volumes", []))
    rows: list[PriceRow] = []
    for d in sorted(closes):
        if d < start:
            continue
        c = num(closes[d], 10)
        # Only close is available: O=H=L=C.
        rows.append((d, c, c, c, c, num(vols.get(d), 2)))
    return rows


_fund_described = False


def describe_payload(code: str, payload: dict) -> None:
    """--verbose: show the structure of the first mfapi response."""
    global _fund_described
    if _fund_described:
        return
    _fund_described = True
    data = payload.get("data") or []
    summary = {
        "scheme_code_requested": code,
        "top_level_keys": list(payload.keys()),
        "status": payload.get("status"),
        "meta": payload.get("meta"),
        "data_length": len(data),
        "data_first_3 (newest)": data[:3],
        "data_last_1 (oldest)": data[-1:],
        "data_entry_keys": list(data[0].keys()) if data else None,
    }
    print("=== mfapi.in first response structure ===")
    print(json.dumps(summary, indent=2, default=str))
    print("=========================================")


def fetch_fund_payload(code: str, verbose: bool) -> dict:
    r = http_get(f"https://api.mfapi.in/mf/{code}", source="mfapi", what=f"mfapi {code}")
    payload = r.json()
    if verbose:
        describe_payload(code, payload)
    meta = payload.get("meta") or {}
    if not meta.get("scheme_name"):
        raise ValueError("mfapi response has no meta.scheme_name")
    if not payload.get("data"):
        raise ValueError("mfapi response has no NAV data")
    return payload


def parse_fund_rows(payload: dict, start: date) -> list[PriceRow]:
    rows: dict[date, PriceRow] = {}
    for entry in payload.get("data", []):
        try:
            d = datetime.strptime(entry["date"], "%d-%m-%Y").date()
            nav = Decimal(str(entry["nav"]).strip())
        except (KeyError, ValueError, ArithmeticError):
            continue  # e.g. nav "N.A."
        if d < start:
            continue
        rows[d] = (d, nav, nav, nav, nav, None)
    return sorted(rows.values(), key=lambda t: t[0])


# --------------------------------------------------------------------------
# Fetcher: news
# --------------------------------------------------------------------------


_FUND_NOISE = re.compile(r"\b(?:Direct|Plan|Growth|Option)\b", re.IGNORECASE)


def news_query_for(a: Asset) -> str:
    """Google News query for an asset. Funds use a shortened scheme name, because the full
    mfapi name ("... Fund - Direct Plan - Growth Option") matches almost no headlines."""
    if a.news_query:
        return a.news_query
    if a.type != "fund":
        return a.name
    short = a.name.split(" - ", 1)[0]               # drop everything after the first " - "
    short = _FUND_NOISE.sub(" ", short)             # drop the words Direct / Plan / Growth / Option
    short = re.sub(r"\s+", " ", short).strip(" -")
    return short or a.name


def fetch_news(query: str, since: date | None) -> list[dict]:
    r = http_get(
        "https://news.google.com/rss/search", source="gnews", what=f"news '{query}'",
        params={"q": query, "hl": "en-IN", "gl": "IN", "ceid": "IN:en"},
    )
    root = ET.fromstring(r.content)
    items: dict[str, dict] = {}
    for item in root.iter("item"):
        url = (item.findtext("link") or "").strip()
        title = (item.findtext("title") or "").strip()
        if not url or not title:
            continue
        src_el = item.find("source")
        source = src_el.text.strip() if src_el is not None and src_el.text else None
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3].rstrip()
        published = None
        pub = item.findtext("pubDate")
        if pub:
            try:
                published = parsedate_to_datetime(pub)
                if published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                published = None
        if since and published and published.date() < since:
            continue
        items[url] = {"source": source, "headline": title, "url": url, "published_at": published}
    return list(items.values())


# --------------------------------------------------------------------------
# DB writes
# --------------------------------------------------------------------------


def make_engine():
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is not set")
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            url = "postgresql+psycopg2://" + url[len(prefix):]
            break
    return create_engine(url, pool_pre_ping=True)


def upsert_assets(conn, assets: list[Asset]) -> None:
    if not assets:
        return
    stmt = pg_insert(assets_t).values([
        {"symbol": a.symbol, "name": a.name, "type": a.type,
         "exchange": a.exchange, "currency": a.currency} for a in assets
    ])
    stmt = stmt.on_conflict_do_update(
        index_elements=["symbol", "type"],
        set_={"name": stmt.excluded.name, "exchange": stmt.excluded.exchange,
              "currency": stmt.excluded.currency},
    ).returning(assets_t.c.id, assets_t.c.symbol, assets_t.c.type)
    ids = {(r.symbol, r.type): r.id for r in conn.execute(stmt)}
    for a in assets:
        a.id = ids[(a.symbol, a.type)]


def upsert_prices(conn, asset_id: int, rows: list[PriceRow]) -> None:
    for part in chunks(rows, BATCH_SIZE):
        stmt = pg_insert(prices_t).values([
            {"asset_id": asset_id, "date": d, "open": o, "high": h, "low": l,
             "close": c, "volume": v} for d, o, h, l, c, v in part
        ])
        stmt = stmt.on_conflict_do_update(
            index_elements=["asset_id", "date"],
            set_={k: stmt.excluded[k] for k in ("open", "high", "low", "close", "volume")},
        )
        conn.execute(stmt)


def insert_news(conn, asset_id: int, items: list[dict]) -> int:
    inserted = 0
    for part in chunks(items, BATCH_SIZE):
        stmt = pg_insert(news_t).values([{**it, "asset_id": asset_id} for it in part])
        stmt = stmt.on_conflict_do_nothing(index_elements=["url"])
        inserted += max(conn.execute(stmt).rowcount, 0)
    return inserted


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


@dataclass
class Result:
    asset: Asset
    price_rows: int = 0
    news_fetched: int = 0
    news_new: int = 0
    errors: list[str] = field(default_factory=list)


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Ingest assets, prices and news into Postgres.")
    p.add_argument("--since", type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
                   help="YYYY-MM-DD; only ingest prices/news from this date on (default: last 3 years)")
    p.add_argument("--only", help="comma-separated symbols to process (e.g. TCS.NS,bitcoin,120716)")
    p.add_argument("--verbose", action="store_true", help="debug logging + print first mfapi response")
    return p.parse_args(argv)


def select_assets(all_assets: list[Asset], only: str | None) -> list[Asset]:
    if not only:
        return all_assets
    wanted = {s.strip().lower() for s in only.split(",") if s.strip()}
    chosen = [a for a in all_assets if a.symbol.lower() in wanted]
    unknown = wanted - {a.symbol.lower() for a in chosen}
    if unknown:
        log.warning("--only: unknown symbols ignored: %s", ", ".join(sorted(unknown)))
    return chosen


def fetch_prices(a: Asset, start: date, strict_empty: bool, fund_payloads: dict[str, dict]) -> list[PriceRow]:
    if a.source == "yfinance":
        ticker = a.price_ticker or a.symbol
        try:
            rows = fetch_yfinance(ticker, start, strict_empty)
        except Exception as exc:  # noqa: BLE001 - only swallowed when a fallback exists
            if not a.fallback:
                raise
            log.warning("%s: yfinance %s failed (%s)", a.symbol, ticker, exc)
            rows = []
        if not rows and a.fallback == "coingecko":
            log.warning("%s: yfinance %s returned no data; falling back to CoinGecko "
                        "(close-only, max %d days)", a.symbol, ticker, COINGECKO_FREE_MAX_DAYS)
            return fetch_coingecko(a.symbol, start)
        return rows
    if a.source == "coingecko":
        return fetch_coingecko(a.symbol, start)
    if a.source == "mfapi":
        return parse_fund_rows(fund_payloads[a.symbol], start)
    raise ValueError(f"unknown source {a.source}")


def run(args: argparse.Namespace) -> int:
    engine = make_engine()
    today = date.today()
    if args.since and args.since > today:
        sys.exit("--since is in the future")
    start = args.since or (today - timedelta(days=HISTORY_DAYS))
    strict_empty = args.since is None  # on incremental runs "no new rows" is normal

    selected = select_assets(build_assets(), args.only)
    if not selected:
        sys.exit("No assets selected")
    results: dict[str, Result] = {a.symbol: Result(a) for a in selected}
    log.info("Processing %d assets from %s", len(selected), start)

    # Step 1: resolve fund names from mfapi, then upsert assets.
    fund_payloads: dict[str, dict] = {}
    ready: list[Asset] = []
    for a in selected:
        if a.source == "mfapi":
            try:
                payload = fetch_fund_payload(a.symbol, args.verbose)
                a.name = payload["meta"]["scheme_name"].strip()
                fund_payloads[a.symbol] = payload
            except Exception as exc:  # noqa: BLE001
                log.error("Fund %s: could not fetch metadata: %s", a.symbol, exc)
                results[a.symbol].errors.append(f"fund metadata: {exc}")
                continue
        ready.append(a)

    with engine.begin() as conn:
        upsert_assets(conn, ready)
    log.info("Upserted %d assets", len(ready))

    # Steps 2-4: prices + news per asset, isolated so one failure never stops the rest.
    for a in ready:
        res = results[a.symbol]
        log.info("--- %s (%s) ---", a.symbol, a.type)

        try:
            rows = fetch_prices(a, start, strict_empty, fund_payloads)
            if rows:
                with engine.begin() as conn:
                    upsert_prices(conn, a.id, rows)
                res.price_rows = len(rows)
                log.info("%s: upserted %d price rows (%s .. %s)", a.symbol, len(rows),
                         rows[0][0], rows[-1][0])
                if a.type == "fund":
                    log.info("%s: latest NAV %s on %s", a.symbol, rows[-1][4], rows[-1][0])
            else:
                log.warning("%s: no price data returned", a.symbol)
        except Exception as exc:  # noqa: BLE001
            log.error("%s: price ingestion failed: %s", a.symbol, exc,
                      exc_info=args.verbose)
            res.errors.append(f"prices: {exc}")

        try:
            items = fetch_news(news_query_for(a), args.since)
            res.news_fetched = len(items)
            if items:
                with engine.begin() as conn:
                    res.news_new = insert_news(conn, a.id, items)
            log.info("%s: news %d fetched, %d new", a.symbol, len(items), res.news_new)
        except Exception as exc:  # noqa: BLE001
            log.error("%s: news ingestion failed: %s", a.symbol, exc, exc_info=args.verbose)
            res.errors.append(f"news: {exc}")

    return print_summary(list(results.values()))


def print_summary(results: list[Result]) -> int:
    print("\n=== Summary ===")
    print(f"{'symbol':<16}{'type':<11}{'price rows':>11}{'news new':>10}{'news seen':>10}  status")
    for r in results:
        status = "ERROR" if r.errors else ("no data" if r.price_rows == 0 else "ok")
        print(f"{r.asset.symbol:<16}{r.asset.type:<11}{r.price_rows:>11}{r.news_new:>10}"
              f"{r.news_fetched:>10}  {status}")

    no_prices = [r.asset.symbol for r in results if r.price_rows == 0]
    no_news = [r.asset.symbol for r in results if r.news_fetched == 0]
    failed = [r for r in results if r.errors]
    if no_prices:
        print("\nAssets with NO price data returned: " + ", ".join(no_prices))
    if no_news:
        print("Assets with NO news returned: " + ", ".join(no_news))
    if failed:
        print("\nErrors:")
        for r in failed:
            for e in r.errors:
                print(f"  {r.asset.symbol}: {e}")
    if not (no_prices or no_news or failed):
        print("\nAll assets returned data.")
    return 2 if failed else 0


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
    )
    if not args.verbose:
        logging.getLogger("yfinance").setLevel(logging.CRITICAL)
        logging.getLogger("urllib3").setLevel(logging.WARNING)
    sys.exit(run(args))


if __name__ == "__main__":
    main()
