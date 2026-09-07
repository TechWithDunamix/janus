import { Head, Link } from '@inertiajs/react'
import { ago, useCan } from '@/js/hooks'
import { Badge, Empty, LinkButton, PageHeader, Panel } from '@/views/ui/kit'

type Target = {
  id: number; dial: string; weight: number; enabled: boolean
  healthy: boolean; last_error: string | null; last_checked_at: string | null
}
type Upstream = {
  id: number; name: string; slug: string; description: string | null
  policy: string; enabled: boolean; maintenance: boolean
  max_fails: number; health_path: string | null; route_count: number
  targets: Target[]
}

export default function Upstreams({ upstreams }: { upstreams: Upstream[] }) {
  const can = useCan()

  return (
    <>
      <Head title="Upstreams" />
      <PageHeader
        title="Upstreams"
        description="Backend pools. Health checking is declared here and executed by Caddy — no Janus process sits in the request path."
        actions={
          can('upstreams.write') && (
            <LinkButton href="/upstreams/0" size="sm" variant="primary">
              New upstream
            </LinkButton>
          )
        }
      />

      {upstreams.length === 0 ? (
        <Panel padded={false}>
          <Empty title="No upstreams yet" body="An upstream is a named pool of backend servers." />
        </Panel>
      ) : (
        <div className="grid gap-3 lg:grid-cols-2">
          {upstreams.map((upstream) => {
            const healthy = upstream.targets.filter((t) => t.healthy && t.enabled).length
            const total = upstream.targets.filter((t) => t.enabled).length
            const totalWeight = upstream.targets.reduce((sum, t) => sum + (t.enabled ? t.weight : 0), 0) || 1
            return (
              <Panel key={upstream.id} padded={false}>
                <div className="flex items-start justify-between gap-3 border-b border-line px-5 py-3.5">
                  <div className="min-w-0">
                    <Link href={`/upstreams/${upstream.id}`} className="text-[13px] font-semibold hover:text-brand">
                      {upstream.name}
                    </Link>
                    <p className="mt-0.5 text-[11.5px] text-ink-faint">
                      {upstream.policy.replace(/_/g, ' ')} · {upstream.route_count} route(s)
                      {upstream.health_path ? ` · active check ${upstream.health_path}` : ' · passive checks only'}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-1.5">
                    {upstream.maintenance && <Badge tone="caution">draining</Badge>}
                    <Badge tone={healthy === total && total > 0 ? 'positive' : healthy === 0 ? 'critical' : 'caution'}>
                      {healthy}/{total} healthy
                    </Badge>
                  </div>
                </div>

                <ul className="divide-y divide-line">
                  {upstream.targets.length === 0 && (
                    <li className="px-5 py-3 text-[12px] text-caution">
                      No targets. Routes pointing here will answer 503.
                    </li>
                  )}
                  {upstream.targets.map((target) => (
                    <li key={target.id} className="flex items-center gap-3 px-5 py-2.5">
                      <span className={`dot ${target.healthy ? 'bg-ok' : 'bg-critical'}`} />
                      <span className="mono text-[12.5px]">{target.dial}</span>
                      {!target.enabled && <Badge tone="neutral">disabled</Badge>}
                      <span className="ml-auto flex items-center gap-2.5 text-[11.5px] text-ink-faint">
                        <span className="num">weight {target.weight}</span>
                        {/* The share the weight actually produces. A weight of
                            5 means nothing on its own; 45% of traffic does. */}
                        <span className="num">
                          {target.enabled ? `${Math.round((target.weight / totalWeight) * 100)}%` : '—'}
                        </span>
                        <span>{ago(target.last_checked_at)}</span>
                      </span>
                    </li>
                  ))}
                </ul>

                {upstream.targets.some((t) => t.last_error) && (
                  <div className="border-t border-line bg-critical-soft px-5 py-2 text-[11.5px] text-critical">
                    {upstream.targets.filter((t) => t.last_error).map((t) => (
                      <div key={t.id} className="mono">{t.dial} — {t.last_error}</div>
                    ))}
                  </div>
                )}
              </Panel>
            )
          })}
        </div>
      )}
    </>
  )
}
