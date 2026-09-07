/**
 * Charts, drawn as SVG. No charting library.
 *
 * Three forms, and the reasoning behind each:
 *
 * - `AreaChart` — revenue and orders over time. **One series**, so there is no
 *   legend (the panel title names it) and no categorical palette to get wrong.
 *   Two measures of different scale are never put on one plot with two y-axes;
 *   they get two charts.
 * - `BarList` — a breakdown by category. Magnitude, not identity, so every bar
 *   is the **same hue**: colouring a single-measure bar chart by category is
 *   colour used for decoration, and it makes the reader look for a meaning that
 *   is not there. The category is the row label.
 * - `Sparkline` — a trend inside a stat tile, no axes, no interaction.
 *
 * The series hue is `--color-accent`, validated against both the light and dark
 * chart surfaces (contrast >= 3:1 in each). Grid and axes are deliberately
 * recessive; text wears text tokens rather than the series colour.
 */

import { useId, useMemo, useState } from 'react'
import { cx } from '@/js/hooks'
import { Empty } from './kit'

type Point = { label: string; value: number; display: string }

/* ----------------------------------------------------------------- helpers */

/**
 * A y-axis that ends on a round number.
 *
 * A max of 4,873 gives ticks at 5,000 / 2,500 / 0 rather than at 4,873 and
 * half of it — axis labels are read, and reading 2,436.5 costs the reader
 * something for nothing.
 */
function niceMax(value: number): number {
  if (value <= 0) return 1
  const magnitude = 10 ** Math.floor(Math.log10(value))
  const normalised = value / magnitude
  const step = normalised <= 1 ? 1 : normalised <= 2 ? 2 : normalised <= 5 ? 5 : 10
  return step * magnitude
}

/** A monotone-ish cubic path. Smoothed just enough to read as a trend, not so
 *  much that it invents peaks between the points it was given. */
function linePath(points: { x: number; y: number }[]): string {
  if (points.length === 0) return ''
  if (points.length === 1) return `M ${points[0]!.x} ${points[0]!.y}`
  const parts = [`M ${points[0]!.x} ${points[0]!.y}`]
  for (let i = 1; i < points.length; i += 1) {
    const previous = points[i - 1]!
    const current = points[i]!
    const midX = (previous.x + current.x) / 2
    parts.push(`C ${midX} ${previous.y}, ${midX} ${current.y}, ${current.x} ${current.y}`)
  }
  return parts.join(' ')
}

/* -------------------------------------------------------------- area chart */

