/**
 * The control plane shell: a sidebar, a thin top bar, and the page.
 *
 * The navigation is grouped exactly as the product's information architecture
 * is — Gateway, Analytics, Security, Team, System — and every item is filtered
 * by permission, so an Analyst's sidebar is genuinely shorter rather than full
 * of doors that will refuse them.
 *
 * That filtering is *cosmetic*. The gate that refuses an action is on the
 * route; this only avoids showing a door that will not open.
 *
 * The gateway picker lives in the top bar rather than the sidebar because the
 * selected gateway changes the meaning of every screen, and its sync state is
 * chrome: an operator editing a route needs to see DRIFTED without navigating
 * somewhere else to find out.
 */

import { Link, router, usePage } from '@inertiajs/react'
import type { ReactNode } from 'react'
import { useState } from 'react'
import { cx, useCan, useShared, useTheme } from '@/js/hooks'
import { FlashMessages } from '@/views/ui/FlashMessages'
import { LogoMark } from '@/views/ui/Logo'
import { Badge, MenuItem, Popover } from '@/views/ui/kit'
import {
  IconAllow,
  IconAudit,
  IconBandwidth,
  IconBook,
  IconBlock,
  IconChart,
  IconChevronDown,
  IconClients,
  IconConfig,
  IconDeploy,
  IconDomain,
  IconError,
  IconGateway,
  IconGauge,
  IconHeart,
  IconKey,
  IconLogout,
  IconMoon,
  IconOverview,
  IconPolicy,
  IconPuzzle,
  IconPulse,
  IconRoles,
  IconRoute,
  IconSettings,
  IconShield,
  IconSun,
  IconThrottle,
  IconUpstream,
  IconUsers,
} from '@/views/ui/icons'

type NavItem = {
  label: string
  href: string
  icon: (props: { className?: string }) => ReactNode
  permission?: string
  /** Match on prefix rather than equality — `/routes/12` should light `Routes`.
   *  Exact-only matching leaves the sidebar blank on every detail page, which
   *  is where a reader most wants to know where they are. */
  exact?: boolean
}

type NavGroup = { heading?: string; items: NavItem[] }

const NAVIGATION: NavGroup[] = [
  {
    items: [
      { label: 'Overview', href: '/', icon: IconOverview, exact: true, permission: 'analytics.read' },
    ],
  },
  {
    heading: 'Gateway',
    items: [
      { label: 'Gateways', href: '/gateways', icon: IconGateway, permission: 'gateway.read' },
      { label: 'Routes', href: '/routes', icon: IconRoute, permission: 'routes.read' },
      { label: 'Upstreams', href: '/upstreams', icon: IconUpstream, permission: 'upstreams.read' },
      { label: 'Domains', href: '/domains', icon: IconDomain, permission: 'domains.read' },
      { label: 'Configuration', href: '/configuration', icon: IconConfig, permission: 'configuration.read' },
    ],
  },
  {
    heading: 'Analytics',
    items: [
      { label: 'Overview', href: '/analytics', icon: IconChart, exact: true, permission: 'analytics.read' },
      { label: 'Requests', href: '/analytics/requests', icon: IconPulse, permission: 'analytics.read' },
      { label: 'Routes', href: '/analytics/routes', icon: IconRoute, permission: 'analytics.read' },
      { label: 'Clients', href: '/analytics/clients', icon: IconClients, permission: 'analytics.read' },
      { label: 'Errors', href: '/analytics/errors', icon: IconError, permission: 'analytics.read' },
      { label: 'Performance', href: '/analytics/performance', icon: IconGauge, permission: 'analytics.read' },
      { label: 'Bandwidth', href: '/analytics/bandwidth', icon: IconBandwidth, permission: 'analytics.read' },
    ],
  },
  {
    heading: 'Security',
    items: [
      { label: 'Overview', href: '/security', icon: IconShield, exact: true, permission: 'security.read' },
      { label: 'Rate limits', href: '/security/rate-limits', icon: IconThrottle, permission: 'security.read' },
      { label: 'Allowlist', href: '/security/allowlist', icon: IconAllow, permission: 'security.read' },
      { label: 'Blocklist', href: '/security/blocklist', icon: IconBlock, permission: 'security.read' },
      { label: 'API keys', href: '/security/api-keys', icon: IconKey, permission: 'apikeys.read' },
      { label: 'Policies', href: '/security/policies', icon: IconPolicy, permission: 'security.read' },
    ],
  },
  {
    heading: 'Team',
    items: [
      { label: 'Users', href: '/team/users', icon: IconUsers, permission: 'users.read' },
      { label: 'Roles', href: '/team/roles', icon: IconRoles, permission: 'roles.read' },
      { label: 'Audit log', href: '/team/audit', icon: IconAudit, permission: 'audit.read' },
    ],
  },
  {
    heading: 'System',
    items: [
      { label: 'Deployments', href: '/system/deployments', icon: IconDeploy, permission: 'configuration.read' },
      { label: 'Health', href: '/system/health', icon: IconHeart, permission: 'gateway.read' },
      { label: 'Caddy modules', href: '/system/modules', icon: IconPuzzle, permission: 'gateway.read' },
      { label: 'Settings', href: '/system/settings', icon: IconSettings, permission: 'gateway.read' },
      // No permission: documentation a Viewer cannot read is documentation
      // withheld from the person most likely to need it.
      { label: 'Documentation', href: '/docs', icon: IconBook },
    ],
  },
]

