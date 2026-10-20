import { Card } from './ui'

export function SearchSkeleton() {
  return (
    <div className="search-skeleton" aria-label="Loading search results">
      {[1, 2, 3].map((item) => (
        <div className="search-skeleton-row" key={item}>
          <i className="skeleton-block skeleton-circle" />
          <span>
            <i className="skeleton-block" />
            <i className="skeleton-block short" />
          </span>
          <i className="skeleton-block badge-block" />
        </div>
      ))}
    </div>
  )
}

export function ChartSkeleton() {
  return (
    <div className="chart-skeleton" aria-label="Loading price chart">
      <div className="skeleton-chart-grid">
        <i /><i /><i /><i />
        <svg viewBox="0 0 800 220" preserveAspectRatio="none" aria-hidden="true">
          <path d="M0 190 C70 174 94 185 145 151 S236 164 292 121 S386 142 447 91 S540 108 599 63 S710 72 800 32" />
        </svg>
      </div>
    </div>
  )
}

export function CardsSkeleton() {
  return (
    <div className="insight-grid skeleton-grid" aria-label="Loading market insights">
      {[4, 8, 6, 6].map((span, index) => (
        <Card className={`skeleton-card span-${span}`} key={`${span}-${index}`}>
          <i className="skeleton-block skeleton-label" />
          <i className="skeleton-block skeleton-title" />
          <i className="skeleton-block" />
          <i className="skeleton-block medium" />
        </Card>
      ))}
      <Card className="skeleton-card span-12">
        <i className="skeleton-block skeleton-label" />
        <i className="skeleton-block skeleton-title" />
        <i className="skeleton-block" />
        <i className="skeleton-block" />
        <i className="skeleton-block medium" />
      </Card>
    </div>
  )
}
