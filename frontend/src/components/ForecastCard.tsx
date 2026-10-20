import type { CSSProperties } from 'react'
import { forecast } from '@/data/mockData'
import { ArrowUpRight, Card, Eyebrow } from './ui'

export function ForecastCard() {
  const up = forecast.expectedChange >= 0
  return (
    <Card className="forecast-card">
      <Eyebrow>{forecast.horizonLabel}</Eyebrow>
      <div className="forecast-content">
        <div className="direction-icon"><ArrowUpRight /></div>
        <div>
          <span>EXPECTED CHANGE</span>
          <strong className={`num ${up ? 'positive' : 'negative'}`}>
            {up ? '+' : ''}{forecast.expectedChange.toFixed(2)}%
          </strong>
        </div>
      </div>
      <div className="confidence-row">
        <span>Confidence</span><b className="num">{forecast.confidence}%</b>
      </div>
      <div className="confidence-track"><i style={{ '--value': `${forecast.confidence}%` } as CSSProperties} /></div>
    </Card>
  )
}
