import type { WatchAsset } from '@/types'
import { Eyebrow } from './ui'

function MiniSparkline({ asset }: { asset: WatchAsset }) {
  return (
    <svg className={`sparkline ${asset.change < 0 ? 'down' : ''}`} viewBox="0 0 70 24" aria-hidden="true">
      <polyline points={asset.points} />
    </svg>
  )
}

type Props = {
  assets: WatchAsset[]
  selected: string
  onSelect: (symbol: string) => void
}

export function Watchlist({ assets, selected, onSelect }: Props) {
  return (
    <aside className="watch-rail">
      <div className="rail-heading">
        <Eyebrow>WATCHLIST</Eyebrow>
        <button className="icon-button" aria-label="Add asset">+</button>
      </div>
      <div className="watch-items">
        {assets.map((asset) => (
          <button
            className={`watch-row ${selected === asset.symbol ? 'active' : ''}`}
            onClick={() => onSelect(asset.symbol)}
            key={asset.symbol}
          >
            <span className="watch-name"><b>{asset.symbol}</b><small>{asset.name}</small></span>
            <MiniSparkline asset={asset} />
            <span className="watch-value">
              <b className="num">{asset.price}</b>
              <small className={`num ${asset.change < 0 ? 'negative' : 'positive'}`}>
                {asset.change > 0 ? '+' : ''}{asset.change.toFixed(2)}%
              </small>
            </span>
          </button>
        ))}
      </div>
      <div className="rail-foot">
        <span>{assets.length} of 20 assets</span>
        <span className="num">USD</span>
      </div>
    </aside>
  )
}
