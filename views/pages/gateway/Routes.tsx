/**
 * The route table.
 *
 * Ordered by priority descending, the same order the generator emits and Caddy
 * evaluates, because that order *is* the routing behaviour — a reader scanning
 * this list is reading the match sequence, and sorting it by name would hide
 * the thing that decides which route answers.
 */

import { Head, Link, router } from '@inertiajs/react'
import { count, latency, percent, useCan } from '@/js/hooks'
import type { RouteRow } from '@/js/types'
import { HealthDot } from '@/views/ui/gateway'
import {
  Badge, Button, Empty, LinkButton, PageHeader, Panel,
  Table, TBody, TD, TH, THead, TR,
} from '@/views/ui/kit'

type Route = {
  id: number; name: string; slug: string; path: string; methods: string
  method_label: string; priority: number; enabled: boolean; maintenance: boolean
  action: string; upstream: string | null; upstream_id: number | null
  canary_percent: number; domain: string | null; auth_policy: string
  timeout_seconds: number; retries: number
}

const ACTION_TONE: Record<string, 'brand' | 'neutral' | 'caution'> = {
  proxy: 'brand',
  redirect: 'neutral',
  static: 'neutral',
  maintenance: 'caution',
}

export default function Routes({
  routes,
  traffic,
}: {
  routes: Route[]
  traffic?: Record<string, RouteRow>
}) {
  const can = useCan()

  return (
    <>
      <Head title="Routes" />
      <PageHeader
        title="Routes"
        description="Evaluated top to bottom. The first match wins, so priority is the routing behaviour."
        actions={
          can('routes.write') && (
            <div className="flex gap-2">
              <LinkButton href="/configuration" size="sm" variant="secondary">
                Review &amp; deploy
              </LinkButton>
              <LinkButton href="/routes/0" size="sm" variant="primary">
                New route
              </LinkButton>
            </div>
          )
        }
      />

      <Panel padded={false}>
        {routes.length === 0 ? (
          <Empty
            title="No routes yet"
            body="A route says what to match and where to send it. Create one, then deploy."
          />
        ) : (
          <Table>
            <THead>
              <TR>
                <TH>Priority</TH>
                <TH>Route</TH>
                <TH>Domain</TH>
                <TH>Target</TH>
                <TH>Auth</TH>
                <TH align="right">Requests (1h)</TH>
                <TH align="right">Errors</TH>
                <TH align="right">P95</TH>
                <TH align="right" />
              </TR>
            </THead>
            <TBody>
              {routes.map((route) => {
                const stats = traffic?.[String(route.id)]
                return (
                  <TR key={route.id} className={route.enabled ? '' : 'opacity-55'}>
                    <TD className="num text-ink-faint">{route.priority}</TD>
                    <TD>
                      <Link href={`/routes/${route.id}`} className="hover:text-brand">
                        <span className="inline-flex items-center gap-2">
                          <span className="rounded-sm bg-sunken px-1.5 py-0.5 text-[10.5px] font-semibold uppercase tracking-wide text-ink-muted">
                            {route.method_label}
                          </span>
                          <span className="mono">{route.path}</span>
                        </span>
                      </Link>
                      <div className="mt-0.5 flex items-center gap-1.5 text-[11.5px] text-ink-faint">
                        {route.name}
                        {route.maintenance && <Badge tone="caution">maintenance</Badge>}
                        {route.canary_percent > 0 && (
                          <Badge tone="brand">{route.canary_percent}% canary</Badge>
                        )}
                      </div>
                    </TD>
                    <TD className="text-ink-muted">{route.domain ?? 'any'}</TD>
                    <TD>
                      <Badge tone={ACTION_TONE[route.action] ?? 'neutral'}>{route.action}</Badge>
                      {route.upstream && (
                        <span className="ml-1.5 text-[11.5px] text-ink-muted">{route.upstream}</span>
                      )}
                    </TD>
                    <TD className="text-ink-muted">{route.auth_policy}</TD>
                    <TD align="right" className="num">{stats ? count(stats.requests) : '—'}</TD>
                    <TD align="right" className={`num ${stats && stats.error_rate > 0.05 ? 'text-critical' : 'text-ink-muted'}`}>
                      {stats ? percent(stats.error_rate) : '—'}
                    </TD>
                    <TD align="right" className="num text-ink-muted">
                      {stats ? latency(stats.p95) : '—'}
                    </TD>
                    <TD align="right">
                      <div className="flex items-center justify-end gap-1.5">
                        {stats && <HealthDot health={stats.health} />}
                        {can('routes.write') && (
                          <Button
                            size="xs"
                            variant="ghost"
                            onClick={() => router.post('/routes/toggle', { id: route.id })}
                          >
                            {route.enabled ? 'Disable' : 'Enable'}
                          </Button>
                        )}
                      </div>
                    </TD>
                  </TR>
                )
              })}
            </TBody>
          </Table>
        )}
      </Panel>

      <p className="mt-3 text-[12px] text-ink-faint">
        Changes here are desired state. Nothing reaches the gateway until you deploy.
      </p>
    </>
  )
}
