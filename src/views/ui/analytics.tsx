/**
 * Pieces the analytics screens share.
 *
 * Seven screens ask variations of one question, so the parts that are the same
 * on all of them — the header with its range picker, the simulated-data
 * notice, the standard chart, the route table — live here once. What differs
 * between them is which panels they compose and in what order, which is the
 * part worth writing per screen.
 */

import type { ReactNode } from 'react'
import { bytes, count, latency, percent, rate } from '@/js/hooks'
import type { Range, RouteRow, SeriesPoint, Summary } from '@/js/types'
import { LineChart } from './charts'
import { HealthDot, RouteLabel, TrendDelta } from './gateway'
import { Banner, Empty, PageHeader, Panel, PanelHeader, Skeleton, Stat, Table, TBody, TD, TH, THead, TR } from './kit'
import { RangePicker } from './RangePicker'

/** The header every analytics screen carries. */
export function AnalyticsHeader({
  title,
  description,
  range,
  simulated,
}: {
  title: string
  description?: string
  range: Range
  simulated?: boolean
}) {
  return (
    <>
      <PageHeader
        title={title}
        description={description ? `${description} · ${range.label}` : range.label}
        actions={<RangePicker current={range.key} />}
      />
      {simulated && (
        <Banner tone="caution" className="mb-4">
          These figures come from generated demo data, not from a running gateway.
        </Banner>
      )}
    </>
  )
}

/** The five-figure headline row, used by four of the seven screens. */
export function SummaryRow({ summary }: { summary: Summary }) {
  return (
    <Panel padded={false} className="mb-4 overflow-hidden">
      <div className="grid divide-y divide-line sm:grid-cols-2 sm:divide-y-0 lg:grid-cols-5 lg:divide-x">
        <Stat label="Requests" value={count(summary.requests)} hint={<TrendDelta delta={summary.deltas.requests} />} />
        <Stat label="Requests / sec" value={rate(summary.rps)} hint={<TrendDelta delta={summary.deltas.rps} />} />
        <Stat
          label="Error rate"
          value={percent(summary.error_rate)}
          tone={summary.server_error_rate >= 0.05 ? 'critical' : undefined}
          hint={<TrendDelta delta={summary.deltas.error_rate} lowerIsBetter />}
        />
        <Stat
          label="P95"
          value={latency(summary.p95)}
          tone={summary.p95 >= 1000 ? 'critical' : undefined}
          hint={<TrendDelta delta={summary.deltas.p95} lowerIsBetter />}
        />
        <Stat label="Bandwidth" value={bytes(summary.bytes_out)} hint={<TrendDelta delta={summary.deltas.bytes_out} />} />
      </div>
    </Panel>
  )
}

/** `14:05` — the x-axis label for every chart on these screens. */
export function clockLabel(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
}

type ChartKind = 'traffic' | 'latency' | 'bandwidth' | 'security'

/**
 * The standard chart, in four flavours.
 *
 * Each flavour picks its series and its colours deliberately. Errors are drawn
 * in the *state* hues rather than the series palette, because on this
 * dashboard red already means "bad" and a red line that meant "series three"
 * would be fighting that.
 */
export function TrafficChart({
  series,
  kind = 'traffic',
  height = 300,
}: {
  series?: SeriesPoint[]
  kind?: ChartKind
  height?: number
}) {
  if (!series) return <Skeleton rows={6} />

  const labels = series.map((point) => clockLabel(point.t))

  if (kind === 'latency') {
    return (
      <LineChart
        labels={labels}
        height={height}
        series={[
          { name: 'P95', color: 'var(--color-series-1)', values: series.map((p) => p.p95), format: latency, area: true },
          { name: 'P99', color: 'var(--color-series-3)', values: series.map((p) => p.p99), format: latency },
          { name: 'Mean', color: 'var(--color-series-2)', values: series.map((p) => p.avg_latency_ms), format: latency },
        ]}
      />
    )
  }

  if (kind === 'bandwidth') {
    return (
      <LineChart
        labels={labels}
        height={height}
        series={[
          { name: 'Bytes out', color: 'var(--color-series-1)', values: series.map((p) => p.bytes_out), format: bytes, area: true },
        ]}
      />
    )
  }

  if (kind === 'security') {
    return (
      <LineChart
        labels={labels}
        height={height}
        series={[
          { name: 'Blocked', color: 'var(--color-critical)', values: series.map((p) => p.blocked), area: true },
          { name: 'Rate limited', color: 'var(--color-caution)', values: series.map((p) => p.rate_limited) },
          { name: 'Requests', color: 'var(--color-series-1)', values: series.map((p) => p.requests) },
        ]}
        rightAxis={2}
      />
    )
  }

  return (
    <LineChart
      labels={labels}
      height={height}
      series={[
        { name: 'Requests', color: 'var(--color-series-1)', values: series.map((p) => p.requests), area: true },
        { name: '5xx', color: 'var(--color-critical)', values: series.map((p) => p.errors_5xx) },
        { name: '4xx', color: 'var(--color-caution)', values: series.map((p) => p.errors_4xx) },
      ]}
    />
  )
}