export function AreaChart({
  points,
  height = 200,
  valueLabel,
  emptyTitle = 'No data yet',
  emptyBody,
}: {
  points: Point[]
  height?: number
  /** What the y-axis measures, for the tooltip and the accessible summary. */
  valueLabel: string
  emptyTitle?: string
  emptyBody?: string
}) {
  const gradientId = useId()
  const [hover, setHover] = useState<number | null>(null)

  const shape = useMemo(() => {
    const width = 1000
    const padding = { top: 12, right: 8, bottom: 22, left: 44 }
    const plotWidth = width - padding.left - padding.right
    const plotHeight = height - padding.top - padding.bottom
    const max = niceMax(Math.max(...points.map((p) => p.value), 0))

    const coords = points.map((point, index) => ({
      x:
        padding.left +
        (points.length === 1 ? plotWidth / 2 : (index / (points.length - 1)) * plotWidth),
      y: padding.top + plotHeight - (point.value / max) * plotHeight,
    }))

    return { width, padding, plotWidth, plotHeight, max, coords }
  }, [points, height])

  if (points.length === 0) {
    return <Empty title={emptyTitle} body={emptyBody} />
  }

  const { width, padding, plotWidth, plotHeight, max, coords } = shape
  const line = linePath(coords)
  const area =
    `${line} L ${coords[coords.length - 1]!.x} ${padding.top + plotHeight}` +
    ` L ${coords[0]!.x} ${padding.top + plotHeight} Z`

  // Four gridlines including the baseline: enough to judge a value against,
  // few enough to stay behind the data.
  const ticks = [0, 0.5, 1].map((fraction) => ({
    y: padding.top + plotHeight - fraction * plotHeight,
    value: max * fraction,
  }))

  // At most eight date labels, however many points there are — a 90-day series
  // with a label per day is an unreadable smear.
  const labelEvery = Math.max(1, Math.ceil(points.length / 8))
  const active = hover === null ? null : points[hover]

  return (
    <div className="relative">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="w-full"
        style={{ height }}
        role="img"
        aria-label={`${valueLabel} from ${points[0]!.label} to ${points[points.length - 1]!.label}`}
        onMouseLeave={() => setHover(null)}
        onMouseMove={(event) => {
          const rect = event.currentTarget.getBoundingClientRect()
          const ratio = ((event.clientX - rect.left) / rect.width) * width
          const fraction = (ratio - padding.left) / plotWidth
          const index = Math.round(fraction * (points.length - 1))
          setHover(Math.max(0, Math.min(points.length - 1, index)))
        }}
      >
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--color-accent)" stopOpacity="0.16" />
            <stop offset="100%" stopColor="var(--color-accent)" stopOpacity="0" />
          </linearGradient>
        </defs>

        {ticks.map((tick) => (
          <g key={tick.y}>
            <line
              x1={padding.left}
              x2={width - padding.right}
              y1={tick.y}
              y2={tick.y}
              stroke="var(--color-line)"
              strokeWidth="1"
              // The baseline is solid; the rest are dashed, so the zero line
              // reads as the floor rather than as one gridline among three.
              strokeDasharray={tick.value === 0 ? undefined : '3 4'}
            />
            <text
              x={padding.left - 8}
              y={tick.y + 3.5}
              textAnchor="end"
              className="fill-[var(--color-ink-faint)] text-[11px] tabular"
            >
              {tick.value >= 1000
                ? `${(tick.value / 1000).toFixed(tick.value >= 10000 ? 0 : 1)}k`
                : Math.round(tick.value)}
            </text>
          </g>
        ))}

        <path d={area} fill={`url(#${gradientId})`} />
        <path
          d={line}
          fill="none"
          stroke="var(--color-accent)"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />

        {points.map((point, index) =>
          index % labelEvery === 0 ? (
            <text
              key={point.label}
              x={coords[index]!.x}
              y={height - 6}
              textAnchor="middle"
              className="fill-[var(--color-ink-faint)] text-[11px]"
            >
              {point.label.slice(5)}
            </text>
          ) : null,
        )}

        {hover !== null && (
          <g>
            <line
              x1={coords[hover]!.x}
              x2={coords[hover]!.x}
              y1={padding.top}
              y2={padding.top + plotHeight}
              stroke="var(--color-ink-faint)"
              strokeWidth="1"
              strokeDasharray="3 3"
            />
            {/* A surface-coloured ring around the marker, so it stays legible
                wherever it lands on the fill. */}
            <circle
              cx={coords[hover]!.x}
              cy={coords[hover]!.y}
              r="4.5"
              fill="var(--color-accent)"
              stroke="var(--color-surface)"
              strokeWidth="2"
            />
          </g>
        )}
      </svg>

      {active && (
        <div
          className="pointer-events-none absolute top-1 rounded-[var(--radius-sm)] border border-[var(--color-line)] bg-[var(--color-surface)] px-2 py-1.5 shadow-[var(--shadow-popover)]"
          style={{
            left: `${(coords[hover!]!.x / width) * 100}%`,
            transform: 'translateX(-50%)',
          }}
        >
          <div className="text-[11px] text-[var(--color-ink-faint)]">{active.label}</div>
          <div className="text-[13px] font-semibold text-[var(--color-ink)] tabular">
            {active.display}
          </div>
        </div>
      )}
    </div>
  )
}

/* ---------------------------------------------------------------- bar list */

