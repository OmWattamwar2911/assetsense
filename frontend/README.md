# AssetSense Frontend

React 19 + Vite + Tailwind v4 + Recharts. Exported from Figma Make and restructured into components.

## Run
```
npm install
npm run dev      # http://localhost:5173
npm run build    # type-check + production build
```

## Structure
- `src/data/mockData.ts`  all sample data (swap for API calls in Phase 6)
- `src/types.ts`          shared types
- `src/index.css`         design tokens (CSS variables) + styles
- `src/components/`       TopBar, Ticker, SearchDropdown, Watchlist, AssetHeader,
                          PriceChart, ForecastCard, SummaryCard, SentimentGauge,
                          SignalBreakdown, NewsSources, skeletons, ui (shared)

Tip: type `error` in the search bar to preview the error state.