const SYNC_TONE: Record<string, 'positive' | 'caution' | 'critical' | 'neutral'> = {
  SYNCED: 'positive',
  SYNCING: 'caution',
  DRIFTED: 'caution',
  FAILED: 'critical',
}

export function AppLayout({ children }: { children: ReactNode }) {
  const { url } = usePage()
  const { auth, gateways, app } = useShared()
  const can = useCan()
  const [theme, setTheme, resolvedTheme] = useTheme()
  const [mobileOpen, setMobileOpen] = useState(false)

  const path = url.split('?')[0]
  const isCurrent = (item: NavItem) =>
    item.exact ? path === item.href : path === item.href || path.startsWith(`${item.href}/`)

  const groups = NAVIGATION.map((group) => ({
    ...group,
    items: group.items.filter((item) => !item.permission || can(item.permission)),
  })).filter((group) => group.items.length > 0)

  return (
    <div className="min-h-screen bg-canvas text-[13px] text-ink">
      {/* The one banner that is always worth the space it takes. Simulated
          gateway state looks identical to real gateway state, so the only
          honest thing to do is say so on every screen. */}
      {app.simulated && (
        <div className="flex items-center justify-center gap-2 bg-caution-soft px-4 py-1.5 text-[11.5px] font-medium text-caution">
          <span className="dot bg-caution" />
          Simulated gateway — Janus is not connected to a running Caddy. All traffic figures are
          generated demo data.
        </div>
      )}

      <div className="flex min-h-screen">
        {/* -- sidebar ------------------------------------------------- */}
        {/*
          A fixed rail with its own scroll, not a column in the page flow.
          `lg:static` was wrong: it put the sidebar in normal flow, so the whole
          nav scrolled away with the page and a long dashboard left the reader
          with no navigation at all. `sticky top-0 h-screen` keeps it in place
          and gives it its own overflow, which is what a rail of twenty-eight
          links needs.
        */}
        <aside
          className={cx(
            'fixed inset-y-0 left-0 z-40 flex w-[236px] shrink-0 flex-col border-r border-line bg-surface transition-transform',
            'lg:sticky lg:top-0 lg:h-screen lg:translate-x-0',
            mobileOpen ? 'translate-x-0' : '-translate-x-full',
          )}
        >
          <div className="flex h-14 shrink-0 items-center gap-2 border-b border-line px-4">
            <LogoMark className="h-[18px] w-[18px] text-brand" />
            <span className="text-[14px] font-semibold tracking-tight">Janus</span>
            {/* `ink-muted`, not `ink-faint`. At 10px this needs 4.5:1 and the
                faint weight only reaches 4.15:1 on the sunken tint in dark
                mode — the one contrast failure in the whole interface when it
                was audited. */}
            <span className="ml-auto rounded-full bg-sunken px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-ink-muted">
              {app.env}
            </span>
          </div>

          <nav className="min-h-0 flex-1 overflow-y-auto px-3 py-4">
            {groups.map((group, index) => (
              <div key={group.heading ?? index} className={index > 0 ? 'mt-5' : ''}>
                {group.heading && (
                  <p className="nav-heading">{group.heading}</p>
                )}
                <ul className="space-y-px">
                  {group.items.map((item) => {
                    const Icon = item.icon
                    const current = isCurrent(item)
                    return (
                      <li key={item.href}>
                        <Link
                          href={item.href}
                          onClick={() => setMobileOpen(false)}
                          aria-current={current ? 'page' : undefined}
                          data-active={current ? 'true' : undefined}
                          className="nav-link"
                        >
                          <Icon className="h-4 w-4 shrink-0" />
                          {item.label}
                        </Link>
                      </li>
                    )
                  })}
                </ul>
              </div>
            ))}
          </nav>

          <div className="shrink-0 border-t border-line p-3">
            <Popover
              align="left"
              trigger={({ toggle }) => (
                <button
                  onClick={toggle}
                  type="button"
                  className="flex w-full items-center gap-2.5 rounded-md px-2 py-2 text-left transition hover:bg-sunken"
                >
                  <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-brand-soft text-[11px] font-semibold text-brand">
                    {(auth.user?.name ?? '?').slice(0, 2).toUpperCase()}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[12.5px] font-medium">
                      {auth.user?.name}
                    </span>
                    <span className="block truncate text-[11px] text-ink-faint">
                      {auth.roles[0] ?? 'No role'}
                    </span>
                  </span>
                  <IconChevronDown className="h-3.5 w-3.5 shrink-0 text-ink-faint" />
                </button>
              )}
            >
              {() => (
                <>
              <div className="border-b border-line px-3 py-2">
                <p className="truncate text-[12px] font-medium">{auth.user?.email}</p>
                <p className="mt-0.5 text-[11px] text-ink-faint">
                  {auth.permissions.length} permissions
                </p>
              </div>
              <MenuItem
                icon={resolvedTheme === 'dark' ? <IconSun className="h-4 w-4" /> : <IconMoon className="h-4 w-4" />}
                onClick={() => setTheme(resolvedTheme === 'dark' ? 'light' : 'dark')}
              >
                {resolvedTheme === 'dark' ? 'Light theme' : 'Dark theme'}
              </MenuItem>
              {theme !== 'system' && (
                <MenuItem
                  icon={<IconGauge className="h-4 w-4" />}
                  onClick={() => setTheme('system')}
                >
                  Match system
                </MenuItem>
              )}
              <MenuItem
                icon={<IconLogout className="h-4 w-4" />}
                onClick={() => router.post('/logout')}
                tone="danger"
              >
                Sign out
              </MenuItem>
                </>
              )}
            </Popover>
          </div>
        </aside>

        {mobileOpen && (
          <button
            type="button"
            aria-label="Close navigation"
            className="fixed inset-0 z-30 bg-[color-mix(in_srgb,var(--color-ink)_35%,transparent)] lg:hidden"
            onClick={() => setMobileOpen(false)}
          />
        )}

        {/* -- main ---------------------------------------------------- */}
        <div className="flex min-w-0 flex-1 flex-col">
          <header className="sticky top-0 z-20 flex h-14 shrink-0 items-center gap-3 border-b border-line bg-surface/85 px-4 backdrop-blur lg:px-6">
            <button
              type="button"
              className="rounded-md p-1.5 text-ink-muted hover:bg-sunken lg:hidden"
              onClick={() => setMobileOpen(true)}
              aria-label="Open navigation"
            >
              <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75}>
                <path d="M4 7h16M4 12h16M4 17h16" strokeLinecap="round" />
              </svg>
            </button>

            <GatewayPicker />

            <div className="ml-auto flex items-center gap-2">
              <button
                type="button"
                onClick={() => setTheme(resolvedTheme === 'dark' ? 'light' : 'dark')}
                className="rounded-md p-1.5 text-ink-muted transition hover:bg-sunken hover:text-ink"
                aria-label={resolvedTheme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}
                title={resolvedTheme === 'dark' ? 'Light theme' : 'Dark theme'}
              >
                {resolvedTheme === 'dark'
                  ? <IconSun className="h-4 w-4" />
                  : <IconMoon className="h-4 w-4" />}
              </button>
              {gateways.current && (
                <Badge tone={SYNC_TONE[gateways.current.sync_state] ?? 'neutral'} dot>
                  {gateways.current.sync_state}
                </Badge>
              )}
            </div>
          </header>

          <FlashMessages />

          <main className="mx-auto w-full min-w-0 max-w-[1500px] flex-1 px-4 py-6 lg:px-6">
            {children}
          </main>
        </div>
      </div>
    </div>
  )
}

