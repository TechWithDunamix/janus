/**
 * The allowlist and blocklist screens.
 *
 * One component for both, because they are the same rule with opposite verbs.
 * What differs is the copy, and the warning: adding an allow rule turns that
 * scope into deny-by-default for everything outside it, which is the single
 * most surprising thing in the whole security model and so is said on the
 * screen rather than in documentation.
 */

import { Head, router, useForm } from '@inertiajs/react'
import { useState } from 'react'
import { count, dateTime, useCan } from '@/js/hooks'
import {
  Badge, Banner, Button, Empty, Field, Input, Modal, PageHeader, Panel, PanelHeader,
  Select, Table, TBody, TD, TH, THead, TR,
} from './kit'

export type Rule = {
  id: number; action: string; cidr: string; reason: string | null; notes: string | null
  is_permanent: boolean; is_expired: boolean; expires_at: string | null
  created_at: string; scope: string
}

export type RuleStats = {
  blocked_requests: number
  rate_limited: number
  top_blocked: { client_ip: string; blocked: number }[]
}

export function RuleList({
  kind,
  rules,
  stats,
}: {
  kind: 'allow' | 'block'
  rules: Rule[]
  stats?: RuleStats
}) {
  const can = useCan()
  const [adding, setAdding] = useState(false)
  const blocking = kind === 'block'

  return (
    <>
      <Head title={blocking ? 'Blocklist' : 'Allowlist'} />
      <PageHeader
        title={blocking ? 'Blocklist' : 'Allowlist'}
        description={
          blocking
            ? 'Addresses and ranges the gateway refuses. Enforced by Caddy, not by Janus.'
            : 'Addresses and ranges exempt from block rules.'
        }
        actions={
          can('security.write') && (
            <Button variant="primary" size="sm" onClick={() => setAdding(true)}>
              {blocking ? 'Block an address' : 'Allow an address'}
            </Button>
          )
        }
      />

      {!blocking && rules.length > 0 && (
        <Banner tone="caution" title="An allowlist is deny-by-default" className="mb-4">
          Any allow rule at a scope makes that scope refuse everything outside the allowed ranges.
          On a public gateway that is usually not what is wanted — scope the rule to a route or a
          domain instead.
        </Banner>
      )}

      {blocking && stats && (
        <div className="mb-4 grid gap-3 sm:grid-cols-3">
          <Panel>
            <div className="eyebrow">Blocked requests (24h)</div>
            <div className="mt-1 num text-[18px] font-semibold">{count(stats.blocked_requests)}</div>
          </Panel>
          <Panel>
            <div className="eyebrow">Rate limited (24h)</div>
            <div className="mt-1 num text-[18px] font-semibold">{count(stats.rate_limited)}</div>
          </Panel>
          <Panel>
            <div className="eyebrow">Active rules</div>
            <div className="mt-1 num text-[18px] font-semibold">{count(rules.length)}</div>
          </Panel>
        </div>
      )}

      <Panel padded={false}>
        {rules.length === 0 ? (
          <Empty
            title={blocking ? 'Nothing is blocked' : 'Nothing is allowlisted'}
            body={
              blocking
                ? 'Block an address or a CIDR range to refuse it at the edge.'
                : 'Allowlisting exempts a range from block rules.'
            }
          />
        ) : (
          <Table>
            <THead>
              <TR>
                <TH>Range</TH>
                <TH>Scope</TH>
                <TH>Duration</TH>
                <TH>Reason</TH>
                <TH>Added</TH>
                <TH align="right" />
              </TR>
            </THead>
            <TBody>
              {rules.map((rule) => (
                <TR key={rule.id} className={rule.is_expired ? 'opacity-55' : ''}>
                  <TD><span className="mono font-medium">{rule.cidr}</span></TD>
                  <TD><Badge tone="neutral">{rule.scope}</Badge></TD>
                  <TD>
                    {rule.is_permanent ? (
                      <span className="text-ink-muted">permanent</span>
                    ) : (
                      <span className={rule.is_expired ? 'text-ink-faint' : 'text-caution'}>
                        {rule.is_expired ? 'expired' : `until ${dateTime(rule.expires_at)}`}
                      </span>
                    )}
                  </TD>
                  <TD className="max-w-[280px] truncate text-ink-muted">{rule.reason ?? '—'}</TD>
                  <TD className="text-ink-muted">{dateTime(rule.created_at)}</TD>
                  <TD align="right">
                    {can('security.write') && (
                      <Button
                        size="xs"
                        variant="ghost"
                        onClick={() => {
                          if (confirm(`Remove the ${rule.action} rule for ${rule.cidr}?`)) {
                            router.post('/security/rules/delete', { id: rule.id })
                          }
                        }}
                      >
                        Remove
                      </Button>
                    )}
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </Panel>

      <p className="mt-3 text-[12px] text-ink-faint">
        Rules are desired state. Deploy from Configuration to enforce them at the gateway.
        Temporary rules stop applying at the next deployment after they expire.
      </p>

      {blocking && stats && stats.top_blocked.length > 0 && (
        <Panel className="mt-4" padded={false}>
          <PanelHeader title="Most blocked clients (24h)" />
          <Table>
            <THead>
              <TR>
                <TH>Client</TH>
                <TH align="right">Blocked</TH>
              </TR>
            </THead>
            <TBody>
              {stats.top_blocked.map((row) => (
                <TR key={row.client_ip}>
                  <TD><span className="mono">{row.client_ip}</span></TD>
                  <TD align="right" className="num">{count(row.blocked)}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </Panel>
      )}

      {adding && <AddRule kind={kind} onClose={() => setAdding(false)} />}
    </>
  )
}

function AddRule({ kind, onClose }: { kind: 'allow' | 'block'; onClose: () => void }) {
  const form = useForm({ action: kind, cidr: '', reason: '', duration_minutes: 0 })
  return (
    <Modal open title={kind === 'block' ? 'Block an address' : 'Allow an address'} onClose={onClose}>
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault()
          form.post('/security/rules/save', { onSuccess: onClose })
        }}
      >
        <Field label="Address or CIDR" hint="A single address is stored as a /32. 10.0.0.0/8 and 203.0.113.42 are both fine." required>
          <Input
            placeholder="203.0.113.42"
            className="font-mono"
            value={form.data.cidr}
            onChange={(e) => form.setData('cidr', e.target.value)}
            required
            autoFocus
          />
        </Field>
        <Field label="Reason" hint="Recorded in the audit log.">
          <Input value={form.data.reason} onChange={(e) => form.setData('reason', e.target.value)} />
        </Field>
        {kind === 'block' && (
          <Field label="Duration" hint="A temporary block stops applying at the first deployment after it expires.">
            <Select
              value={form.data.duration_minutes}
              onChange={(e) => form.setData('duration_minutes', Number(e.target.value))}
            >
              <option value={0}>Permanent</option>
              <option value={15}>15 minutes</option>
              <option value={60}>1 hour</option>
              <option value={360}>6 hours</option>
              <option value={1440}>24 hours</option>
              <option value={10080}>7 days</option>
            </Select>
          </Field>
        )}
        <div className="flex justify-end gap-2 pt-2">
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant={kind === 'block' ? 'danger' : 'primary'} loading={form.processing}>
            {kind === 'block' ? 'Block' : 'Allow'}
          </Button>
        </div>
      </form>
    </Modal>
  )
}
