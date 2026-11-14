/**
 * Roles and the permission catalogue.
 *
 * A role is edited by ticking permissions, grouped exactly as the catalogue
 * groups them. The Owner role is shown read-only and says why: it is the
 * recovery path for an installation whose roles have been misconfigured, and a
 * control plane that lets you remove your own last way back in is one bad
 * afternoon from needing database access to fix.
 */

import { Head, useForm } from '@inertiajs/react'
import { useState } from 'react'
import { useCan } from '@/js/hooks'
import { Badge, Button, Checkbox, Field, Input, Modal, PageHeader, Panel, PanelHeader } from '@/views/ui/kit'

type Role = {
  id: number; name: string; description: string | null
  permissions: string[]; members: number; is_default: boolean
}

export default function Roles({
  roles,
  catalogue,
}: {
  roles: Role[]
  catalogue: { group: string; permissions: { name: string; description: string }[] }[]
}) {
  const can = useCan()
  const [editing, setEditing] = useState<Role | null>(null)
  const [creating, setCreating] = useState(false)

  return (
    <>
      <Head title="Roles" />
      <PageHeader
        title="Roles"
        description="Authorisation is enforced server-side on every route and every CLI command. Hiding a control in the interface is a convenience, never the gate."
        actions={
          can('roles.write') && (
            <Button variant="primary" size="sm" onClick={() => setCreating(true)}>
              New role
            </Button>
          )
        }
      />

      <div className="grid gap-3 lg:grid-cols-2">
        {roles.map((role) => (
          <Panel key={role.id} padded={false}>
            <div className="flex items-start justify-between gap-3 border-b border-line px-5 py-3.5">
              <div>
                <div className="flex items-center gap-2">
                  <h2 className="text-[13px] font-semibold">{role.name}</h2>
                  {role.is_default && <Badge tone="neutral">built in</Badge>}
                  {role.name === 'Owner' && <Badge tone="brand">recovery path</Badge>}
                </div>
                <p className="mt-0.5 text-[11.5px] text-ink-faint">{role.description}</p>
              </div>
              <div className="shrink-0 text-right">
                <div className="num text-[13px] font-semibold">{role.members}</div>
                <div className="text-[11px] text-ink-faint">members</div>
              </div>
            </div>

            <div className="px-5 py-3.5">
              <div className="mb-2 flex items-center justify-between">
                <span className="eyebrow">{role.permissions.length} permissions</span>
                {can('roles.write') && role.name !== 'Owner' && (
                  <Button size="xs" variant="ghost" onClick={() => setEditing(role)}>
                    Edit
                  </Button>
                )}
              </div>
              <div className="flex flex-wrap gap-1">
                {role.permissions.slice(0, 12).map((permission) => (
                  <span key={permission} className="mono rounded-sm bg-sunken px-1.5 py-0.5 text-[11px] text-ink-muted">
                    {permission}
                  </span>
                ))}
                {role.permissions.length > 12 && (
                  <span className="px-1.5 py-0.5 text-[11px] text-ink-faint">
                    +{role.permissions.length - 12} more
                  </span>
                )}
              </div>
            </div>
          </Panel>
        ))}
      </div>

      <Panel className="mt-4" padded={false}>
        <PanelHeader
          title="Permission catalogue"
          description="Every permission Janus recognises. A permission missing from a role is one that role's holders cannot use, on any surface."
        />
        <div className="grid gap-x-8 gap-y-5 p-5 sm:grid-cols-2">
          {catalogue.map((group) => (
            <div key={group.group}>
              <p className="eyebrow mb-2">{group.group}</p>
              <ul className="space-y-1.5">
                {group.permissions.map((permission) => (
                  <li key={permission.name} className="flex gap-2">
                    <span className="mono shrink-0 text-[11.5px] text-brand">{permission.name}</span>
                    <span className="text-[11.5px] text-ink-faint">{permission.description}</span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </Panel>

      {editing && <EditModal role={editing} catalogue={catalogue} onClose={() => setEditing(null)} />}
      {creating && <CreateModal onClose={() => setCreating(false)} />}
    </>
  )
}

function EditModal({
  role,
  catalogue,
  onClose,
}: {
  role: Role
  catalogue: { group: string; permissions: { name: string; description: string }[] }[]
  onClose: () => void
}) {
  const form = useForm<{ id: number; permissions: string[] }>({
    id: role.id,
    permissions: role.permissions,
  })

  const toggle = (name: string) => {
    form.setData(
      'permissions',
      form.data.permissions.includes(name)
        ? form.data.permissions.filter((p) => p !== name)
        : [...form.data.permissions, name],
    )
  }

  return (
    <Modal open width="lg" title={`Permissions for “${role.name}”`} onClose={onClose}>
      <form
        onSubmit={(event) => {
          event.preventDefault()
          form.post('/team/roles/permissions', { onSuccess: onClose })
        }}
      >
        <div className="max-h-[440px] space-y-5 overflow-y-auto pr-1">
          {catalogue.map((group) => (
            <div key={group.group}>
              <p className="eyebrow mb-2">{group.group}</p>
              <div className="space-y-1.5">
                {group.permissions.map((permission) => (
                  <Checkbox
                    key={permission.name}
                    checked={form.data.permissions.includes(permission.name)}
                    onChange={() => toggle(permission.name)}
                    label={permission.name}
                    hint={permission.description}
                  />
                ))}
              </div>
            </div>
          ))}
        </div>
        <div className="mt-4 flex items-center justify-end gap-2 border-t border-line pt-4">
          <span className="mr-auto text-[12px] text-ink-faint">
            {form.data.permissions.length} selected
          </span>
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" loading={form.processing}>Save permissions</Button>
        </div>
      </form>
    </Modal>
  )
}

function CreateModal({ onClose }: { onClose: () => void }) {
  const form = useForm({ name: '', description: '' })
  return (
    <Modal open title="New role" onClose={onClose}>
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault()
          form.post('/team/roles/save', { onSuccess: onClose })
        }}
      >
        <Field label="Name" required>
          <Input value={form.data.name} onChange={(e) => form.setData('name', e.target.value)} required autoFocus />
        </Field>
        <Field label="Description">
          <Input value={form.data.description} onChange={(e) => form.setData('description', e.target.value)} />
        </Field>
        <p className="text-[12px] text-ink-faint">
          The role starts with no permissions. Edit it after creating to grant them.
        </p>
        <div className="flex justify-end gap-2 pt-2">
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" loading={form.processing}>Create role</Button>
        </div>
      </form>
    </Modal>
  )
}
