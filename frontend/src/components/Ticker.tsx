import type { TickerItem } from '@/types'

export function Ticker({ items }: { items: TickerItem[] }) {
  return (
    <div className="ticker-strip">
      <div className="ticker-track">
        {[...items, ...items].map(([name, price, change], index) => (
          <div className="ticker-item" key={`${name}-${index}`}>
            <span>{name}</span>
            <b className="num">{price}</b>
            <em className={`num ${change.startsWith('−') ? 'negative' : 'positive'}`}>{change}</em>
          </div>
        ))}
      </div>
    </div>
  )
}
