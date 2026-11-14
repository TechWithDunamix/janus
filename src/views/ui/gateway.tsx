/**
 * Small pieces that appear across the gateway screens.
 *
 * Here rather than in `kit.tsx` because they know about Janus's domain — a
 * health verdict, a route, a sync state — whereas the kit deliberately does
 * not know what the application is about.
 */

import { Link } from '@inertiajs/react'
import type { ReactNode } from 'react'
import { cx } from '@/js/hooks'
import type { Delta, Health, RouteRow, SyncState } from '@/js/types'
import { Badge } from './kit'

const HEALTH_COLOR: Record<Health, string> = {
  healthy: 'bg-ok',
  warning: 'bg-caution',
  critical: 'bg-critical',
}

export function HealthDot({ health, title }: { health: Health; title?: string }) {
  return (
    <span
      className={cx('dot', HEALTH_COLOR[health])}
      title={title ?? health}
      aria-label={title ?? health}
    />
  )
}

const SYNC_TONE: Record<SyncState, 'positive' | 'caution' | 'critical'> = {
  SYNCED: 'positive',
  SYNCING: 'caution',
  DRIFTED: 'caution',
  FAILED: 'critical',
}

export function SyncBadge({ state }: { state: SyncState }) {
  return (
    <Badge tone={SYNC_TONE[state] ?? 'neutral'} dot>
      {state}
    </Badge>
  )
}

/**
 * A route, written the way routes are written everywhere: method then path.
 *
 * Links to the route's analytics rather than its editor, because the far more
 * common reason to click a route in a table is to find out what it is doing.
 */
export function RouteLabel({ route, href }: { route: RouteRow; href?: string }) {
  const body = (
    <span className="inline-flex items-center gap-2">
      <span className="rounded-sm bg-sunken px-1.5 py-0.5 text-[10.5px] font-semibold uppercase tracking-wide text-ink-muted">
        {route.method}
      </span>
      <span className="mono">{route.path}</span>
    </span>
  )
  if (!route.id) return body
  return (
    <Link href={href ?? `/analytics/routes/${route.id}`} className="hover:text-brand">
      {body}
    </Link>
  )
}

/**
 * The change against the previous window.
 *
 * `lowerIsBetter` exists because a rise is not always good news: requests up
 * 12% is green, error rate up 12% is not, and a component that coloured both
 * the same would be actively misleading on the one screen where it matters.
 *
 * A null percentage renders as "no baseline" rather than "+100%": the previous
 * window was empty, and any percentage would be invented.
 */
export function TrendDelta({
  delta,
  lowerIsBetter = false,
}: {
  delta: Delta
  lowerIsBetter?: boolean
}) {
  if (!delta) return <span className="text-ink-faint">no baseline</span>
  if (delta.percent === null) {
    return <span className="text-ink-faint">no baseline</span>
  }

  const rose = delta.percent > 0
  const good = lowerIsBetter ? !rose : rose
  const flat = Math.abs(delta.percent) < 0.5

  return (
    <span
      className={cx(
        'num font-medium',
        flat ? 'text-ink-faint' : good ? 'text-ok' : 'text-critical',
      )}
    >
      {flat ? '·' : rose ? '↑' : '↓'} {Math.abs(delta.percent).toFixed(1)}%
      <span className="ml-1 font-normal text-ink-faint">vs previous</span>
    </span>
  )
}

/** A labelled figure, for the dense stat rows the gateway screens use. */
export function Figure({
  label,
  value,
  tone,
}: {
  label: string
  value: ReactNode
  tone?: 'critical' | 'ok'
}) {
  return (
    <div>
      <div className="text-[10.5px] uppercase tracking-[0.06em] text-ink-faint">{label}</div>
      <div
        className={cx(
          'num text-[13px] font-medium',
          tone === 'critical' && 'text-critical',
          tone === 'ok' && 'text-ok',
        )}
      >
        {value}
      </div>
    </div>
  )
}
