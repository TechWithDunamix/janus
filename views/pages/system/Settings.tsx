/**
 * Settings.
 *
 * Read-only, deliberately. Every setting Janus has comes from the environment,
 * so a form here would be a form that either lies about what it changes or
 * introduces a second source of truth that disagrees with the deployment. What
 * the screen does instead is show what the process actually resolved, which is
 * the question an operator is really asking.
 */

import { Head } from '@inertiajs/react'
import { Badge, Banner, PageHeader, Panel, PanelHeader } from '@/views/ui/kit'

type Settings = {
  app_name: string; app_env: string; app_url: string
  caddy_admin_url: string; caddy_server_name: string; caddy_access_log: string
  caddy_simulate: boolean; queue_backend: string; database: string
  request_retention_hours: number; rollup_retention_days: number
  error_rate_critical: number; latency_p95_critical_ms: number
  secret_is_default: boolean
}

export default function SettingsPage({ settings }: { settings: Settings }) {
  return (
    <>
      <Head title="Settings" />
      <PageHeader
        title="Settings"
        description="What this process resolved from the environment. Change these by changing the environment and restarting."
      />

      {settings.secret_is_default && (
        <Banner tone="critical" title="SECRET_KEY is the development default" className="mb-4">
          Sessions and CSRF tokens are signed with a value that is in the source code. Set{' '}
          <span className="mono">SECRET_KEY</span> before this installation is used for anything
          real — Janus refuses to start with it when <span className="mono">APP_ENV=production</span>.
        </Banner>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <Group
          title="Application"
          rows={[
            ['Name', settings.app_name],
            ['Environment', settings.app_env],
            ['URL', settings.app_url],
            ['Database', settings.database],
            ['Queue', settings.queue_backend],
          ]}
        />
        <Group
          title="Caddy"
          rows={[
            ['Admin API', settings.caddy_admin_url],
            ['Owned server', settings.caddy_server_name],
            ['Access log', settings.caddy_access_log],
            [
              'Mode',
              settings.caddy_simulate ? (
                <Badge tone="caution">simulated</Badge>
              ) : (
                <Badge tone="positive">live</Badge>
              ),
            ],
          ]}
        />
        <Group
          title="Retention"
          rows={[
            ['Request records', `${settings.request_retention_hours} hours`],
            ['Rollups', `${settings.rollup_retention_days} days`],
          ]}
          note="Request rows answer per-client questions and are large; rollups answer every chart and are three orders of magnitude smaller. That difference is why the two retentions differ."
        />
        <Group
          title="Thresholds"
          rows={[
            ['Critical error rate', `${(settings.error_rate_critical * 100).toFixed(1)}%`],
            ['Critical P95', `${settings.latency_p95_critical_ms}ms`],
          ]}
          note="What counts as bad is deployment-specific: an internal batch API and a public checkout do not share a definition, so these are configuration rather than constants."
        />
      </div>

      <Panel className="mt-4">
        <p className="text-[12.5px] text-ink-muted">
          Secrets are never shown here. The screen reports whether{' '}
          <span className="mono">SECRET_KEY</span> is still the development default, which is the
          only thing about it an operator needs from this page.
        </p>
      </Panel>
    </>
  )
}

function Group({
  title,
  rows,
  note,
}: {
  title: string
  rows: [string, React.ReactNode][]
  note?: string
}) {
  return (
    <Panel padded={false}>
      <PanelHeader title={title} />
      <dl className="divide-y divide-line">
        {rows.map(([label, value]) => (
          <div key={label} className="flex items-center justify-between gap-4 px-5 py-2.5">
            <dt className="text-[12.5px] text-ink-muted">{label}</dt>
            <dd className="mono max-w-[60%] truncate text-right text-[12.5px]">{value}</dd>
          </div>
        ))}
      </dl>
      {note && <p className="border-t border-line px-5 py-3 text-[11.5px] text-ink-faint">{note}</p>}
    </Panel>
  )
}
