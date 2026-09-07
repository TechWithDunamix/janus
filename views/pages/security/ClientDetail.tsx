import { Head, router, useForm } from '@inertiajs/react'
import { useState } from 'react'
import { ago, bytes, count, dateTime, latency, useCan } from '@/js/hooks'
import type { Range } from '@/js/types'
import { RangePicker } from '@/views/ui/RangePicker'
import {
  Badge, Button, Empty, Field, Input, Modal, PageHeader, Panel, PanelHeader, Select,
  Skeleton, Table, TBody, TD, TH, THead, TR,
} from '@/views/ui/kit'

type Client = {
  id: number; kind: string; identifier: string; label: string | null; notes: string | null
  organisation: string | null; status: string; request_count: number; error_count: number
  blocked_count: number; rate_limited_count: number; bytes_out: number
  first_seen_at: string | null; last_seen_at: string | null; effective_status: string
}

type History = {
  by_status: { status: number; count: number }[]
  recent: { at: string; method: string; path: string; status: number; latency_ms: number; edge_action: string }[]
}

export default function ClientDetail({
  range,
  client,
  rules,
  history,
}: {
  range: Range
  client: Client
  rules: { id: number; action: string; cidr: string; reason: string | null; expires_at: string | null }[]
  history?: History
}) {
  const can = useCan()
  const [blocking, setBlocking] = useState(false)

  return (
    <>
      <Head title={client.identifier} />
      <PageHeader
        breadcrumbs={[{ label: 'Clients', href: '/security/clients' }]}
        title={client.identifier}
        description={client.label ?? `Seen since ${dateTime(client.first_seen_at)}`}
        actions={
          <div className="flex items-center gap-2">
            <RangePicker current={range.key} />
            {can('security.write') &&
              (client.effective_status === 'blocked' ? (
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => {
                    const rule = rules.find((r) => r.action === 'block')
                    if (rule && confirm(`Unblock ${client.identifier}?`)) {
                      router.post('/security/rules/delete', { id: rule.id })
                    }
                  }}
                >
                  Unblock
                </Button>
              ) : (
                <Button size="sm" variant="danger" onClick={() => setBlocking(true)}>
                  Block client
                </Button>
              ))}
          </div>
        }
      />

      <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <Tile label="State" value={<Badge tone={client.effective_status === 'blocked' ? 'critical' : client.effective_status === 'allowed' ? 'positive' : 'neutral'}>{client.effective_status}</Badge>} />
        <Tile label="Requests" value={count(client.request_count)} />
        <Tile label="Errors" value={count(client.error_count)} />
        <Tile label="Blocked" value={count(client.blocked_count)} />
        <Tile label="Bandwidth" value={bytes(client.bytes_out)} />
      </div>

      {rules.length > 0 && (
        <Panel className="mb-4" padded={false}>
          <PanelHeader title="Rules affecting this client" />
          <Table>
            <THead>
              <TR>
                <TH>Action</TH>
                <TH>Range</TH>
                <TH>Reason</TH>
                <TH>Expires</TH>
              </TR>
            </THead>
            <TBody>
              {rules.map((rule) => (
                <TR key={rule.id}>
                  <TD><Badge tone={rule.action === 'block' ? 'critical' : 'positive'}>{rule.action}</Badge></TD>
                  <TD><span className="mono">{rule.cidr}</span></TD>
                  <TD className="text-ink-muted">{rule.reason ?? '—'}</TD>
                  <TD className="text-ink-muted">{rule.expires_at ? dateTime(rule.expires_at) : 'permanent'}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </Panel>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        <Panel padded={false}>
          <PanelHeader title="Status codes" description="In this range." />
          {!history ? (
            <div className="p-5"><Skeleton rows={4} /></div>
          ) : history.by_status.length === 0 ? (
            <Empty title="No requests in range" />
          ) : (
            <Table>
              <THead>
                <TR>
                  <TH>Status</TH>
                  <TH align="right">Count</TH>
                </TR>
              </THead>
              <TBody>
                {history.by_status.map((row) => (
                  <TR key={row.status}>
                    <TD>
                      <span className={`mono ${row.status >= 500 ? 'text-critical' : row.status >= 400 ? 'text-caution' : ''}`}>
                        {row.status}
                      </span>
                    </TD>
                    <TD align="right" className="num">{count(row.count)}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          )}
        </Panel>

        <Panel className="lg:col-span-2" padded={false}>
          <PanelHeader
            title="Recent requests"
            description="Individual records, bounded by the retention window."
          />
          {!history ? (
            <div className="p-5"><Skeleton rows={6} /></div>
          ) : history.recent.length === 0 ? (
            <Empty title="No recent requests" />
          ) : (
            <Table>
              <THead>
                <TR>
                  <TH>When</TH>
                  <TH>Request</TH>
                  <TH align="right">Status</TH>
                  <TH align="right">Latency</TH>
                  <TH align="right">Edge</TH>
                </TR>
              </THead>
              <TBody>
                {history.recent.map((request, index) => (
                  <TR key={index}>
                    <TD className="text-ink-muted">{ago(request.at)}</TD>
                    <TD>
                      <span className="rounded-sm bg-sunken px-1.5 py-0.5 text-[10.5px] font-semibold uppercase text-ink-muted">
                        {request.method}
                      </span>
                      <span className="mono ml-2">{request.path}</span>
                    </TD>
                    <TD align="right">
                      <span className={`num ${request.status >= 500 ? 'text-critical' : request.status >= 400 ? 'text-caution' : ''}`}>
                        {request.status}
                      </span>
                    </TD>
                    <TD align="right" className="num text-ink-muted">{latency(request.latency_ms)}</TD>
                    <TD align="right">
                      {request.edge_action ? <Badge tone="caution">{request.edge_action}</Badge> : '—'}
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          )}
        </Panel>
      </div>

      {blocking && <BlockModal cidr={client.identifier} onClose={() => setBlocking(false)} />}
    </>
  )
}

function Tile({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <Panel>
      <div className="eyebrow">{label}</div>
      <div className="mt-1 num text-[16px] font-semibold">{value}</div>
    </Panel>
  )
}

function BlockModal({ cidr, onClose }: { cidr: string; onClose: () => void }) {
  const form = useForm({ action: 'block', cidr, reason: '', duration_minutes: 0 })
  return (
    <Modal open title={`Block ${cidr}`} onClose={onClose}>
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault()
          form.post('/security/rules/save', { onSuccess: onClose })
        }}
      >
        <Field label="Reason" hint="Recorded in the audit log.">
          <Input value={form.data.reason} onChange={(e) => form.setData('reason', e.target.value)} autoFocus />
        </Field>
        <Field label="Duration">
          <Select value={form.data.duration_minutes} onChange={(e) => form.setData('duration_minutes', Number(e.target.value))}>
            <option value={0}>Permanent</option>
            <option value={15}>15 minutes</option>
            <option value={60}>1 hour</option>
            <option value={360}>6 hours</option>
            <option value={1440}>24 hours</option>
          </Select>
        </Field>
        <div className="flex justify-end gap-2 pt-2">
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="danger" loading={form.processing}>Block</Button>
        </div>
      </form>
    </Modal>
  )
}