/**
 * The gateway selector.
 *
 * A form POST rather than a link, because selecting a gateway writes to the
 * session — and a GET that changes server state is a GET a browser is free to
 * prefetch.
 */
function GatewayPicker() {
  const { gateways } = useShared()
  const current = gateways.current
  // Bound to a local before the render prop closes over it: narrowing
  // `gateways.current` does not survive into a callback, so TypeScript treats
  // it as nullable again inside `trigger`.
  if (!current) {
    return <span className="text-[12.5px] text-ink-faint">No gateway</span>
  }

  return (
    <Popover
      align="left"
      trigger={({ toggle }) => (
        <button
          type="button"
          onClick={toggle}
          className="flex items-center gap-2 rounded-md border border-line px-2.5 py-1.5 text-[12.5px] font-medium transition hover:bg-sunken"
        >
          <IconGateway className="h-[15px] w-[15px] text-ink-faint" />
          {current.name}
          <span className="mono text-ink-faint">{current.listen}</span>
          <IconChevronDown className="h-3.5 w-3.5 text-ink-faint" />
        </button>
      )}
    >
      {({ close }) =>
        gateways.list.map((gateway) => (
        <MenuItem
          key={gateway.id}
          onClick={() => {
            close()
            router.post('/gateways/switch', { gateway_id: gateway.id })
          }}
          icon={
            <span
              className={cx(
                'dot',
                gateway.sync_state === 'SYNCED'
                  ? 'bg-ok'
                  : gateway.sync_state === 'FAILED'
                    ? 'bg-critical'
                    : 'bg-caution',
              )}
            />
          }
        >
          <span className="flex w-full items-center gap-2">
            {gateway.name}
            {gateway.region && (
              <span className="ml-auto text-[11px] text-ink-faint">{gateway.region}</span>
            )}
          </span>
        </MenuItem>
        ))
      }
    </Popover>
  )
}
