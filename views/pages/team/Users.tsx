/**
 * Team management.
 *
 * Three things on one screen because they are one question — who has access —
 * asked at three timescales: the accounts that exist, the sessions that are
 * live right now, and the sign-in attempts that led to them.
 */

import { Head, router, useForm } from '@inertiajs/react'
import { useState } from 'react'
import { ago, dateTime, useAuth, useCan } from '@/js/hooks'
import {
  Badge, Button, Empty, Field, Input, Modal, PageHeader, Panel, PanelHeader, Select,
  Skeleton, Tabs, Table, TBody, TD, TH, THead, TR,
} from '@/views/ui/kit'

type User = {
  id: number; email: string; name: string; title: string | null
  is_active: boolean; is_superuser: boolean; roles: string[]
  pending_invite: boolean; disabled_reason: string | null; last_login: string | null
}

type Session = {
  id: number; user: string | null; kind: string; ip: string | null
  user_agent: string | null; is_live: boolean
  created_at: string; last_seen_at: string | null; expires_at: string | null
}

type Login = {
  id: number; email: string; successful: boolean; reason: string
  kind: string; ip: string | null; created_at: string
}

export default function Users({
  users,
  roles,
  sessions,
  logins,
}: {
  users: User[]
  roles: { id: number; name: string; description: string | null }[]
  sessions?: Session[]
  logins?: Login[]
}) {
  const can = useCan()
  const me = useAuth()
  const [tab, setTab] = useState('users')
  const [inviting, setInviting] = useState(false)

  return (
    <>
      <Head title="Users" />
      <PageHeader
        title="Team"
        description="Accounts, live sessions and sign-in activity."
        actions={
          can('users.write') && (
            <Button variant="primary" size="sm" onClick={() => setInviting(true)}>
              Invite user
            </Button>
          )
        }
      />

      <Tabs
        tabs={[
          { key: 'users', label: 'Users', count: users.length },
          { key: 'sessions', label: 'Sessions', count: sessions?.filter((s) => s.is_live).length },
          { key: 'activity', label: 'Sign-in activity' },
        ]}
        active={tab}
        onSelect={setTab}
      />

      <div className="mt-4">
        {tab === 'users' && (
          <Panel padded={false}>
            <Table>
              <THead>
                <TR>
                  <TH>User</TH>
                  <TH>Role</TH>
                  <TH>State</TH>
                  <TH align="right">Last sign-in</TH>
                  <TH align="right" />
                </TR>
              </THead>
              <TBody>
                {users.map((user) => (
                  <TR key={user.id} className={user.is_active ? '' : 'opacity-60'}>
                    <TD>
                      <div className="font-medium">{user.name}</div>
                      <div className="text-[11.5px] text-ink-faint">
                        {user.email}
                        {user.title && ` · ${user.title}`}
                      </div>
                    </TD>
                    <TD>
                      {can('roles.write') && user.id !== me.user?.id ? (
                        <Select
                          value={user.roles[0] ?? ''}
                          onChange={(event) =>
                            router.post('/team/users/role', { id: user.id, role: event.target.value })
                          }
                          className="w-[150px]"
                        >
                          <option value="">No role</option>
                          {roles.map((role) => (
                            <option key={role.id} value={role.name}>{role.name}</option>
                          ))}
                        </Select>
                      ) : (
                        <Badge tone="neutral">{user.roles[0] ?? 'none'}</Badge>
                      )}
                    </TD>
                    <TD>
                      {user.is_superuser && <Badge tone="brand">owner</Badge>}
                      {user.pending_invite && <Badge tone="caution">invited</Badge>}
                      {!user.is_active && (
                        <Badge tone="critical">disabled</Badge>
                      )}
                      {user.is_active && !user.pending_invite && !user.is_superuser && (
                        <Badge tone="positive">active</Badge>
                      )}
                      {user.disabled_reason && (
                        <div className="mt-0.5 text-[11px] text-ink-faint">{user.disabled_reason}</div>
                      )}
                    </TD>
                    <TD align="right" className="text-ink-muted">{ago(user.last_login)}</TD>
                    <TD align="right">
                      {can('users.write') && user.id !== me.user?.id && (
                        <Button
                          size="xs"
                          variant="ghost"
                          onClick={() => router.post('/team/users/toggle', { id: user.id })}
                        >
                          {user.is_active ? 'Disable' : 'Enable'}
                        </Button>
                      )}
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </Panel>
        )}

        {tab === 'sessions' && (
          <Panel padded={false}>
            <PanelHeader
              title="Live sessions"
              description="Revoking takes effect on the session's next request, not when its cookie expires."
            />
            {!sessions ? (
              <div className="p-5"><Skeleton rows={5} /></div>
            ) : sessions.length === 0 ? (
              <Empty title="No live sessions" />
            ) : (
              <Table>
                <THead>
                  <TR>
                    <TH>User</TH>
                    <TH>Kind</TH>
                    <TH>Address</TH>
                    <TH>Started</TH>
                    <TH align="right">Last seen</TH>
                    <TH align="right" />
                  </TR>
                </THead>
                <TBody>
                  {sessions.map((session) => (
                    <TR key={session.id}>
                      <TD>{session.user ?? '—'}</TD>
                      <TD><Badge tone={session.kind === 'cli' ? 'brand' : 'neutral'}>{session.kind}</Badge></TD>
                      <TD><span className="mono">{session.ip ?? '—'}</span></TD>
                      <TD className="text-ink-muted">{dateTime(session.created_at)}</TD>
                      <TD align="right" className="text-ink-muted">{ago(session.last_seen_at)}</TD>
                      <TD align="right">
                        {can('users.write') && (
                          <Button
                            size="xs"
                            variant="ghost"
                            onClick={() => router.post('/team/sessions/revoke', { id: session.id })}
                          >
                            Revoke
                          </Button>
                        )}
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            )}
          </Panel>
        )}

        {tab === 'activity' && (
          <Panel padded={false}>
            <PanelHeader
              title="Sign-in activity"
              description="Every attempt, successful or not. Failed attempts in bursts are a security signal."
            />
            {!logins ? (
              <div className="p-5"><Skeleton rows={6} /></div>
            ) : (
              <Table>
                <THead>
                  <TR>
                    <TH>When</TH>
                    <TH>Address</TH>
                    <TH>Result</TH>
                    <TH>Via</TH>
                    <TH align="right">From</TH>
                  </TR>
                </THead>
                <TBody>
                  {logins.map((login) => (
                    <TR key={login.id}>
                      <TD className="text-ink-muted">{dateTime(login.created_at)}</TD>
                      <TD>{login.email}</TD>
                      <TD>
                        <Badge tone={login.successful ? 'positive' : 'critical'}>
                          {login.successful ? 'signed in' : login.reason.replace(/_/g, ' ')}
                        </Badge>
                      </TD>
                      <TD className="text-ink-muted">{login.kind}</TD>
                      <TD align="right"><span className="mono">{login.ip ?? '—'}</span></TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            )}
          </Panel>
        )}
      </div>

      {inviting && <InviteModal roles={roles} onClose={() => setInviting(false)} />}
    </>
  )
}

function InviteModal({
  roles,
  onClose,
}: {
  roles: { id: number; name: string }[]
  onClose: () => void
}) {
  const form = useForm({ email: '', full_name: '', title: '', role: 'Viewer' })
  return (
    <Modal open title="Invite a user" onClose={onClose}>
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault()
          form.post('/team/users/invite', { onSuccess: onClose })
        }}
      >
        <p className="text-[12.5px] text-ink-muted">
          The account is created with no usable password and cannot sign in until one is set. There
          is no temporary password: a temporary password is a real credential that gets emailed,
          reused and never rotated.
        </p>
        <Field label="Email" required>
          <Input type="email" value={form.data.email} onChange={(e) => form.setData('email', e.target.value)} required autoFocus />
        </Field>
        <Field label="Name">
          <Input value={form.data.full_name} onChange={(e) => form.setData('full_name', e.target.value)} />
        </Field>
        <Field label="Role">
          <Select value={form.data.role} onChange={(e) => form.setData('role', e.target.value)}>
            {roles.map((role) => (
              <option key={role.id} value={role.name}>{role.name}</option>
            ))}
          </Select>
        </Field>
        <div className="flex justify-end gap-2 pt-2">
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" loading={form.processing}>Invite</Button>
        </div>
      </form>
    </Modal>
  )
}
