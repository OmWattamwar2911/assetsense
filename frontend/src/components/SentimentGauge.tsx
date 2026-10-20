import type { CSSProperties } from 'react'
import { sentiment } from '@/data/mockData'
import { Card, Eyebrow } from './ui'

export function SentimentGauge() {
  return (
    <Card className="sentiment-card">
      <div className="card-heading"><Eyebrow>MARKET SENTIMENT</Eyebrow><span className="updated">{sentiment.updated}</span></div>
      <div className="gauge">
        <div className="gauge-track"><i style={{ '--value': `${sentiment.score}%` } as CSSProperties} /></div>
        <div className="gauge-labels"><span>NEGATIVE</span><b>POSITIVE</b></div>
      </div>
      <div className="sentiment-score">
        <strong className="num">{sentiment.score}</strong><span>/ 100</span><b>{sentiment.label}</b>
      </div>
    </Card>
  )
}
