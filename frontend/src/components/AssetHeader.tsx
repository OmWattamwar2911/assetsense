import { assetProfile as a } from '@/data/mockData'

export function AssetHeader() {
  return (
    <section className="asset-overview">
      <div className="asset-title">
        <div className="asset-monogram">{a.monogram}</div>
        <div>
          <div className="asset-name-row">
            <h1>{a.name}</h1>
            <span className="badge">{a.badge}</span>
          </div>
          <div className="asset-meta">{a.symbol} <i /> {a.sector} <i /> {a.country}</div>
        </div>
      </div>
      <div className="asset-quote">
        <div className="quote-price num">{a.priceLabel}</div>
        <div className="quote-change num positive">{a.changeLabel} <span>today</span></div>
      </div>
    </section>
  )
}