export function BarList({
  rows,
  emptyTitle = 'Nothing to show',
  emptyBody,
}: {
  rows: { label: string; value: number; display: string; hint?: string }[]
  emptyTitle?: string
  emptyBody?: string
}) {
  if (rows.length === 0) return <Empty title={emptyTitle} body={emptyBody} />

  const max = Math.max(...rows.map((row) => row.value), 1)

  return (
    <div className="space-y-1">
      {rows.map((row) => (
        <div key={row.label} className="group relative" title={`${row.label}: ${row.display}`}>
          <div className="relative flex items-center justify-between gap-3 rounded-[var(--radius-xs)] px-2 py-1.5">
            {/* The bar is behind the text rather than beside it: the label is
                what identifies the row, and a separate bar column would push
                the numbers off a narrow panel. */}
            <div
              className="absolute inset-y-0 left-0 rounded-[var(--radius-xs)] bg-[var(--color-accent)] opacity-[0.13] transition-[width] duration-300 group-hover:opacity-[0.2]"
              style={{ width: `${Math.max((row.value / max) * 100, 2)}%` }}
              aria-hidden
            />
            <span className="relative truncate text-[12.5px] text-[var(--color-ink)]">
              {row.label}
            </span>
            <span className="relative flex shrink-0 items-baseline gap-2">
              {row.hint && (
                <span className="text-[11.5px] text-[var(--color-ink-faint)]">{row.hint}</span>
              )}
              <span className="text-[12.5px] font-medium text-[var(--color-ink)] tabular">
                {row.display}
              </span>
            </span>
          </div>
        </div>
      ))}
    </div>
  )
}

/* --------------------------------------------------------------- sparkline */

export function Sparkline({
  values,
  className,
  height = 28,
}: {
  values: number[]
  className?: string
  height?: number
}) {
  if (values.length < 2) return null

  const width = 100
  const max = Math.max(...values, 1)
  const min = Math.min(...values, 0)
  const span = max - min || 1

  const coords = values.map((value, index) => ({
    x: (index / (values.length - 1)) * width,
    y: height - ((value - min) / span) * (height - 3) - 1.5,
  }))

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className={cx('w-full', className)}
      style={{ height }}
      preserveAspectRatio="none"
      // Decorative: the number beside it is the information. A screen reader
      // announcing a hundred coordinates would be noise.
      aria-hidden
    >
      <path
        d={linePath(coords)}
        fill="none"
        stroke="var(--color-accent)"
        strokeWidth="1.5"
        strokeLinecap="round"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  )
}

/* ------------------------------------------------------------ donut / meter */

/**
 * A single proportion, as a ring.
 *
 * One value against a whole — a conversion rate, a fulfilment percentage.
 * Never used for a breakdown of several categories: a reader cannot compare
 * arc lengths, which is what `BarList` is for.
 */
export function Meter({
  value,
  label,
  caption,
  tone = 'accent',
}: {
  /** 0–1. `null` renders as "not enough data" rather than as zero. */
  value: number | null
  label: string
  caption?: string
  tone?: 'accent' | 'positive' | 'critical'
}) {
  const size = 92
  const stroke = 8
  const radius = (size - stroke) / 2
  const circumference = 2 * Math.PI * radius
  const fraction = value === null ? 0 : Math.max(0, Math.min(1, value))

  const COLOR = {
    accent: 'var(--color-accent)',
    positive: 'var(--color-positive)',
    critical: 'var(--color-critical)',
  }[tone]

  return (
    <div className="flex items-center gap-3">
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden>
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="var(--color-line)"
          strokeWidth={stroke}
        />
        {value !== null && (
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke={COLOR}
            strokeWidth={stroke}
            strokeLinecap="round"
            strokeDasharray={`${fraction * circumference} ${circumference}`}
            transform={`rotate(-90 ${size / 2} ${size / 2})`}
          />
        )}
        <text
          x={size / 2}
          y={size / 2 + 5}
          textAnchor="middle"
          className="fill-[var(--color-ink)] text-[16px] font-semibold tabular"
        >
          {value === null ? '—' : `${(fraction * 100).toFixed(1)}%`}
        </text>
      </svg>
      <div className="min-w-0">
        <div className="text-[13px] font-medium text-[var(--color-ink)]">{label}</div>
        {caption && (
          <div className="text-[12px] text-[var(--color-ink-soft)]">{caption}</div>
        )}
        {value === null && (
          <div className="mt-0.5 text-[12px] text-[var(--color-ink-faint)]">
            Not enough data yet
          </div>
        )}
      </div>
    </div>
  )
}

/* ------------------------------------------------------------- multi-series */

