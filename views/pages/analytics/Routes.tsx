import { Head } from '@inertiajs/react'
import { count, latency, percent } from '@/js/hooks'
import type { ProblematicRoute, Range, RouteRow } from '@/js/types'
import { AnalyticsHeader, RouteTable } from '@/views/ui/analytics'
import { Figure, HealthDot } from '@/views/ui/gateway'
import { Badge, Empty, Panel, PanelHeader, Skeleton } from '@/views/ui/kit'
import { Link } from '@inertiajs/react'

export default function Routes({
  range,
  routes,
  problematic,
}: {
  range: Range
  routes?: RouteRow[]
  problematic?: ProblematicRoute[]
}) {
  return (
    <>
      <Head title="Route analytics" />
      <AnalyticsHeader title="Route analytics" description="Every route with traffic" range={range} />

      <Panel padded={false} className="mb-4">
        <PanelHeader
          title="Most problematic"
          description="Ranked by server errors, latency and timeouts, weighted by traffic share. The component scores are shown so the ranking can be checked rather than trusted."
        />
        {!problematic ? (
          <div className="p-5"><Skeleton rows={4} /></div>
        ) : problematic.length === 0 ? (
          <Empty title="Nothing is misbehaving" body="No route crossed a threshold in this range." />
        ) : (
          <div className="divide-y divide-line">
            {problematic.map((route) => (
              <Link
                key={route.id ?? route.label}
                href={route.id ? `/analytics/routes/${route.id}` : '#'}
                className="block px-5 py-4 transition hover:bg-sunken"
              >
                <div className="flex flex-wrap items-center gap-3">
                  <HealthDot health={route.health} />
                  <span className="mono text-[13px] font-medium">{route.label}</span>
                  <Badge tone={route.status === 'CRITICAL' ? 'critical' : 'caution'}>{route.status}</Badge>
                  <span className="ml-auto text-[11px] text-ink-faint">
                    score {(route.score * 100).toFixed(1)}
                  </span>
                </div>
                <div className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-4 lg:grid-cols-7">
                  <Figure label="Error rate" value={percent(route.error_rate)} tone={route.error_rate > 0.05 ? 'critical' : undefined} />
                  <Figure label="5xx" value={count(route.errors_5xx)} tone={route.errors_5xx > 0 ? 'critical' : undefined} />
                  <Figure label="P95" value={latency(route.p95)} tone={route.p95 >= 1000 ? 'critical' : undefined} />
                  <Figure label="P99" value={latency(route.p99)} />
                  <Figure label="Requests" value={count(route.requests)} />
                  <Figure label="Timeouts" value={count(route.timeouts)} />
                  <Figure label="Traffic share" value={percent(route.components.traffic_share, 1)} />
                </div>
              </Link>
            ))}
          </div>
        )}
      </Panel>

      <Panel padded={false}>
        <PanelHeader title="All routes" description="Sorted by request volume." />
        <RouteTable routes={routes} />
      </Panel>
    </>
  )
}