/** The full per-route table, shared by three screens. */
export function RouteTable({
  routes,
  columns = 'traffic',
}: {
  routes?: RouteRow[]
  columns?: 'traffic' | 'latency'
}) {
  if (!routes) return <div className="p-5"><Skeleton rows={6} /></div>
  if (routes.length === 0) {
    return <Empty title="No traffic in this range" body="Nothing matched a route in this window." />
  }

  return (
    <Table>
      <THead>
        <TR>
          <TH>Route</TH>
          <TH align="right">Requests</TH>
          <TH align="right">RPS</TH>
          {columns === 'traffic' ? (
            <>
              <TH align="right">Error rate</TH>
              <TH align="right">5xx</TH>
              <TH align="right">P95</TH>
              <TH align="right">Bandwidth</TH>
            </>
          ) : (
            <>
              <TH align="right">P50</TH>
              <TH align="right">P95</TH>
              <TH align="right">P99</TH>
              <TH align="right">Max</TH>
              <TH align="right">Timeouts</TH>
            </>
          )}
          <TH align="right">Health</TH>
        </TR>
      </THead>
      <TBody>
        {routes.map((route) => (
          <TR key={route.id ?? route.label}>
            <TD><RouteLabel route={route} /></TD>
            <TD align="right" className="num">{count(route.requests)}</TD>
            <TD align="right" className="num text-ink-muted">{route.rps.toFixed(2)}</TD>
            {columns === 'traffic' ? (
              <>
                <TD align="right" className={`num ${route.error_rate > 0.05 ? 'text-critical' : ''}`}>
                  {percent(route.error_rate)}
                </TD>
                <TD align="right" className={`num ${route.errors_5xx > 0 ? 'text-critical' : 'text-ink-muted'}`}>
                  {count(route.errors_5xx)}
                </TD>
                <TD align="right" className={`num ${route.p95 >= 1000 ? 'text-critical' : ''}`}>
                  {latency(route.p95)}
                </TD>
                <TD align="right" className="num text-ink-muted">{bytes(route.bytes_out)}</TD>
              </>
            ) : (
              <>
                <TD align="right" className="num text-ink-muted">{latency(route.p50)}</TD>
                <TD align="right" className={`num ${route.p95 >= 1000 ? 'text-critical' : ''}`}>{latency(route.p95)}</TD>
                <TD align="right" className="num">{latency(route.p99)}</TD>
                <TD align="right" className="num text-ink-muted">{latency(route.max_latency_ms)}</TD>
                <TD align="right" className={`num ${route.timeouts > 0 ? 'text-critical' : 'text-ink-muted'}`}>
                  {count(route.timeouts)}
                </TD>
              </>
            )}
            <TD align="right"><HealthDot health={route.health} /></TD>
          </TR>
        ))}
      </TBody>
    </Table>
  )
}

/** A ranked breakdown — traffic by domain, method, client. */
export function Breakdown({
  title,
  rows,
  unit = 'requests',
}: {
  title: string
  rows?: { label: string; requests: number; bytes_out: number }[]
  unit?: 'requests' | 'bytes'
}) {
  const max = Math.max(...(rows ?? []).map((row) => row.requests), 1)
  return (
    <Panel padded={false}>
      <PanelHeader title={title} />
      {!rows ? (
        <div className="p-5"><Skeleton rows={4} /></div>
      ) : rows.length === 0 ? (
        <Empty title="Nothing recorded" />
      ) : (
        <ul className="divide-y divide-line">
          {rows.map((row) => (
            <li key={row.label} className="px-5 py-2.5">
              <div className="flex items-baseline justify-between gap-3">
                <span className="mono truncate text-[12.5px]">{row.label}</span>
                <span className="num shrink-0 text-[12.5px] font-medium">
                  {unit === 'bytes' ? bytes(row.bytes_out) : count(row.requests)}
                </span>
              </div>
              {/* A magnitude bar, not a chart. One hue, because the label is
                  the identity and colouring by category would invite a reader
                  to look for a meaning that is not there. */}
              <div className="mt-1.5 h-1 w-full overflow-hidden rounded-full bg-sunken">
                <div
                  className="h-full rounded-full bg-brand/45"
                  style={{ width: `${Math.max(2, (row.requests / max) * 100)}%` }}
                />
              </div>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  )
}

export function Section({ children }: { children: ReactNode }) {
  return <div className="mt-4 grid gap-4 lg:grid-cols-2">{children}</div>
}
