import { Head, Link } from '@inertiajs/react'
import { ago, bytes, count } from '@/js/hooks'
import type { Range, RouteRow } from '@/js/types'
import { AnalyticsHeader, Breakdown, Section } from '@/views/ui/analytics'
import { Badge, Empty, Panel, PanelHeader, Skeleton, Table, TBody, TD, TH, THead, TR } from '@/views/ui/kit'

type Client = {
  ip: string
  requests: number
  bytes_out: number
  last_seen: string | null
  label: string | null
  status: string
  client_id: number | null
}

export default function Clients({
  range,
  clients,
  traffic,
}: {
  range: Range
  clients?: Client[]
  traffic?: {
    by_domain: { label: string; requests: number; bytes_out: number }[]
    by_method: { label: string; requests: number; bytes_out: number }[]
    by_route: RouteRow[]
  }
}) {
  return (
    <>
      <Head title="Clients" />
      <AnalyticsHeader title="Client analytics" description="Who the traffic came from" range={range} />

      <Panel padded={false}>
        <PanelHeader
          title="Clients by volume"
          description="From request-level records, so bounded by the retention window."
        />
        {!clients ? (
          <div className="p-5"><Skeleton rows={6} /></div>
        ) : clients.length === 0 ? (
          <Empty title="No client traffic recorded" />
        ) : (
          <Table>
            <THead>
              <TR>
                <TH>Client</TH>
                <TH>Label</TH>
                <TH align="right">Requests</TH>
                <TH align="right">Bandwidth</TH>
                <TH align="right">Last seen</TH>
                <TH align="right">State</TH>
              </TR>
            </THead>
            <TBody>
              {clients.map((client) => (
                <TR key={client.ip}>
                  <TD>
                    {client.client_id ? (
                      <Link href={`/security/clients/${client.client_id}`} className="mono hover:text-brand">
                        {client.ip}
                      </Link>
                    ) : (
                      <span className="mono">{client.ip}</span>
                    )}
                  </TD>
                  <TD className="text-ink-muted">{client.label ?? '—'}</TD>
                  <TD align="right" className="num">{count(client.requests)}</TD>
                  <TD align="right" className="num text-ink-muted">{bytes(client.bytes_out)}</TD>
                  <TD align="right" className="text-ink-muted">{ago(client.last_seen)}</TD>
                  <TD align="right">
                    <Badge tone={client.status === 'blocked' ? 'critical' : client.status === 'allowed' ? 'positive' : 'neutral'}>
                      {client.status}
                    </Badge>
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </Panel>

      <Section>
        <Breakdown title="By domain" rows={traffic?.by_domain} />
        <Breakdown title="By method" rows={traffic?.by_method} />
      </Section>
    </>
  )
}
