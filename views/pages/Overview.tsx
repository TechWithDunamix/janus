/**
 * The overview.
 *
 * Built around the twelve questions an operator arrives with, in the order
 * they ask them: how much traffic, is the gateway healthy, are errors rising,
 * what is failing, what is slow, what is unhealthy, who is loudest, who is
 * blocked, what changed. The layout follows that reading order down the page
 * rather than being a grid of equally-weighted tiles.
 *
 * The deliberate choice here is the **weighting**. A row of twelve identical
 * stat cards treats "total requests" and "P99 latency" as equally important,
 * which they are not: the headline row carries five numbers, and everything
 * else gets a panel sized to how long you actually look at it. The problematic
 * routes panel is the largest thing on the screen because it is the one that
 * tells you what to do next.
 */

import { Head, Link } from '@inertiajs/react'
import { bytes, count, latency, percent, rate, useShared } from '@/js/hooks'
import type {
  Alert as AlertRow,
  Deployment,
  GatewayHealth,
  ProblematicRoute,
  Range,
  RouteRow,
  SeriesPoint,
  Summary,
} from '@/js/types'
import { LineChart, StackedBar } from '@/views/ui/charts'
import {
  Badge,
  Empty,
  LinkButton,
  PageHeader,
  Panel,
  PanelHeader,
  Skeleton,
  Stat,
  Table,
  TBody,
  TD,
  TH,
  THead,
  TR,
} from '@/views/ui/kit'
import { RangePicker } from '@/views/ui/RangePicker'
import { HealthDot, RouteLabel, TrendDelta } from '@/views/ui/gateway'

