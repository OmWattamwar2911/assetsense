import { summaryText } from '@/data/mockData'
import { Card, Eyebrow } from './ui'

export function SummaryCard() {
  return (
    <Card className="summary-card">
      <Eyebrow>IN SHORT</Eyebrow>
      <p>{summaryText}</p>
    </Card>
  )
}
