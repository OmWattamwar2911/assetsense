import type { CSSProperties } from 'react'
import { signals } from '@/data/mockData'
import { Card, Eyebrow } from './ui'

export function SignalBreakdown() {
  const summary = signals.map((s) => `${s.label.toLowerCase()} ${s.value}%`).join(', ')
  return (
    <Card className="signals-card">
      <div className="card-heading"><Eyebrow>SIGNAL BREAKDOWN</Eyebrow><span className="updated">CONTRIBUTION</span></div>
      <div className="stacked-bar" aria-label={`Signal contribution: ${summary}`}>
        {signals.map((s) => <i key={s.key} style={{ '--w': `${s.value}%` } as CSSProperties} />)}
      </div>
      <div className="signal-labels">
        {signals.map((s) => (
          <div key={s.key}>
            <span className={`signal-dot ${s.key}`} /><span>{s.label}</span><b className="num">{s.value}%</b>
          </div>
        ))}
      </div>
    </Card>
  )
}
