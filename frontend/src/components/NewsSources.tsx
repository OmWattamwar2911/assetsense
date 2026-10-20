import { news } from '@/data/mockData'
import { Card, Eyebrow } from './ui'

export function NewsSources() {
  return (
    <Card className="sources-card">
      <div className="sources-header">
        <div><Eyebrow>SOURCES</Eyebrow><h2>What the market is reading</h2></div>
        <button className="text-button">VIEW ALL <span>→</span></button>
      </div>
      <div className="news-list">
        {news.map((item) => (
          <article className="news-row" key={item.headline}>
            <span className="news-source">{item.source}</span>
            <p>{item.headline}</p>
            <span className={`tone ${item.tone.toLowerCase()}`}>{item.tone}</span>
            <time className="num">{item.time}</time>
          </article>
        ))}
      </div>
    </Card>
  )
}
