/**
 * The security overview.
 *
 * Split deliberately in two halves. **Alerts** are thresholds someone chose
 * being crossed; **anomalies** are Janus observing that the last few minutes do
 * not look like the preceding hour. The second is often nothing at all, so it
 * is presented as an observation with its baseline and deviation on show,
 * never as an incident — a dashboard that cries wolf is one people stop reading.
 */

import { Head } from '@inertiajs/react'
import { ago, count } from '@/js/hooks'
import type { Alert, Anomaly, Range, SeriesPoint } from '@/js/types'
import { AnalyticsHeader, TrafficChart } from '@/views/ui/analytics'
import { Badge, Empty, LinkButton, Panel, PanelHeader, Skeleton, Table, TBody, TD, TH, THead, TR } from '@/views/ui/kit'

type Summary = {
  blocked: number; rate_limited: number; unauthorized: number; failed_logins: number
  active_blocks: number; temporary_blocks: number; allowlisted: number
  policies: number; open_alerts: number; unreviewed_anomalies: number
}

export default function SecurityOverview({
  range,
  summary,
  series,
  alerts,
  anomalies,
  top_blocked,
}: {
  range: Range
  summary: Summary
  series?: SeriesPoint[]
  alerts?: Alert[]
  anomalies?: Anomaly[]
  top_blocked?: { client_ip: string; blocked: number }[]
}) {
  return (
    <>
      <Head title="Security" />
      <AnalyticsHeader title="Security" description="What the edge refused, and why" range={range} />

      <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <Tile label="Blocked requests" value={count(summary.blocked)} tone={summary.blocked > 0} />
        <Tile label="Rate limited" value={count(summary.rate_limited)} />
        <Tile label="Failed logins" value={count(summary.failed_logins)} tone={summary.failed_logins > 10} />
        <Tile label="Active block rules" value={`${summary.active_blocks} (${summary.temporary_blocks} temporary)`} />
        <Tile label="Allowlisted ranges" value={count(summary.allowlisted)} />
      </div>

      <Panel padded={false}>
        <PanelHeader title="Refusals over time" description="Blocked and rate-limited requests against total traffic." />
        <div className="p-5">
          <TrafficChart series={series} kind="security" height={280} />
        </div>
      </Panel>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Panel padded={false}>
          <PanelHeader
            title="Open alerts"
            description="Thresholds that are currently being crossed."
          />
          {!alerts ? (
            <div className="p-5"><Skeleton rows={4} /></div>
          ) : alerts.length === 0 ? (
            <Empty title="No open alerts" body="Nothing has crossed a configured threshold." />
          ) : (
            <ul className="divide-y divide-line">
              {alerts.map((alert) => (
                <li key={alert.id} className="flex gap-3 px-5 py-3">
                  <span className={`dot mt-1.5 ${alert.severity === 'critical' ? 'bg-critical' : 'bg-caution'}`} />
                  <div className="min-w-0 flex-1">
                    <p className="text-[12.5px] font-medium">{alert.title}</p>
                    {alert.detail && <p className="mt-0.5 text-[11.5px] text-ink-muted">{alert.detail}</p>}
                    {alert.observed !== null && alert.threshold !== null && (
                      <p className="mt-1 num text-[11px] text-ink-faint">
                        observed {alert.observed.toFixed(1)} · threshold {alert.threshold.toFixed(1)}
                      </p>
                    )}
                  </div>
                  <span className="shrink-0 text-[11px] text-ink-faint">{ago(alert.opened_at)}</span>
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <Panel padded={false}>
          <PanelHeader
            title="Anomalies"
            description="Statistical outliers against the preceding hour. Not incidents — a verdict is a human decision."
          />
          {!anomalies ? (
            <div className="p-5"><Skeleton rows={4} /></div>
          ) : anomalies.length === 0 ? (
            <Empty title="Nothing unusual" body="Recent traffic matches its baseline." />
          ) : (
            <ul className="divide-y divide-line">
              {anomalies.map((anomaly) => (
                <li key={anomaly.id} className="px-5 py-3">
                  <div className="flex items-center gap-2">
                    <Badge tone="neutral">{anomaly.kind.replace(/_/g, ' ')}</Badge>
                    <span className="num text-[11.5px] text-ink-faint">
                      {anomaly.deviation.toFixed(1)}σ from baseline
                    </span>
                    <span className="ml-auto text-[11px] text-ink-faint">{ago(anomaly.detected_at)}</span>
                  </div>
                  <p className="mt-1 text-[12px] text-ink-muted">{anomaly.detail}</p>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>

      <Panel className="mt-4" padded={false}>
        <PanelHeader
          title="Most blocked clients"
          description="Clients receiving 403s in this range."
          action={<LinkButton href="/security/blocklist" size="xs" variant="ghost">Blocklist</LinkButton>}
        />
        {!top_blocked ? (
          <div className="p-5"><Skeleton rows={4} /></div>
        ) : top_blocked.length === 0 ? (
          <Empty title="Nothing has been blocked" />
        ) : (
          <Table>
            <THead>
              <TR>
                <TH>Client</TH>
                <TH align="right">Blocked requests</TH>
              </TR>
            </THead>
            <TBody>
              {top_blocked.map((row) => (
                <TR key={row.client_ip}>
                  <TD><span className="mono">{row.client_ip}</span></TD>
                  <TD align="right" className="num">{count(row.blocked)}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </Panel>
    </>
  )
}

function Tile({ label, value, tone }: { label: string; value: string; tone?: boolean }) {
  return (
    <Panel>
      <div className="eyebrow">{label}</div>
      <div className={`mt-1 num text-[18px] font-semibold ${tone ? 'text-critical' : ''}`}>{value}</div>
    </Panel>
  )
}
