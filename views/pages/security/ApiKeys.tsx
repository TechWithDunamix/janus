/**
 * API keys.
 *
 * The screen is built around one rule: **the secret exists once.** It is shown
 * in a panel immediately after creation or rotation and never again — there is
 * no code path in Janus that can produce it a second time, and the list shows
 * only the prefix. Everything about the layout follows from that: the reveal is
 * a modal that has to be dismissed deliberately, with a copy button, and a
 * warning that says plainly what happens if it is lost.
 */

import { Head, router, useForm, usePage } from '@inertiajs/react'
import { useState } from 'react'
import { ago, count, useCan, useCopy } from '@/js/hooks'
import {
  Badge, Button, Empty, Field, Input, Modal, PageHeader, Panel, Select,
  Table, TBody, TD, TH, THead, TR,
} from '@/views/ui/kit'
import { IconCheck, IconCopy } from '@/views/ui/icons'

type Key = {
  id: number; name: string; prefix: string; status: string; scopes: string[]
  enabled: boolean; client: string | null; rate_limit: string | null
  request_count: number; last_used_at: string | null; expires_at: string | null
  created_at: string; rotated_from_id: number | null
}

const STATUS_TONE: Record<string, 'positive' | 'neutral' | 'critical' | 'caution'> = {
  active: 'positive',
  disabled: 'neutral',
  revoked: 'critical',
  expired: 'caution',
}

