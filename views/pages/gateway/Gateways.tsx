import { Head, router, useForm } from '@inertiajs/react'
import { useState } from 'react'
import { ago, count, useCan } from '@/js/hooks'
import { SyncBadge } from '@/views/ui/gateway'
import {
  Button, Empty, Field, Input, Modal, PageHeader, Panel,
  Table, TBody, TD, TH, THead, TR,
} from '@/views/ui/kit'

type Row = {
  id: number; name: string; slug: string; region: string | null; listen: string
  enabled: boolean; maintenance: boolean; sync_state: 'SYNCED' | 'SYNCING' | 'FAILED' | 'DRIFTED'
  sync_error: string | null; simulated: boolean; caddy_version: string | null
  admin_url: string | null; last_synced_at: string | null; routes: number; upstreams: number
}

export default function Gateways({ gateways }: { gateways: Row[] }) {
  const can = useCan()
  const [creating, setCreating] = useState(false)

  return (
    <>
      <Head title="Gateways" />
      <PageHeader
        title="Gateways"
        description="The Caddy instances Janus manages."
        actions={
          can('gateway.write') && (
            <Button variant="primary" size="sm" onClick={() => setCreating(true)}>
              Register gateway
            </Button>
          )
        }
      />

      <Panel padded={false}>
        {gateways.length === 0 ? (
          <Empty
            title="No gateways yet"
            body="Register a Caddy instance to start managing routes."
          />
        ) : (
          <Table>
            <THead>
              <TR>
                <TH>Gateway</TH>
                <TH>Listen</TH>
                <TH>Caddy</TH>
                <TH align="right">Routes</TH>
                <TH align="right">Upstreams</TH>
                <TH>Sync</TH>
                <TH align="right">Last synced</TH>
                <TH align="right" />
              </TR>
            </THead>
            <TBody>
              {gateways.map((gateway) => (
                <TR key={gateway.id}>
                  <TD>
                    <div className="font-medium">{gateway.name}</div>
                    <div className="text-[11.5px] text-ink-faint">
                      {gateway.region ?? 'no region'}
                      {gateway.simulated && ' · simulated'}
                    </div>
                  </TD>
                  <TD><span className="mono">{gateway.listen}</span></TD>
                  <TD className="text-ink-muted">{gateway.caddy_version ?? '—'}</TD>
                  <TD align="right" className="num">{count(gateway.routes)}</TD>
                  <TD align="right" className="num">{count(gateway.upstreams)}</TD>
                  <TD>
                    <SyncBadge state={gateway.sync_state} />
                    {gateway.sync_error && (
                      <div className="mt-1 max-w-[280px] text-[11px] text-critical">
                        {gateway.sync_error}
                      </div>
                    )}
                  </TD>
                  <TD align="right" className="text-ink-muted">{ago(gateway.last_synced_at)}</TD>
                  <TD align="right">
                    {can('gateway.write') && (
                      <Button
                        size="xs"
                        variant={gateway.maintenance ? 'danger' : 'ghost'}
                        onClick={() =>
                          router.post('/gateways/maintenance', {
                            id: gateway.id,
                            maintenance: !gateway.maintenance,
                          })
                        }
                      >
                        {gateway.maintenance ? 'End maintenance' : 'Maintenance'}
                      </Button>
                    )}
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </Panel>

      {gateways.some((g) => g.maintenance) && (
        <p className="mt-3 text-[12px] text-caution">
          A gateway in maintenance answers every route with its maintenance response. The routes
          stay defined, so ending maintenance restores service without redeploying them.
        </p>
      )}

      {creating && <CreateModal onClose={() => setCreating(false)} />}
    </>
  )
}

function CreateModal({ onClose }: { onClose: () => void }) {
  const form = useForm({ name: '', slug: '', listen: ':8080', admin_url: '', region: '', enabled: true })
  return (
    <Modal open title="Register a gateway" onClose={onClose}>
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault()
          form.post('/gateways/save', { onSuccess: onClose })
        }}
      >
        <Field label="Name">
          <Input value={form.data.name} onChange={(e) => form.setData('name', e.target.value)} required autoFocus />
        </Field>
        <Field label="Listen address" hint="What Caddy's Janus-owned server listens on.">
          <Input value={form.data.listen} onChange={(e) => form.setData('listen', e.target.value)} />
        </Field>
        <Field
          label="Admin API"
          hint="Leave blank to use the global CADDY_ADMIN_URL. Janus only ever writes the server and logger it owns."
        >
          <Input
            placeholder="http://localhost:2019"
            value={form.data.admin_url}
            onChange={(e) => form.setData('admin_url', e.target.value)}
          />
        </Field>
        <Field label="Region" hint="Free text, shown in the picker.">
          <Input value={form.data.region} onChange={(e) => form.setData('region', e.target.value)} />
        </Field>
        <div className="flex justify-end gap-2 pt-2">
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" loading={form.processing}>Register</Button>
        </div>
      </form>
    </Modal>
  )
}
