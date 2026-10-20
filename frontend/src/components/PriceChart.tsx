import {
  Area,
  ComposedChart,
  Line,
  ReferenceArea,
  ReferenceDot,
  ReferenceLine,
  ResponsiveContainer,
  XAxis,
  YAxis,
} from 'recharts'
import { assetProfile, buildChartRows, forecast, TODAY_X, xLabels, Y_MAX, Y_MIN, Y_TICKS } from '@/data/mockData'
import type { Timeframe } from '@/types'
import { ChartSkeleton } from './skeletons'

const TIMEFRAMES: Timeframe[] = ['1W', '1M', '3M', '1Y']

type Props = {
  timeframe: Timeframe
  loading: boolean
  onTimeframeChange: (t: Timeframe) => void
}

function Chart({ timeframe }: { timeframe: Timeframe }) {
  const rows = buildChartRows(timeframe)

  return (
    <div className="chart-wrap">
      <div className="chart-y-labels num" aria-hidden="true">
        {Y_TICKS.map((t) => <span key={t}>${t}</span>)}
      </div>

      {/* Hidden SVG that only hosts the hatch pattern used by the forecast region. */}
      <svg className="hatch-defs" aria-hidden="true">
        <defs>
          <pattern id="forecastHatch" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(35)">
            <line x1="0" y1="0" x2="0" y2="8" stroke="var(--gold)" strokeWidth="1" />
          </pattern>
        </defs>
      </svg>

      <div className="price-chart" role="img" aria-label={`${assetProfile.name} ${timeframe} historical and forecast price chart`}>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={rows} margin={{ top: 18, right: 0, bottom: 2, left: 0 }}>
            <XAxis dataKey="x" type="number" domain={[0, 100]} hide />
            <YAxis domain={[Y_MIN, Y_MAX]} type="number" hide allowDataOverflow />

            {Y_TICKS.map((t) => (
              <ReferenceLine key={t} y={t} stroke="var(--line)" strokeWidth={1} />
            ))}

            <ReferenceArea x1={TODAY_X} x2={100} y1={Y_MIN} y2={Y_MAX} fill="var(--gold-soft)" fillOpacity={0.33} stroke="none" />
            <ReferenceArea x1={TODAY_X} x2={100} y1={Y_MIN} y2={Y_MAX} fill="url(#forecastHatch)" fillOpacity={0.2} stroke="none" />
            <ReferenceLine x={TODAY_X} stroke="var(--gold)" strokeWidth={1} strokeDasharray="3 5" />

            <Area dataKey="band" type="monotone" stroke="none" fill="var(--gold-soft)" fillOpacity={0.6} isAnimationActive={false} />
            <Line dataKey="price" type="monotone" stroke="var(--ink)" strokeWidth={1.5} dot={false} activeDot={false} isAnimationActive={false} />
            <Line dataKey="forecast" type="monotone" stroke="var(--gold)" strokeWidth={1.5} strokeDasharray="5 5" dot={false} activeDot={false} isAnimationActive={false} />

            <ReferenceDot x={TODAY_X} y={assetProfile.price} r={7} fill="var(--surface)" stroke="var(--gold)" strokeWidth={1} />
            <ReferenceDot x={TODAY_X} y={assetProfile.price} r={3.5} fill="var(--gold)" stroke="none" />
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      <div className="today-label num">TODAY</div>
      <div className="forecast-label">FORECAST</div>
      <div className="target-label num"><span>30D TARGET</span>${forecast.target.toFixed(2)}</div>
      <div className="chart-x-labels num" aria-hidden="true">
        {xLabels.map((l) => <span key={l}>{l}</span>)}
      </div>
    </div>
  )
}

export function PriceChart({ timeframe, loading, onTimeframeChange }: Props) {
  return (
    <section className="chart-section">
      <div className="chart-toolbar">
        <div className="chart-legend">
          <span><i className="legend-solid" />HISTORICAL</span>
          <span><i className="legend-dash" />FORECAST</span>
        </div>
        <div className="time-tabs" aria-label="Chart timeframe">
          {TIMEFRAMES.map((item) => (
            <button className={timeframe === item ? 'active' : ''} onClick={() => onTimeframeChange(item)} key={item}>
              {item}
            </button>
          ))}
        </div>
      </div>
      {loading ? <ChartSkeleton /> : <Chart timeframe={timeframe} />}
    </section>
  )
}
