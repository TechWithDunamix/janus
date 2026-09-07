import { Head } from '@inertiajs/react'
import { dateTime } from '@/js/hooks'
import type { Deployment } from '@/js/types'
import { Badge, Empty, PageHeader, Panel, Table, TBody, TD, TH, THead, TR } from '@/views/ui/kit'

const STATUS_TONE: Record<string, 'positive' | 'critical' | 'caution' | 'neutral'> = {
  succeeded: 'positive',
  failed: 'critical',
  applying: 'caution',
  validating: 'caution',
  verifying: 'caution',
  pending: 'neutral',
}

export default function Deployments({ deployments }: { deployments: Deployment[] }) {
  return (
    <>
      <Head title="Deployments" />
      <PageHeader
        title="Deployments"
        description="Every attempt to put a version onto a gateway, including the ones that did not make it."
      />

      <Panel padded={false}>
        {deployments.length === 0 ? (
          <Empty title="No deployments yet" body="Deploy from the Configuration screen." />
        ) : (
          <Table>
            <THead>
              <TR>
                <TH>Started</TH>
                <TH>Version</TH>
                <TH>Status</TH>
                <TH>Summary</TH>
                <TH>Actor</TH>
                <TH>Via</TH>
                <TH align="right">Duration</TH>
              </TR>
            </THead>
            <TBody>
              {deployments.map((deployment) => (
                <TR key={deployment.id}>
                  <TD className="text-ink-muted">{dateTime(deployment.started_at)}</TD>
                  <TD><span className="num font-medium">v{deployment.version}</span></TD>
                  <TD>
                    <Badge tone={STATUS_TONE[deployment.status] ?? 'neutral'}>
                      {deployment.status}
                    </Badge>
                    {/* The stage a failure stopped at. A deployment that failed
                        at `validating` never touched the gateway; one that
                        failed at `applying` left the previous config running.
                        The distinction is the first thing to know. */}
                    {deployment.status === 'failed' && (
                      <span className="ml-1.5 text-[11px] text-ink-faint">at {deployment.stage}</span>
                    )}
                  </TD>
                  <TD className="max-w-[320px]">
                    <div className="truncate text-ink-muted">{deployment.summary ?? '—'}</div>
                    {deployment.error && (
                      <div className="mt-0.5 text-[11.5px] text-critical">{deployment.error}</div>
                    )}
                  </TD>
                  <TD>{deployment.actor}</TD>
                  <TD><Badge tone={deployment.origin === 'cli' ? 'brand' : 'neutral'}>{deployment.origin}</Badge></TD>
                  <TD align="right" className="num text-ink-muted">
                    {deployment.duration_ms !== null ? `${deployment.duration_ms}ms` : '—'}
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </Panel>
    </>
  )
}
