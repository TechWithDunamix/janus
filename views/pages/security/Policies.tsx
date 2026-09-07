/**
 * Security policies.
 *
 * A closed vocabulary rather than an expression language: every policy reads
 * `IF subject operator value THEN action`. That is enough for the rules an
 * edge actually needs and small enough that the generator can prove what it
 * emits — a DSL here would put a parser between an operator and their gateway.
 */

import { Head, router, useForm } from '@inertiajs/react'
import { useState } from 'react'
import { count, useCan } from '@/js/hooks'
import {
  Badge, Button, Empty, Field, Input, Modal, PageHeader, Panel, Select,
  Table, TBody, TD, TH, THead, TR,
} from '@/views/ui/kit'

type Policy = {
  id: number; name: string; description: string | null
  subject: string; operator: string; value: string; action: string
  priority: number; enabled: boolean; sentence: string; match_count: number
  domain: string | null; route: string | null; rate_limit: string | null; scope: string
}

const ACTION_TONE: Record<string, 'critical' | 'positive' | 'caution' | 'neutral'> = {
  block: 'critical',
  allow: 'positive',
  rate_limit: 'caution',
  require_auth: 'caution',
  log: 'neutral',
}

export default function Policies({
  policies,
  routes,
  domains,
  rate_limits,
}: {
  policies: Policy[]
  routes: { id: number; name: string }[]
  domains: { id: number; hostname: string }[]
  rate_limits: { id: number; name: string; rate_label: string }[]
}) {
  const can = useCan()
  const [editing, setEditing] = useState<Policy | 'new' | null>(null)

  return (
    <>
      <Head title="Policies" />
      <PageHeader
        title="Security policies"
        description="Evaluated highest priority first, above the route table. An allow ordered above a block lets a specific caller through a broad rule."
        actions={
          can('security.write') && (
            <Button variant="primary" size="sm" onClick={() => setEditing('new')}>
              New policy
            </Button>
          )
        }
      />

      <Panel padded={false}>
        {policies.length === 0 ? (
          <Empty
            title="No policies"
            body="A policy is a condition and an action: block a path, allow a range, require a credential."
          />
        ) : (
          <Table>
            <THead>
              <TR>
                <TH align="right">Priority</TH>
                <TH>Policy</TH>
                <TH>Rule</TH>
                <TH>Action</TH>
                <TH>Scope</TH>
                <TH align="right">Matches</TH>
                <TH align="right" />
              </TR>
            </THead>
            <TBody>
              {policies.map((policy) => (
                <TR key={policy.id} className={policy.enabled ? '' : 'opacity-55'}>
                  <TD align="right" className="num text-ink-faint">{policy.priority}</TD>
                  <TD>
                    <div className="font-medium">{policy.name}</div>
                    {policy.description && (
                      <div className="text-[11.5px] text-ink-faint">{policy.description}</div>
                    )}
                  </TD>
                  <TD><span className="mono text-[12px]">{policy.sentence}</span></TD>
                  <TD>
                    <Badge tone={ACTION_TONE[policy.action] ?? 'neutral'}>{policy.action}</Badge>
                    {policy.rate_limit && (
                      <span className="ml-1.5 text-[11.5px] text-ink-muted">{policy.rate_limit}</span>
                    )}
                  </TD>
                  <TD className="text-ink-muted">
                    {policy.route ?? policy.domain ?? 'global'}
                  </TD>
                  <TD align="right" className="num text-ink-muted">{count(policy.match_count)}</TD>
                  <TD align="right">
                    {can('security.write') && (
                      <div className="flex justify-end gap-1">
                        <Button size="xs" variant="ghost" onClick={() => setEditing(policy)}>Edit</Button>
                        <Button
                          size="xs"
                          variant="ghost"
                          onClick={() => {
                            if (confirm(`Delete policy "${policy.name}"?`)) {
                              router.post('/security/policies/delete', { id: policy.id })
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

      <p className="mt-3 text-[12px] text-ink-faint">
        Policies become matchers and handlers in the generated configuration. A policy the gateway
        cannot express is reported as a warning at generation time rather than emitted and rejected.
      </p>

      {editing && (
        <PolicyModal
          policy={editing === 'new' ? null : editing}
          routes={routes}
          domains={domains}
          rateLimits={rate_limits}
          onClose={() => setEditing(null)}
        />
      )}
    </>
  )
}

function PolicyModal({
  policy,
  routes,
  domains,
  rateLimits,
  onClose,
}: {
  policy: Policy | null
  routes: { id: number; name: string }[]
  domains: { id: number; hostname: string }[]
  rateLimits: { id: number; name: string; rate_label: string }[]
  onClose: () => void
}) {
  const form = useForm({
    id: policy?.id ?? 0,
    name: policy?.name ?? '',
    description: policy?.description ?? '',
    subject: policy?.subject ?? 'ip',
    operator: policy?.operator ?? 'equals',
    value: policy?.value ?? '',
    action: policy?.action ?? 'block',
    priority: policy?.priority ?? 0,
    domain_id: 0,
    route_id: 0,
    rate_limit_id: 0,
    enabled: policy?.enabled ?? true,
  })

  return (
    <Modal open title={policy ? `Edit “${policy.name}”` : 'New policy'} onClose={onClose}>
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault()
          form.post('/security/policies/save', { onSuccess: onClose })
        }}
      >
        <Field label="Name" required>
          <Input value={form.data.name} onChange={(e) => form.setData('name', e.target.value)} required autoFocus />
        </Field>

        <div className="rounded-md border border-line bg-sunken p-3">
          <p className="eyebrow mb-2">Rule</p>
          <div className="grid gap-2 sm:grid-cols-3">
            <Select value={form.data.subject} onChange={(e) => form.setData('subject', e.target.value)}>
              <option value="ip">IP</option>
              <option value="cidr">CIDR</option>
              <option value="client">Client</option>
              <option value="path">Path</option>
              <option value="method">Method</option>
              <option value="header">Header</option>
              <option value="api_key">API key</option>
            </Select>
            <Select value={form.data.operator} onChange={(e) => form.setData('operator', e.target.value)}>
              <option value="equals">equals</option>
              <option value="in_cidr">in CIDR</option>
              <option value="matches">matches</option>
              <option value="exceeds">exceeds</option>
            </Select>
            <Input
              className="font-mono"
              placeholder="value"
              value={form.data.value}
              onChange={(e) => form.setData('value', e.target.value)}
              required
            />
          </div>
          <p className="mt-2 text-[11.5px] text-ink-faint">
            THEN{' '}
            <span className="mono">{form.data.action}</span>
          </p>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Action">
            <Select value={form.data.action} onChange={(e) => form.setData('action', e.target.value)}>
              <option value="block">Block</option>
              <option value="allow">Allow</option>
              <option value="require_auth">Require authentication</option>
              <option value="rate_limit">Rate limit</option>
              <option value="log">Log only</option>
            </Select>
          </Field>
          <Field label="Priority" hint="Higher is evaluated first.">
            <Input type="number" value={form.data.priority} onChange={(e) => form.setData('priority', Number(e.target.value))} />
          </Field>
          {form.data.action === 'rate_limit' && (
            <Field label="Rate limit" className="sm:col-span-2">
              <Select value={form.data.rate_limit_id} onChange={(e) => form.setData('rate_limit_id', Number(e.target.value))}>
                <option value={0}>Select a limit</option>
                {rateLimits.map((limit) => (
                  <option key={limit.id} value={limit.id}>{limit.name} ({limit.rate_label})</option>
                ))}
              </Select>
            </Field>
          )}
          <Field label="Scope: domain">
            <Select value={form.data.domain_id} onChange={(e) => form.setData('domain_id', Number(e.target.value))}>
              <option value={0}>All domains</option>
              {domains.map((domain) => (
                <option key={domain.id} value={domain.id}>{domain.hostname}</option>
              ))}
            </Select>
          </Field>
          <Field label="Scope: route">
            <Select value={form.data.route_id} onChange={(e) => form.setData('route_id', Number(e.target.value))}>
              <option value={0}>All routes</option>
              {routes.map((route) => (
                <option key={route.id} value={route.id}>{route.name}</option>
              ))}
            </Select>
          </Field>
        </div>

        <div className="flex justify-end gap-2 pt-2">
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" loading={form.processing}>Save policy</Button>
        </div>
      </form>
    </Modal>
  )
}