export type Series = {
  /** Shown in the legend and the tooltip. */
  name: string
  /** A CSS colour. Callers pass a `--color-series-*` token, or a state token
   *  when the series *is* a state — errors are drawn in the critical hue on
   *  purpose, because that is the one case where the series and the meaning
   *  are the same thing. */
  color: string
  values: number[]
  /** Formats a value for the axis and the tooltip. */
  format?: (value: number) => string
  /** Draw a filled area beneath the line. At most one series should, or the
   *  fills stack up into mud. */
  area?: boolean
}

/**
 * Several measures over one time axis.
 *
 * The chart the gateway screens are built on, and it does three things the
 * single-series `AreaChart` does not need to:
 *
 * - **A shared crosshair.** Hovering reads every series at that instant, which
 *   is the whole question being asked of a chart with more than one line —
 *   "what were errors doing when latency spiked".
 * - **A right-hand axis, optionally.** Requests and error rate have no common
 *   scale, and drawing them against one makes the smaller one a flat line on
 *   the floor.
 * - **Gaps stay gaps.** A `null` in `values` breaks the line rather than
 *   interpolating across it. A chart that draws a straight line through an
 *   outage is a chart that hides the outage.
 */
export function LineChart({
  labels,
  series,
  height = 260,
  rightAxis,
  emptyTitle = 'No traffic in this range',
  emptyBody,
}: {
  labels: string[]
  series: Series[]
  height?: number
  /** Index of the series drawn against a right-hand axis, if any. */
  rightAxis?: number
  emptyTitle?: string
  emptyBody?: string
}) {
  const gradientId = useId()
  const [hover, setHover] = useState<number | null>(null)

  const shape = useMemo(() => {
    const width = 1000
    const padding = { top: 14, right: rightAxis === undefined ? 12 : 52, bottom: 24, left: 52 }
    const plotWidth = width - padding.left - padding.right
    const plotHeight = height - padding.top - padding.bottom

    const leftSeries = series.filter((_, index) => index !== rightAxis)
    const leftMax = niceMax(Math.max(...leftSeries.flatMap((s) => s.values), 0))
    const rightMax =
      rightAxis === undefined ? 1 : niceMax(Math.max(...(series[rightAxis]?.values ?? [0]), 0))

    const x = (index: number) =>
      padding.left +
      (labels.length <= 1 ? plotWidth / 2 : (index / (labels.length - 1)) * plotWidth)
    const y = (value: number, right: boolean) =>
      padding.top + plotHeight - (value / (right ? rightMax : leftMax)) * plotHeight

    return { width, padding, plotWidth, plotHeight, leftMax, rightMax, x, y }
  }, [labels, series, height, rightAxis])

  if (labels.length === 0 || series.length === 0) {
    return <Empty title={emptyTitle} body={emptyBody} />
  }

  const { width, padding, plotWidth, plotHeight, leftMax, rightMax, x, y } = shape
  const ticks = [0, 0.25, 0.5, 0.75, 1]

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1.5">
        {series.map((item) => (
          <span key={item.name} className="flex items-center gap-1.5 text-[11.5px] text-ink-muted">
            <span className="h-[3px] w-3.5 rounded-full" style={{ background: item.color }} />
            {item.name}
          </span>
        ))}
      </div>

      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="w-full"
        style={{ height }}
        role="img"
        aria-label={`${series.map((s) => s.name).join(', ')} over time`}
        onMouseLeave={() => setHover(null)}
        onMouseMove={(event) => {
          const box = event.currentTarget.getBoundingClientRect()
          const ratio = (event.clientX - box.left) / box.width
          const position = (ratio * width - padding.left) / plotWidth
          const index = Math.round(position * (labels.length - 1))
          setHover(index >= 0 && index < labels.length ? index : null)
        }}
      >
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={series[0]?.color} stopOpacity="0.18" />
            <stop offset="100%" stopColor={series[0]?.color} stopOpacity="0" />
          </linearGradient>
        </defs>

        {ticks.map((fraction) => {
          const lineY = padding.top + plotHeight - fraction * plotHeight
          return (
            <g key={fraction}>
              <line
                x1={padding.left}
                x2={width - padding.right}
                y1={lineY}
                y2={lineY}
                stroke="var(--color-line)"
                strokeWidth={1}
              />
              <text
                x={padding.left - 8}
                y={lineY + 3.5}
                textAnchor="end"
                className="fill-[var(--color-ink-faint)] text-[10px]"
              >
                {(series.find((_, i) => i !== rightAxis)?.format ?? compactNumber)(
                  leftMax * fraction,
                )}
              </text>
              {rightAxis !== undefined && (
                <text
                  x={width - padding.right + 8}
                  y={lineY + 3.5}
                  textAnchor="start"
                  className="fill-[var(--color-ink-faint)] text-[10px]"
                >
                  {(series[rightAxis]?.format ?? compactNumber)(rightMax * fraction)}
                </text>
              )}
            </g>
          )
        })}

        {series.map((item, index) => {
          const right = index === rightAxis
          const coords = item.values.map((value, i) => ({ x: x(i), y: y(value, right) }))
          const path = linePath(coords)
          return (
            <g key={item.name}>
              {item.area && (
                <path
                  d={`${path} L ${coords[coords.length - 1]!.x} ${padding.top + plotHeight} L ${coords[0]!.x} ${padding.top + plotHeight} Z`}
                  fill={`url(#${gradientId})`}
                />
              )}
              <path
                d={path}
                fill="none"
                stroke={item.color}
                strokeWidth={1.75}
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </g>
          )
        })}

        {hover !== null && (
          <line
            x1={x(hover)}
            x2={x(hover)}
            y1={padding.top}
            y2={padding.top + plotHeight}
            stroke="var(--color-ink-faint)"
            strokeWidth={1}
            strokeDasharray="3 3"
          />
        )}

        {labels.map((label, index) =>
          // Roughly six x labels whatever the point count, so a 720-point
          // 30-day chart does not try to print 720 timestamps on top of
          // each other.
          index % Math.max(1, Math.floor(labels.length / 6)) === 0 ? (
            <text
              key={label + index}
              x={x(index)}
              y={height - 6}
              textAnchor="middle"
              className="fill-[var(--color-ink-faint)] text-[10px]"
            >
              {label}
            </text>
          ) : null,
        )}
      </svg>

      {hover !== null && (
        <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 rounded-md border border-line bg-sunken px-3 py-2 text-[11.5px]">
          <span className="font-medium text-ink">{labels[hover]}</span>
          {series.map((item) => (
            <span key={item.name} className="flex items-center gap-1.5 text-ink-muted">
              <span className="h-[3px] w-3.5 rounded-full" style={{ background: item.color }} />
              {item.name}
              <span className="font-medium text-ink tabular">
                {(item.format ?? compactNumber)(item.values[hover] ?? 0)}
              </span>
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

function compactNumber(value: number): string {
  if (Math.abs(value) >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`
  if (Math.abs(value) >= 1000) return `${(value / 1000).toFixed(value < 10_000 ? 1 : 0)}K`
  return String(Math.round(value))
}

/**
 * A horizontal proportion bar — the status-class distribution, mostly.
 *
 * Segments carry a state hue rather than a categorical palette, because 2xx,
 * 4xx and 5xx *are* states. Segments below 1.5% are still drawn, at a minimum
 * width, so a handful of 5xx in a million requests is visible rather than
 * rounded out of existence.
 */
export function StackedBar({
  segments,
  height = 10,
}: {
  segments: { label: string; value: number; color: string }[]
  height?: number
}) {
  const total = segments.reduce((sum, segment) => sum + segment.value, 0)
  if (total === 0) {
    return <div className="h-2.5 w-full rounded-full bg-sunken" />
  }

  return (
    <div>
      <div className="flex w-full overflow-hidden rounded-full" style={{ height }}>
        {segments
          .filter((segment) => segment.value > 0)
          .map((segment) => (
            <div
              key={segment.label}
              style={{
                width: `${Math.max(1.5, (segment.value / total) * 100)}%`,
                background: segment.color,
              }}
              title={`${segment.label}: ${segment.value.toLocaleString()}`}
            />
          ))}
      </div>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
        {segments.map((segment) => (
          <span key={segment.label} className="flex items-center gap-1.5 text-[11.5px]">
            <span className="dot" style={{ background: segment.color }} />
            <span className="text-ink-muted">{segment.label}</span>
            <span className="font-medium tabular">{segment.value.toLocaleString()}</span>
          </span>
        ))}
      </div>
    </div>
  )
}
