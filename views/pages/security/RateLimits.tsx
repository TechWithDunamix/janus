import { Head, router, useForm } from '@inertiajs/react'
import { useState } from 'react'
import { count, useCan } from '@/js/hooks'
import type { Range, RouteRow, SeriesPoint } from '@/js/types'
import { TrafficChart } from '@/views/ui/analytics'
import { RangePicker } from '@/views/ui/RangePicker'
import {
  Badge, Banner, Button, Empty, Field, Input, Modal, PageHeader, Panel, PanelHeader,
  Select, Skeleton, Table, TBody, TD, TH, THead, TR,
} from '@/views/ui/kit'

type Limit = {
  id: number; name: string; key: string; limit: number; window_seconds: number
  burst: number; rate_label: string; enabled: boolean; response_status: number
  route: string | null; route_id: number | null
}

type Stats = {
  total: number
  series: SeriesPoint[]
  offenders: { client_ip: string; hits: number }[]
  routes: RouteRow[]
}

export default function RateLimits({
  range,
  limits,
  enforceable,
  stats,
}: {
  range: Range
  limits: Limit[]
  enforceable: boolean
  stats?: Stats
}) {
  const can = useCan()
  const [editing, setEditing] = useState<Limit | 'new' | null>(null)

  return (
    <>
      <Head title="Rate limits" />
      <PageHeader
        title="Rate limits"
        description="Ceilings applied per key, per window, at the edge."
        actions={
          <div className="flex items-center gap-2">
            <RangePicker current={range.key} />
            {can('security.write') && (
              <Button variant="primary" size="sm" onClick={() => setEditing('new')}>
                New limit
              </Button>
            )}
          </div>
        }
      />

      {/* Said plainly rather than discovered when a deployment warns. Stock
          Caddy has no rate-limit handler; without the plugin these rules are
          recorded and displayed but not enforced, and claiming otherwise
          would be the worst kind of dashboard lie. */}
      {!enforceable && (
        <Banner tone="caution" title="This gateway cannot enforce rate limits" className="mb-4">
          The Caddy build behind this gateway has no <span className="mono">http.handlers.rate_limit</span>{' '}
          module, which ships in the <span className="mono">caddy-ratelimit</span> plugin rather than
          the standard binary. Limits below are stored and shown, and Janus will not emit a handler it
          knows would be rejected. Rebuild Caddy with the plugin to enforce them.
        </Banner>
      )}

      <div className="mb-4 grid gap-3 sm:grid-cols-3">
        <Panel>
          <div className="eyebrow">Rate limited (this range)</div>
          <div className="mt-1 num text-[18px] font-semibold">
            {stats ? count(stats.total) : '—'}
          </div>
        </Panel>
        <Panel>
          <div className="eyebrow">Rules defined</div>
          <div className="mt-1 num text-[18px] font-semibold">{limits.length}</div>
        </Panel>
        <Panel>
          <div className="eyebrow">Enforced</div>
          <div className={`mt-1 text-[18px] font-semibold ${enforceable ? 'text-ok' : 'text-caution'}`}>
            {enforceable ? 'Yes' : 'No module'}
          </div>
        </Panel>
      </div>

      <Panel padded={false}>
        <PanelHeader title="Rules" />
        {limits.length === 0 ? (
          <Empty title="No rate limits" body="A limit is a ceiling on requests per key per window." />
        ) : (
          <Table>
            <THead>
              <TR>
                <TH>Name</TH>
                <TH>Keyed by</TH>
                <TH>Rate</TH>
                <TH align="right">Burst</TH>
                <TH>Scope</TH>
                <TH>Response</TH>
                <TH align="right" />
              </TR>
            </THead>
            <TBody>
              {limits.map((limit) => (
                <TR key={limit.id} className={limit.enabled ? '' : 'opacity-55'}>
                  <TD className="font-medium">{limit.name}</TD>
                  <TD><Badge tone="neutral">{limit.key}</Badge></TD>
                  <TD><span className="num font-medium">{limit.rate_label}</span></TD>
                  <TD align="right" className="num text-ink-muted">{limit.burst || '—'}</TD>
                  <TD className="text-ink-muted">{limit.route ?? 'global'}</TD>
                  <TD className="num text-ink-muted">{limit.response_status}</TD>
                  <TD align="right">
                    {can('security.write') && (
                      <div className="flex justify-end gap-1">
                        <Button size="xs" variant="ghost" onClick={() => setEditing(limit)}>Edit</Button>
                        <Button
                          size="xs"
                          variant="ghost"
                          onClick={() => {
                            if (confirm(`Delete rate limit "${limit.name}"?`)) {
                              router.post('/security/rate-limits/delete', { id: limit.id })
                            }
                          }}
                        >
                          Delete
                        </Button>
                      </div>
                    )}
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </Panel>

      <Panel className="mt-4" padded={false}>
        <PanelHeader title="Violations over time" description="429 responses across the range." />
        <div className="p-5">
          <TrafficChart series={stats?.series} kind="security" height={240} />
        </div>
      </Panel>

      <Panel className="mt-4" padded={false}>
        <PanelHeader
          title="Top offenders"
          description="Clients receiving the most 429s — the ones repeatedly exceeding a limit."
        />
        {!stats ? (
          <div className="p-5"><Skeleton rows={4} /></div>
        ) : stats.offenders.length === 0 ? (
          <Empty title="No client has been rate limited" />
        ) : (
          <Table>
            <THead>
              <TR>
                <TH>Client</TH>
                <TH align="right">429s</TH>
              </TR>
            </THead>
            <TBody>
              {stats.offenders.map((row) => (
                <TR key={row.client_ip}>
                  <TD><span className="mono">{row.client_ip}</span></TD>
                  <TD align="right" className="num">{count(row.hits)}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </Panel>

      {editing && (
        <LimitModal
          limit={editing === 'new' ? null : editing}
          onClose={() => setEditing(null)}
        />
      )}
    </>
  )
}

function LimitModal({ limit, onClose }: { limit: Limit | null; onClose: () => void }) {
  const form = useForm({
    id: limit?.id ?? 0,
    name: limit?.name ?? '',
    key: limit?.key ?? 'ip',
    limit: limit?.limit ?? 100,
    window_seconds: limit?.window_seconds ?? 60,
    burst: limit?.burst ?? 0,
    response_status: limit?.response_status ?? 429,
    enabled: true,
  })

  return (
    <Modal open title={limit ? `Edit “${limit.name}”` : 'New rate limit'} onClose={onClose}>
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault()
          form.post('/security/rate-limits/save', { onSuccess: onClose })
        }}
      >
        <Field label="Name" required>
          <Input value={form.data.name} onChange={(e) => form.setData('name', e.target.value)} required autoFocus />
        </Field>
        <Field label="Keyed by" hint="What the counter is kept per.">
          <Select value={form.data.key} onChange={(e) => form.setData('key', e.target.value)}>
            <option value="ip">Client IP</option>
            <option value="api_key">API key</option>
            <option value="user">Authenticated user</option>
            <option value="route">Route</option>
            <option value="domain">Domain</option>
            <option value="global">Global</option>
          </Select>
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Requests">
            <Input type="number" min={1} value={form.data.limit} onChange={(e) => form.setData('limit', Number(e.target.value))} />
          </Field>
          <Field label="Per">
            <Select value={form.data.window_seconds} onChange={(e) => form.setData('window_seconds', Number(e.target.value))}>
              <option value={1}>second</option>
              <option value={60}>minute</option>
              <option value={3600}>hour</option>
              <option value={86400}>day</option>
            </Select>
          </Field>
          <Field label="Burst" hint="Momentary allowance above the sustained rate.">
            <Input type="number" min={0} value={form.data.burst} onChange={(e) => form.setData('burst', Number(e.target.value))} />
          </Field>
          <Field label="Response status">
            <Input type="number" value={form.data.response_status} onChange={(e) => form.setData('response_status', Number(e.target.value))} />
          </Field>
        </div>
        <div className="flex justify-end gap-2 pt-2">
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" loading={form.processing}>Save limit</Button>
        </div>
      </form>
    </Modal>
  )
}