export default function ApiKeys({
  keys,
  clients,
  rate_limits,
}: {
  keys: Key[]
  clients: { id: number; label: string }[]
  rate_limits: { id: number; name: string; rate_label: string }[]
}) {
  const can = useCan()
  const { flash } = usePage().props as { flash?: Record<string, string> }
  const [creating, setCreating] = useState(false)
  const [secret, setSecret] = useState<string | null>(flash?.secret ?? null)

  return (
    <>
      <Head title="API keys" />
      <PageHeader
        title="API keys"
        description="Keys issued to gateway consumers. Only a SHA-256 digest and a short prefix are stored."
        actions={
          can('apikeys.write') && (
            <Button variant="primary" size="sm" onClick={() => setCreating(true)}>
              Issue key
            </Button>
          )
        }
      />

      <Panel padded={false}>
        {keys.length === 0 ? (
          <Empty title="No keys issued" body="A key identifies a consumer at the edge and can carry scopes and a rate limit." />
        ) : (
          <Table>
            <THead>
              <TR>
                <TH>Name</TH>
                <TH>Key</TH>
                <TH>Status</TH>
                <TH>Scopes</TH>
                <TH>Limit</TH>
                <TH align="right">Requests</TH>
                <TH align="right">Last used</TH>
                <TH align="right" />
              </TR>
            </THead>
            <TBody>
              {keys.map((key) => (
                <TR key={key.id} className={key.status === 'active' ? '' : 'opacity-60'}>
                  <TD>
                    <div className="font-medium">{key.name}</div>
                    {key.client && <div className="text-[11.5px] text-ink-faint">{key.client}</div>}
                    {key.rotated_from_id && (
                      <div className="text-[11px] text-ink-faint">rotated from #{key.rotated_from_id}</div>
                    )}
                  </TD>
                  {/* The prefix, and only the prefix. */}
                  <TD><span className="mono">{key.prefix}…</span></TD>
                  <TD><Badge tone={STATUS_TONE[key.status] ?? 'neutral'}>{key.status}</Badge></TD>
                  <TD className="text-ink-muted">
                    {key.scopes.length ? key.scopes.join(', ') : '—'}
                  </TD>
                  <TD className="text-ink-muted">{key.rate_limit ?? '—'}</TD>
                  <TD align="right" className="num">{count(key.request_count)}</TD>
                  <TD align="right" className="text-ink-muted">{ago(key.last_used_at)}</TD>
                  <TD align="right">
                    {can('apikeys.write') && key.status === 'active' && (
                      <div className="flex justify-end gap-1">
                        <Button
                          size="xs"
                          variant="ghost"
                          onClick={() => {
                            if (confirm(`Rotate “${key.name}”? The current secret stops working immediately.`)) {
                              router.post('/security/api-keys/rotate', { id: key.id })
                            }
                          }}
                        >
                          Rotate
                        </Button>
                        <Button
                          size="xs"
                          variant="ghost"
                          onClick={() => {
                            if (confirm(`Revoke “${key.name}”? This cannot be undone.`)) {
                              router.post('/security/api-keys/revoke', { id: key.id })
                            }
                          }}
                        >
                          Revoke
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

      {creating && (
        <CreateModal clients={clients} rateLimits={rate_limits} onClose={() => setCreating(false)} />
      )}
      {secret && <SecretModal secret={secret} onClose={() => setSecret(null)} />}
    </>
  )
}

function CreateModal({
  clients,
  rateLimits,
  onClose,
}: {
  clients: { id: number; label: string }[]
  rateLimits: { id: number; name: string; rate_label: string }[]
  onClose: () => void
}) {
  const form = useForm({ name: '', scopes: '', client_id: 0, rate_limit_id: 0, expires_in_days: 0 })
  return (
    <Modal open title="Issue an API key" onClose={onClose}>
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault()
          form.post('/security/api-keys/create', { onSuccess: onClose })
        }}
      >
        <Field label="Name" hint="How the key is identified in lists and analytics." required>
          <Input value={form.data.name} onChange={(e) => form.setData('name', e.target.value)} required autoFocus />
        </Field>
        <Field label="Scopes" hint="Comma-separated. Matched by a route's `scope` auth policy.">
          <Input placeholder="products.read,orders.write" value={form.data.scopes} onChange={(e) => form.setData('scopes', e.target.value)} />
        </Field>
        <Field label="Client">
          <Select value={form.data.client_id} onChange={(e) => form.setData('client_id', Number(e.target.value))}>
            <option value={0}>Not linked</option>
            {clients.map((client) => (
              <option key={client.id} value={client.id}>{client.label}</option>
            ))}
          </Select>
        </Field>
        <Field label="Rate limit">
          <Select value={form.data.rate_limit_id} onChange={(e) => form.setData('rate_limit_id', Number(e.target.value))}>
            <option value={0}>None</option>
            {rateLimits.map((limit) => (
              <option key={limit.id} value={limit.id}>{limit.name} ({limit.rate_label})</option>
            ))}
          </Select>
        </Field>
        <Field label="Expires">
          <Select value={form.data.expires_in_days} onChange={(e) => form.setData('expires_in_days', Number(e.target.value))}>
            <option value={0}>Never</option>
            <option value={30}>30 days</option>
            <option value={90}>90 days</option>
            <option value={365}>1 year</option>
          </Select>
        </Field>
        <div className="flex justify-end gap-2 pt-2">
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" loading={form.processing}>Issue key</Button>
        </div>
      </form>
    </Modal>
  )
}

function SecretModal({ secret, onClose }: { secret: string; onClose: () => void }) {
  const [copied, copy] = useCopy()
  return (
    <Modal open title="Copy this secret now" onClose={onClose}>
      <div className="space-y-4">
        <p className="text-[12.5px] text-ink-muted">
          This is the only time the secret is shown. Janus stores a SHA-256 digest, not the key, so
          there is no way to retrieve it later — if it is lost the key has to be rotated.
        </p>
        <div className="flex items-center gap-2 rounded-md border border-line bg-sunken px-3 py-2.5">
          <code className="mono flex-1 break-all text-[12.5px]">{secret}</code>
          <Button size="xs" onClick={() => copy(secret)}>
            {copied ? <IconCheck className="h-3.5 w-3.5" /> : <IconCopy className="h-3.5 w-3.5" />}
            {copied ? 'Copied' : 'Copy'}
          </Button>
        </div>
        <div className="flex justify-end">
          <Button variant="primary" onClick={onClose}>I have saved it</Button>
        </div>
      </div>
    </Modal>
  )
}
