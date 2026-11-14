/**
 * One route's complete investigation view.
 *
 * The screen you land on from "most problematic". It has to answer the next
 * question after "this route is failing", which is always some form of *how* —
 * so it puts the status distribution, the client breakdown and the recent
 * individual requests on one page rather than making an operator navigate
 * between three.
 */

import { Head } from '@inertiajs/react'
import { bytes, count, latency, percent, rate } from '@/js/hooks'
import type { Health, Range, SeriesPoint } from '@/js/types'
import { TrafficChart } from '@/views/ui/analytics'
import { StackedBar } from '@/views/ui/charts'
import { Figure, HealthDot } from '@/views/ui/gateway'
import { RangePicker } from '@/views/ui/RangePicker'
import {
  Badge, Empty, LinkButton, PageHeader, Panel, PanelHeader, Skeleton,
  Table, TBody, TD, TH, THead, TR,
} from '@/views/ui/kit'

type Detail = {
  stats: {
    requests: number; rps: number; error_rate: number; server_error_rate: number
    errors_4xx: number; errors_5xx: number; avg_latency_ms: number
    p50: number; p95: number; p99: number; max_latency_ms: number
    bytes_out: number; blocked: number; rate_limited: number; timeouts: number
  }
  health: Health
  series: SeriesPoint[]
  status_distribution: { label: string; count: number; share: number }[]
  clients: { ip: string; label: string | null; requests: number; bytes_out: number; status: string }[]
  upstream: { id: number; name: string } | null
}

export default function RouteDetail({
  range,
  route,
  detail,
}: {
  range: Range
  route: {
    id: number; name: string; label: string; path: string; method: string
    enabled: boolean; domain: string | null; upstream: string | null
    auth_policy: string; priority: number
  }
  detail?: Detail
}) {
  return (
    <>
      <Head title={route.name} />

      <PageHeader
        breadcrumbs={[
          { label: 'Analytics', href: '/analytics' },
          { label: 'Routes', href: '/analytics/routes' },
        ]}
        title={route.name}
        description={route.label}
        actions={
          <div className="flex items-center gap-2">
            <LinkButton href={`/routes/${route.id}`} size="sm" variant="secondary">
              Edit route
            </LinkButton>
            <RangePicker current={range.key} />
          </div>
        }
      />

      <Panel className="mb-4">
        <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
          {detail && <HealthDot health={detail.health} />}
          <Figure label="Method" value={route.method} />
          <Figure label="Path" value={<span className="mono">{route.path}</span>} />
          <Figure label="Domain" value={route.domain ?? 'any'} />
          <Figure label="Upstream" value={route.upstream ?? '—'} />
          <Figure label="Auth" value={route.auth_policy} />
          <Figure label="Priority" value={route.priority} />
          <Figure
            label="State"
            value={<Badge tone={route.enabled ? 'positive' : 'neutral'}>{route.enabled ? 'Enabled' : 'Disabled'}</Badge>}
          />
        </div>
      </Panel>

      {detail ? (
        <>
          <Panel padded={false} className="mb-4 overflow-hidden">
            <div className="grid gap-px bg-line sm:grid-cols-3 lg:grid-cols-6">
              <Cell label="Requests" value={count(detail.stats.requests)} />
              <Cell label="Requests / sec" value={rate(detail.stats.rps)} />
              <Cell
                label="Error rate"
                value={percent(detail.stats.error_rate)}
                bad={detail.stats.server_error_rate >= 0.05}
              />
              <Cell label="P50" value={latency(detail.stats.p50)} />
              <Cell label="P95" value={latency(detail.stats.p95)} bad={detail.stats.p95 >= 1000} />
              <Cell label="P99" value={latency(detail.stats.p99)} />
              <Cell label="4xx" value={count(detail.stats.errors_4xx)} />
              <Cell label="5xx" value={count(detail.stats.errors_5xx)} bad={detail.stats.errors_5xx > 0} />
              <Cell label="Timeouts" value={count(detail.stats.timeouts)} bad={detail.stats.timeouts > 0} />
              <Cell label="Rate limited" value={count(detail.stats.rate_limited)} />
              <Cell label="Blocked" value={count(detail.stats.blocked)} />
              <Cell label="Bandwidth" value={bytes(detail.stats.bytes_out)} />
            </div>
          </Panel>

          <div className="grid gap-4 lg:grid-cols-3">
            <Panel className="lg:col-span-2" padded={false}>
              <PanelHeader title="Traffic and errors" />
              <div className="p-5">
                <TrafficChart series={detail.series} kind="traffic" height={280} />
              </div>
            </Panel>

            <Panel padded={false}>
              <PanelHeader title="Status distribution" />
              <div className="p-5">
                <StackedBar
                  segments={[
                    { label: '2xx', value: detail.status_distribution.find((s) => s.label === '2xx')?.count ?? 0, color: 'var(--color-ok)' },
                    { label: '3xx', value: detail.status_distribution.find((s) => s.label === '3xx')?.count ?? 0, color: 'var(--color-series-2)' },
                    { label: '4xx', value: detail.status_distribution.find((s) => s.label === '4xx')?.count ?? 0, color: 'var(--color-caution)' },
                    { label: '5xx', value: detail.status_distribution.find((s) => s.label === '5xx')?.count ?? 0, color: 'var(--color-critical)' },
                  ]}
                />
              </div>
            </Panel>
          </div>

          <Panel className="mt-4" padded={false}>
            <PanelHeader title="Latency" description="P95 and P99 against the mean." />
            <div className="p-5">
              <TrafficChart series={detail.series} kind="latency" height={260} />
            </div>
          </Panel>

          <Panel className="mt-4" padded={false}>
            <PanelHeader title="Client distribution" description="Who called this route in the window." />
            {detail.clients.length === 0 ? (
              <Empty title="No client detail" body="Request-level records for this window have aged out." />
            ) : (
              <Table>
                <THead>
                  <TR>
                    <TH>Client</TH>
                    <TH>Label</TH>
                    <TH align="right">Requests</TH>
                    <TH align="right">Bandwidth</TH>
                  </TR>
                </THead>
                <TBody>
                  {detail.clients.map((client) => (
                    <TR key={client.ip}>
                      <TD><span className="mono">{client.ip}</span></TD>
                      <TD className="text-ink-muted">{client.label ?? '—'}</TD>
                      <TD align="right" className="num">{count(client.requests)}</TD>
                      <TD align="right" className="num text-ink-muted">{bytes(client.bytes_out)}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            )}
          </Panel>
        </>
      ) : (
        <Panel><Skeleton rows={10} /></Panel>
      )}
    </>
  )
}

function Cell({ label, value, bad }: { label: string; value: string; bad?: boolean }) {
  return (
    <div className="bg-surface px-4 py-3">
      <div className="eyebrow">{label}</div>
      <div className={`mt-1 num text-[15px] font-semibold ${bad ? 'text-critical' : ''}`}>{value}</div>
    </div>
  )
}
