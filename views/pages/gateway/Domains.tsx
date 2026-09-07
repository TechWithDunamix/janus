import { Head, useForm } from '@inertiajs/react'
import { useState } from 'react'
import { bytes, count, dateTime, useCan } from '@/js/hooks'
import {
  Badge, Button, Empty, Field, Input, Modal, PageHeader, Panel, Select,
  Table, TBody, TD, TH, THead, TR,
} from '@/views/ui/kit'

type Domain = {
  id: number; hostname: string; tls_mode: string; tls_status: string
  tls_issuer: string | null; tls_expires_at: string | null
  enabled: boolean; route_count: number
}

const TLS_TONE: Record<string, 'positive' | 'neutral' | 'caution'> = {
  issued: 'positive',
  unknown: 'neutral',
  pending: 'caution',
}

export default function Domains({
  domains,
  traffic,
}: {
  domains: Domain[]
  traffic?: Record<string, { requests: number; bytes_out: number }>
}) {
  const can = useCan()
  const [adding, setAdding] = useState(false)

  return (
    <>
      <Head title="Domains" />
      <PageHeader
        title="Domains"
        description="Hostnames the gateway answers on, and how TLS is provisioned for each."
        actions={
          can('domains.write') && (
            <Button variant="primary" size="sm" onClick={() => setAdding(true)}>
              Add domain
            </Button>
          )
        }
      />

      <Panel padded={false}>
        {domains.length === 0 ? (
          <Empty
            title="No domains yet"
            body="Without a domain, routes match every host the gateway serves."
          />
        ) : (
          <Table>
            <THead>
              <TR>
                <TH>Hostname</TH>
                <TH>TLS</TH>
                <TH>Issuer</TH>
                <TH>Expires</TH>
                <TH align="right">Routes</TH>
                <TH align="right">Requests (24h)</TH>
                <TH align="right">Bandwidth</TH>
              </TR>
            </THead>
            <TBody>
              {domains.map((domain) => {
                const stats = traffic?.[domain.hostname]
                return (
                  <TR key={domain.id} className={domain.enabled ? '' : 'opacity-55'}>
                    <TD><span className="mono font-medium">{domain.hostname}</span></TD>
                    <TD>
                      <Badge tone={domain.tls_mode === 'off' ? 'neutral' : TLS_TONE[domain.tls_status] ?? 'neutral'}>
                        {domain.tls_mode === 'off' ? 'plain HTTP' : `${domain.tls_mode} · ${domain.tls_status}`}
                      </Badge>
                    </TD>
                    <TD className="text-ink-muted">{domain.tls_issuer ?? '—'}</TD>
                    <TD className="text-ink-muted">{dateTime(domain.tls_expires_at)}</TD>
                    <TD align="right" className="num">{domain.route_count}</TD>
                    <TD align="right" className="num">{stats ? count(stats.requests) : '—'}</TD>
                    <TD align="right" className="num text-ink-muted">{stats ? bytes(stats.bytes_out) : '—'}</TD>
                  </TR>
                )
              })}
            </TBody>
          </Table>
        )}
      </Panel>

      <p className="mt-3 text-[12px] text-ink-faint">
        TLS mode decides automatic HTTPS. With every domain set to <span className="mono">off</span>,
        the gateway speaks plain HTTP — which is what a laptop or a gateway behind a TLS-terminating
        load balancer wants.
      </p>

      {adding && <AddModal onClose={() => setAdding(false)} />}
    </>
  )
}

function AddModal({ onClose }: { onClose: () => void }) {
  const form = useForm({ id: 0, hostname: '', tls_mode: 'auto', enabled: true })
  return (
    <Modal open title="Add a domain" onClose={onClose}>
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault()
          form.post('/domains/save', { onSuccess: onClose })
        }}
      >
        <Field label="Hostname" required>
          <Input
            placeholder="api.example.com"
            value={form.data.hostname}
            onChange={(e) => form.setData('hostname', e.target.value)}
            required
            autoFocus
          />
        </Field>
        <Field
          label="TLS"
          hint="`auto` provisions a public certificate. `internal` uses Caddy's local CA, which is right for a private network. `off` serves plain HTTP."
        >
          <Select value={form.data.tls_mode} onChange={(e) => form.setData('tls_mode', e.target.value)}>
            <option value="auto">Automatic (public CA)</option>
            <option value="internal">Internal (Caddy local CA)</option>
            <option value="custom">Custom certificate</option>
            <option value="off">Off (plain HTTP)</option>
          </Select>
        </Field>
        <div className="flex justify-end gap-2 pt-2">
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" loading={form.processing}>Add domain</Button>
        </div>
      </form>
    </Modal>
  )
}
