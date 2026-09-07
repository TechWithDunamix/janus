import { Head } from '@inertiajs/react'
import { dateTime } from '@/js/hooks'
import type { GatewayHealth } from '@/js/types'
import { SyncBadge } from '@/views/ui/gateway'
import { Badge, Banner, Empty, Panel, PanelHeader, PageHeader, Skeleton, Table, TBody, TD, TH, THead, TR } from '@/views/ui/kit'

export default function Health({
  gateway,
  health,
}: {
  gateway: { id: number; name: string; listen: string; admin_url: string | null; sync_state: string }
  health?: GatewayHealth
}) {
  return (
    <>
      <Head title="Health" />
      <PageHeader
        title="Gateway health"
        description={`${gateway.name} · ${gateway.listen}`}
      />

      {!health ? (
        <Panel><Skeleton rows={8} /></Panel>
      ) : (
        <>
          {!health.reachable && (
            <Banner tone="critical" title="The admin API is unreachable" className="mb-4">
              {health.error ?? 'Janus cannot reach this gateway.'} Configuration cannot be deployed
              until it comes back; whatever the gateway is currently running keeps running.
            </Banner>
          )}

          {health.simulated && (
            <Banner tone="caution" title="Simulated gateway" className="mb-4">
              This gateway is backed by the in-process simulator rather than a real Caddy. The
              configuration pipeline is genuine — generation, validation, apply, verify and drift
              all run — but nothing is proxying traffic, and every figure on the analytics screens
              is generated.
            </Banner>
          )}

          <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Tile
              label="Admin API"
              value={health.reachable ? 'reachable' : 'unreachable'}
              tone={health.reachable ? 'ok' : 'critical'}
            />
            <Tile label="Caddy version" value={health.version ?? 'unknown'} />
            <Tile label="Configuration" value={<SyncBadge state={health.sync_state} />} />
            <Tile label="Last synced" value={dateTime(health.last_synced_at)} />
          </div>

          <Panel padded={false}>
            <PanelHeader
              title="Upstream targets"
              description="Health as Caddy's own checkers report it. Janus caches this for the dashboard and never substitutes its own opinion."
            />
            {health.targets_total === 0 ? (
              <Empty title="No targets" body="Define an upstream with at least one target." />
            ) : health.unhealthy.length === 0 ? (
              <div className="px-5 py-4 text-[12.5px] text-ok">
                All {health.targets_total} targets are healthy.
              </div>
            ) : (
              <Table>
                <THead>
                  <TR>
                    <TH>Target</TH>
                    <TH>Reported</TH>
                  </TR>
                </THead>
                <TBody>
                  {health.unhealthy.map((target) => (
                    <TR key={target.dial}>
                      <TD><span className="mono">{target.dial}</span></TD>
                      <TD className="text-critical">{target.error ?? 'unhealthy'}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            )}
          </Panel>

          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            <Panel padded={false}>
              <PanelHeader
                title="Other servers on this Caddy"
                description="Janus writes only the server and logger it owns. Anything else on this instance is read but never rewritten."
              />
              {health.foreign_servers.length === 0 ? (
                <div className="px-5 py-4 text-[12.5px] text-ink-muted">
                  Janus is the only tenant of this Caddy.
                </div>
              ) : (
                <ul className="divide-y divide-line">
                  {health.foreign_servers.map((name) => (
                    <li key={name} className="px-5 py-2.5">
                      <span className="mono text-[12.5px]">{name}</span>
                    </li>
                  ))}
                </ul>
              )}
            </Panel>

            <Panel padded={false}>
              <PanelHeader
                title="Build capabilities"
                description="Which handler modules this Caddy provides. Janus declines to emit a handler the build lacks rather than having the deployment rejected."
              />
              <div className="px-5 py-4">
                <div className="flex items-center gap-2">
                  <Badge tone={health.rate_limiting_available ? 'positive' : 'caution'}>
                    {health.rate_limiting_available ? 'rate limiting available' : 'no rate-limit module'}
                  </Badge>
                  <span className="text-[12px] text-ink-faint">
                    {health.capabilities.length} modules detected
                  </span>
                </div>
                {!health.rate_limiting_available && (
                  <p className="mt-2 text-[12px] text-ink-muted">
                    <span className="mono">http.handlers.rate_limit</span> ships in the{' '}
                    <span className="mono">caddy-ratelimit</span> plugin, not the standard binary.
                    Rate limits are stored and displayed but not enforced by this gateway.
                  </p>
                )}
              </div>
            </Panel>
          </div>
        </>
      )}
    </>
  )
}

function Tile({ label, value, tone }: { label: string; value: React.ReactNode; tone?: 'ok' | 'critical' }) {
  return (
    <Panel>
      <div className="eyebrow">{label}</div>
      <div
        className={`mt-1 text-[15px] font-semibold ${
          tone === 'ok' ? 'text-ok' : tone === 'critical' ? 'text-critical' : ''
        }`}
      >
        {value}
      </div>
    </Panel>
  )
}