export default function Overview({
  range,
  summary,
  series,
  problematic,
  slow,
  status_distribution,
  top_clients,
  alerts,
  recent_changes,
  health,
}: {
  range: Range
  summary: Summary
  series?: SeriesPoint[]
  problematic?: ProblematicRoute[]
  slow?: RouteRow[]
  status_distribution?: { label: string; count: number; share: number }[]
  top_clients?: { ip: string; label: string | null; requests: number; bytes_out: number; status: string }[]
  alerts?: AlertRow[]
  recent_changes?: Deployment[]
  health?: GatewayHealth
}) {
  const { gateways } = useShared()

  return (
    <>
      <Head title="Overview" />

      <PageHeader
        title="Overview"
        description={
          gateways.current
            ? `${gateways.current.name} · ${gateways.current.listen}`
            : 'No gateway selected'
        }
        actions={<RangePicker current={range.key} />}
      />

      {/* 1–3. How much traffic, is it healthy, are errors rising. */}
      <Panel padded={false} className="mb-4 overflow-hidden">
        <div className="grid divide-y divide-line sm:grid-cols-2 sm:divide-y-0 lg:grid-cols-5 lg:divide-x">
          <Stat
            label="Requests"
            value={count(summary.requests)}
            hint={<TrendDelta delta={summary.deltas.requests} />}
          />
          <Stat
            label="Requests / sec"
            value={rate(summary.rps)}
            hint={<TrendDelta delta={summary.deltas.rps} />}
          />
          <Stat
            label="Error rate"
            value={percent(summary.error_rate)}
            tone={summary.server_error_rate >= 0.05 ? 'critical' : undefined}
            hint={<TrendDelta delta={summary.deltas.error_rate} lowerIsBetter />}
          />
          <Stat
            label="P95 latency"
            value={latency(summary.p95)}
            tone={summary.p95 >= 1000 ? 'critical' : undefined}
            hint={<TrendDelta delta={summary.deltas.p95} lowerIsBetter />}
          />
          <Stat
            label="Bandwidth out"
            value={bytes(summary.bytes_out)}
            hint={<TrendDelta delta={summary.deltas.bytes_out} />}
          />
        </div>
      </Panel>

      {/* The secondary figures. Present, but not competing with the five above. */}
      <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <MiniStat label="Successful" value={count(summary.successful)} />
        <MiniStat label="4xx" value={count(summary.errors_4xx)} />
        <MiniStat label="5xx" value={count(summary.errors_5xx)} tone={summary.errors_5xx > 0} />
        <MiniStat
          label="P50 · P99"
          value={`${latency(summary.p50)} · ${latency(summary.p99)}`}
        />
        <MiniStat label="Blocked" value={count(summary.blocked)} />
        <MiniStat label="Rate limited" value={count(summary.rate_limited)} />
        <MiniStat
          label="Active routes"
          value={`${summary.active_routes} / ${summary.total_routes}`}
        />
        <MiniStat label="Upstreams" value={count(summary.active_upstreams)} />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        {/* Traffic and errors, on one time axis with two scales. */}
        <Panel className="lg:col-span-2" padded={false}>
          <PanelHeader
            title="Traffic and errors"
            description="Requests per bucket against the server error rate."
          />
          <div className="p-5">
            {series ? (
              <LineChart
                labels={series.map((point) => clockLabel(point.t))}
                series={[
                  {
                    name: 'Requests',
                    color: 'var(--color-series-1)',
                    values: series.map((p) => p.requests),
                    area: true,
                  },
                  {
                    name: '5xx',
                    color: 'var(--color-critical)',
                    values: series.map((p) => p.errors_5xx),
                  },
                  {
                    name: '4xx',
                    color: 'var(--color-caution)',
                    values: series.map((p) => p.errors_4xx),
                  },
                ]}
                height={280}
              />
            ) : (
              <Skeleton rows={6} />
            )}
          </div>
        </Panel>

        {/* 2 & 6. Is the gateway healthy, which upstreams are not. */}
        <Panel padded={false}>
          <PanelHeader title="Gateway health" />
          <div className="p-5">
            {health ? <HealthPanel health={health} /> : <Skeleton rows={5} />}
          </div>
        </Panel>
      </div>

      {/* 4. Which routes are failing — the most important panel on the page. */}
      <Panel className="mt-4" padded={false}>
        <PanelHeader
          title="Most problematic routes"
          description="Ranked by server errors, latency and timeouts, weighted by traffic share."
          action={
            <LinkButton href="/analytics/routes" size="xs" variant="ghost">
              All routes
            </LinkButton>
          }
        />
        {problematic ? (
          problematic.length === 0 ? (
            <Empty
              title="Nothing is misbehaving"
              body="No route in this range crossed an error-rate or latency threshold."
            />
          ) : (
            <div className="divide-y divide-line">
              {problematic.map((route) => (
                <ProblemRow key={route.id ?? route.label} route={route} />
              ))}
            </div>
          )
        ) : (
          <div className="p-5">
            <Skeleton rows={4} />
          </div>
        )}
      </Panel>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        {/* 5. Which routes are slow. */}
        <Panel padded={false}>
          <PanelHeader
            title="Slowest routes"
            description="By P95, which is the number users feel."
            action={
              <LinkButton href="/analytics/performance" size="xs" variant="ghost">
                Performance
              </LinkButton>
            }
          />
          {slow ? (
            slow.length === 0 ? (
              <Empty title="No route has enough traffic to rank" />
            ) : (
              <Table>
                <THead>
                  <TR>
                    <TH>Route</TH>
                    <TH align="right">P95</TH>
                    <TH align="right">P99</TH>
                    <TH align="right">Requests</TH>
                  </TR>
                </THead>
                <TBody>
                  {slow.map((route) => (
                    <TR key={route.id ?? route.label}>
                      <TD>
                        <RouteLabel route={route} />
                      </TD>
                      <TD align="right" className="num">
                        {latency(route.p95)}
                      </TD>
                      <TD align="right" className="num text-ink-muted">
                        {latency(route.p99)}
                      </TD>
                      <TD align="right" className="num text-ink-muted">
                        {count(route.requests)}
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            )
          ) : (
            <div className="p-5">
              <Skeleton rows={4} />
            </div>
          )}
        </Panel>

        {/* 7. Who is consuming the most. */}
        <Panel padded={false}>
          <PanelHeader
            title="Top clients"
            description="By request volume in this range."
            action={
              <LinkButton href="/analytics/clients" size="xs" variant="ghost">
                All clients
              </LinkButton>
            }
          />
          {top_clients ? (
            top_clients.length === 0 ? (
              <Empty title="No client traffic recorded" />
            ) : (
              <Table>
                <THead>
                  <TR>
                    <TH>Client</TH>
                    <TH align="right">Requests</TH>
                    <TH align="right">Bandwidth</TH>
                    <TH align="right">State</TH>
                  </TR>
                </THead>
                <TBody>
                  {top_clients.map((client) => (
                    <TR key={client.ip}>
                      <TD>
                        <span className="mono">{client.ip}</span>
                        {client.label && (
                          <span className="ml-2 text-[11.5px] text-ink-faint">{client.label}</span>
                        )}
                      </TD>
                      <TD align="right" className="num">
                        {count(client.requests)}
                      </TD>
                      <TD align="right" className="num text-ink-muted">
                        {bytes(client.bytes_out)}
                      </TD>
                      <TD align="right">
                        <Badge tone={client.status === 'blocked' ? 'critical' : 'neutral'}>
                          {client.status}
                        </Badge>
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            )
          ) : (
            <div className="p-5">
              <Skeleton rows={4} />
            </div>
          )}
        </Panel>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Panel padded={false}>
          <PanelHeader title="Status distribution" />
          <div className="p-5">
            {status_distribution ? (
              <StackedBar
                segments={[
                  {
                    label: '2xx',
                    value: status_distribution.find((s) => s.label === '2xx')?.count ?? 0,
                    color: 'var(--color-ok)',
                  },
                  {
                    label: '3xx',
                    value: status_distribution.find((s) => s.label === '3xx')?.count ?? 0,
                    color: 'var(--color-series-2)',
                  },
                  {
                    label: '4xx',
                    value: status_distribution.find((s) => s.label === '4xx')?.count ?? 0,
                    color: 'var(--color-caution)',
                  },
                  {
                    label: '5xx',
                    value: status_distribution.find((s) => s.label === '5xx')?.count ?? 0,
                    color: 'var(--color-critical)',
                  },
                ]}
              />
            ) : (
              <Skeleton rows={2} />
            )}
          </div>
        </Panel>

        {/* 12. Are there suspicious patterns — surfaced as open alerts. */}
        <Panel padded={false}>
          <PanelHeader
            title="Open alerts"
            action={
              <LinkButton href="/security" size="xs" variant="ghost">
                Security
              </LinkButton>
            }
          />
          {alerts ? (
            alerts.length === 0 ? (
              <Empty title="No open alerts" body="Nothing has crossed a threshold." />
            ) : (
              <ul className="divide-y divide-line">
                {alerts.map((alert) => (
                  <li key={alert.id} className="flex gap-2.5 px-5 py-3">
                    <span
                      className={`dot mt-1.5 ${
                        alert.severity === 'critical' ? 'bg-critical' : 'bg-caution'
                      }`}
                    />
                    <div className="min-w-0">
                      <p className="text-[12.5px] font-medium">{alert.title}</p>
                      {alert.detail && (
                        <p className="mt-0.5 text-[11.5px] text-ink-muted">{alert.detail}</p>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            )
          ) : (
            <div className="p-5">
              <Skeleton rows={3} />
            </div>
          )}
        </Panel>

        {/* 10 & 11. What changed recently, and is the configuration synced. */}
        <Panel padded={false}>
          <PanelHeader
            title="Recent deployments"
            action={
              <LinkButton href="/system/deployments" size="xs" variant="ghost">
                All
              </LinkButton>
            }
          />
          {recent_changes ? (
            recent_changes.length === 0 ? (
              <Empty
                title="Nothing deployed yet"
                body="Run Deploy on the Configuration screen to push the desired state."
              />
            ) : (
              <ul className="divide-y divide-line">
                {recent_changes.map((deployment) => (
                  <li key={deployment.id} className="px-5 py-3">
                    <div className="flex items-center gap-2">
                      <Badge
                        tone={
                          deployment.status === 'succeeded'
                            ? 'positive'
                            : deployment.status === 'failed'
                              ? 'critical'
                              : 'caution'
                        }
                      >
                        v{deployment.version}
                      </Badge>
                      <span className="text-[11.5px] text-ink-faint">
                        {deployment.actor} · {deployment.origin}
                      </span>
                    </div>
                    <p className="mt-1 text-[12px] text-ink-muted">
                      {deployment.error ?? deployment.summary ?? '—'}
                    </p>
                  </li>
                ))}
              </ul>
            )
          ) : (
            <div className="p-5">
              <Skeleton rows={3} />
            </div>
          )}
        </Panel>
      </div>
    </>
  )
}

function MiniStat({ label, value, tone }: { label: string; value: string; tone?: boolean }) {
  return (
    <div className="panel px-4 py-3">
      <div className="eyebrow">{label}</div>
      <div className={`mt-1 num text-[16px] font-semibold ${tone ? 'text-critical' : ''}`}>
        {value}
      </div>
    </div>
  )
}

/**
 * One problematic route.
 *
 * Shows the score's *components*, not just the score. A ranking that says
 * "this is worst" without saying why is a ranking an operator has to take on
 * faith, and the first time it disagrees with their intuition they stop using
 * it.
 */
function ProblemRow({ route }: { route: ProblematicRoute }) {
  return (
    <Link
      href={route.id ? `/analytics/routes/${route.id}` : '/analytics/routes'}
      className="block px-5 py-4 transition hover:bg-sunken"
    >
      <div className="flex flex-wrap items-center gap-3">
        <HealthDot health={route.health} />
        <span className="mono text-[13px] font-medium">{route.label}</span>
        <Badge tone={route.status === 'CRITICAL' ? 'critical' : 'caution'}>{route.status}</Badge>
        {route.upstream && (
          <span className="text-[11.5px] text-ink-faint">→ {route.upstream}</span>
        )}
      </div>

      <div className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-3 lg:grid-cols-6">
        <Figure label="Error rate" value={percent(route.error_rate)} bad={route.error_rate > 0.05} />
        <Figure label="5xx" value={count(route.errors_5xx)} bad={route.errors_5xx > 0} />
        <Figure label="P95" value={latency(route.p95)} bad={route.p95 >= 1000} />
        <Figure label="P99" value={latency(route.p99)} />
        <Figure label="Requests" value={count(route.requests)} />
        <Figure label="Timeouts" value={count(route.timeouts)} bad={route.timeouts > 0} />
      </div>
    </Link>
  )
}

function Figure({ label, value, bad }: { label: string; value: string; bad?: boolean }) {
  return (
    <div>
      <div className="text-[10.5px] uppercase tracking-[0.06em] text-ink-faint">{label}</div>
      <div className={`num text-[13px] font-medium ${bad ? 'text-critical' : ''}`}>{value}</div>
    </div>
  )
}

function HealthPanel({ health }: { health: GatewayHealth }) {
  return (
    <div className="space-y-3">
      <Row
        label="Admin API"
        value={
          health.reachable ? (
            <Badge tone="positive" dot>
              Reachable
            </Badge>
          ) : (
            <Badge tone="critical" dot>
              Unreachable
            </Badge>
          )
        }
      />
      <Row label="Caddy" value={<span className="mono">{health.version ?? 'unknown'}</span>} />
      <Row
        label="Configuration"
        value={
          <Badge
            tone={
              health.sync_state === 'SYNCED'
                ? 'positive'
                : health.sync_state === 'FAILED'
                  ? 'critical'
                  : 'caution'
            }
          >
            {health.sync_state}
          </Badge>
        }
      />
      <Row
        label="Upstream targets"
        value={
          <span className={health.targets_unhealthy > 0 ? 'text-critical' : ''}>
            {health.targets_total - health.targets_unhealthy} / {health.targets_total} healthy
          </span>
        }
      />
      {health.unhealthy.length > 0 && (
        <ul className="rounded-md bg-critical-soft px-3 py-2 text-[11.5px] text-critical">
          {health.unhealthy.map((target) => (
            <li key={target.dial} className="mono">
              {target.dial} — {target.error}
            </li>
          ))}
        </ul>
      )}
      {health.sync_error && (
        <p className="rounded-md bg-caution-soft px-3 py-2 text-[11.5px] text-caution">
          {health.sync_error}
        </p>
      )}
      {!health.rate_limiting_available && (
        <p className="rounded-md bg-sunken px-3 py-2 text-[11.5px] text-ink-muted">
          This Caddy build has no rate-limit module. Limits are stored and shown but not enforced.
        </p>
      )}
      {health.foreign_servers.length > 0 && (
        <p className="text-[11.5px] text-ink-faint">
          Sharing this Caddy with {health.foreign_servers.length} server(s) Janus does not manage.
        </p>
      )}
    </div>
  )
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="text-[12px] text-ink-muted">{label}</span>
      <span className="text-[12.5px]">{value}</span>
    </div>
  )
}

/** `14:05` for short ranges, `Mar 4` for long ones. */
function clockLabel(iso: string): string {
  const date = new Date(iso)
  return date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
}
