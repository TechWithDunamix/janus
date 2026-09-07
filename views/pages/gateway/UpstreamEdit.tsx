import { Head, router, useForm } from '@inertiajs/react'
import { useState } from 'react'
import { useCan } from '@/js/hooks'
import { Button, Checkbox, Field, Input, PageHeader, Panel, PanelHeader, Select } from '@/views/ui/kit'
import { IconClose, IconPlus } from '@/views/ui/icons'

type Target = { id?: number; dial: string; weight: number; enabled: boolean }

export default function UpstreamEdit({ upstream }: { upstream: Record<string, any> }) {
  const can = useCan()
  const readOnly = !can('upstreams.write')
  const editing = Boolean(upstream?.id)

  const [targets, setTargets] = useState<Target[]>(
    upstream?.targets?.length ? upstream.targets : [{ dial: '', weight: 1, enabled: true }],
  )

  const form = useForm({
    id: upstream?.id ?? 0,
    name: upstream?.name ?? '',
    description: upstream?.description ?? '',
    policy: upstream?.policy ?? 'weighted_round_robin',
    max_fails: upstream?.max_fails ?? 3,
    fail_duration_seconds: upstream?.fail_duration_seconds ?? 30,
    health_path: upstream?.health_path ?? '',
    health_interval_seconds: upstream?.health_interval_seconds ?? 30,
    health_timeout_seconds: upstream?.health_timeout_seconds ?? 5,
    health_expect_status: upstream?.health_expect_status ?? 200,
    dial_timeout_seconds: upstream?.dial_timeout_seconds ?? 10,
    max_connections: upstream?.max_connections ?? 0,
    enabled: upstream?.enabled ?? true,
    maintenance: upstream?.maintenance ?? false,
    targets: targets as any,
  })

  const totalWeight = targets.reduce((sum, t) => sum + (t.enabled ? Number(t.weight) || 0 : 0), 0) || 1

  return (
    <>
      <Head title={editing ? upstream.name : 'New upstream'} />
      <PageHeader
        breadcrumbs={[{ label: 'Upstreams', href: '/upstreams' }]}
        title={editing ? upstream.name : 'New upstream'}
        description="A named pool of backend servers, and how Caddy should treat them."
      />

      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault()
          // `transform` returns void in this version of the adapter, so it
          // is called for its effect and `post` is a separate statement.
          form.transform((data) => ({ ...data, targets }))
          form.post('/upstreams/save')
        }}
      >
        <Panel padded={false}>
          <PanelHeader title="Pool" />
          <div className="grid gap-4 p-5 sm:grid-cols-2">
            <Field label="Name" required>
              <Input value={form.data.name} onChange={(e) => form.setData('name', e.target.value)} required autoFocus disabled={readOnly} />
            </Field>
            <Field label="Load balancing" hint="Weighted round robin gives each target `weight` consecutive requests.">
              <Select value={form.data.policy} onChange={(e) => form.setData('policy', e.target.value)} disabled={readOnly}>
                <option value="weighted_round_robin">Weighted round robin</option>
                <option value="round_robin">Round robin</option>
                <option value="least_conn">Least connections</option>
                <option value="ip_hash">IP hash</option>
                <option value="random">Random</option>
                <option value="first">First available</option>
              </Select>
            </Field>
            <Field label="Description" className="sm:col-span-2">
              <Input value={form.data.description} onChange={(e) => form.setData('description', e.target.value)} disabled={readOnly} />
            </Field>
          </div>
        </Panel>

        <Panel padded={false}>
          <PanelHeader
            title="Targets"
            description="Weights are relative. The share each target receives is shown beside it."
            action={
              !readOnly && (
                <Button
                  size="xs"
                  onClick={() => setTargets([...targets, { dial: '', weight: 1, enabled: true }])}
                >
                  <IconPlus className="h-3.5 w-3.5" /> Add target
                </Button>
              )
            }
          />
          <div className="space-y-2 p-5">
            {targets.map((target, index) => (
              <div key={index} className="flex items-center gap-2">
                <div className="min-w-0 flex-1">
                  <Input
                    placeholder="10.0.0.11:8000"
                    className="font-mono"
                    value={target.dial}
                    disabled={readOnly}
                    onChange={(e) => {
                      const next = [...targets]
                      next[index] = { ...target, dial: e.target.value }
                      setTargets(next)
                    }}
                  />
                </div>
                <div className="w-20 shrink-0">
                  <Input
                    type="number"
                    min={1}
                    aria-label="Weight"
                    value={target.weight}
                    disabled={readOnly}
                    onChange={(e) => {
                      const next = [...targets]
                      next[index] = { ...target, weight: Number(e.target.value) }
                      setTargets(next)
                    }}
                  />
                </div>
                <span className="num w-12 shrink-0 text-right text-[12px] text-ink-faint">
                  {target.enabled ? `${Math.round((Number(target.weight) / totalWeight) * 100)}%` : '—'}
                </span>
                <Checkbox
                  checked={target.enabled}
                  disabled={readOnly}
                  onChange={(e) => {
                    const next = [...targets]
                    next[index] = { ...target, enabled: e.target.checked }
                    setTargets(next)
                  }}
                  label=""
                />
                {!readOnly && (
                  <Button
                    size="xs"
                    variant="ghost"
                    onClick={() => setTargets(targets.filter((_, i) => i !== index))}
                    aria-label="Remove target"
                  >
                    <IconClose className="h-3.5 w-3.5" />
                  </Button>
                )}
              </div>
            ))}
          </div>
        </Panel>

        <Panel padded={false}>
          <PanelHeader title="Health checks" description="Declared here, executed by Caddy." />
          <div className="grid gap-4 p-5 sm:grid-cols-2">
            <Field label="Active check path" hint="Empty disables active checking; passive still applies.">
              <Input placeholder="/healthz" value={form.data.health_path} onChange={(e) => form.setData('health_path', e.target.value)} disabled={readOnly} />
            </Field>
            <Field label="Check interval (s)">
              <Input type="number" value={form.data.health_interval_seconds} onChange={(e) => form.setData('health_interval_seconds', Number(e.target.value))} disabled={readOnly} />
            </Field>
            <Field label="Passive: max failures" hint="Failures inside the window before Caddy takes a target out.">
              <Input type="number" value={form.data.max_fails} onChange={(e) => form.setData('max_fails', Number(e.target.value))} disabled={readOnly} />
            </Field>
            <Field label="Failure window (s)">
              <Input type="number" value={form.data.fail_duration_seconds} onChange={(e) => form.setData('fail_duration_seconds', Number(e.target.value))} disabled={readOnly} />
            </Field>
            <Field label="Dial timeout (s)">
              <Input type="number" value={form.data.dial_timeout_seconds} onChange={(e) => form.setData('dial_timeout_seconds', Number(e.target.value))} disabled={readOnly} />
            </Field>
            <Field label="Max connections per host" hint="0 is unlimited, which suits a backend that manages its own concurrency.">
              <Input type="number" value={form.data.max_connections} onChange={(e) => form.setData('max_connections', Number(e.target.value))} disabled={readOnly} />
            </Field>
            <Checkbox checked={form.data.enabled} onChange={(e) => form.setData('enabled', e.target.checked)} label="Enabled" disabled={readOnly} />
            <Checkbox
              checked={form.data.maintenance}
              onChange={(e) => form.setData('maintenance', e.target.checked)}
              label="Draining"
              hint="Takes the whole pool out without deleting it. Routes answer 503."
              disabled={readOnly}
            />
          </div>
        </Panel>

        {!readOnly && (
          <div className="flex justify-end gap-2">
            <Button onClick={() => router.get('/upstreams')}>Cancel</Button>
            <Button type="submit" variant="primary" loading={form.processing}>
              {editing ? 'Save upstream' : 'Create upstream'}
            </Button>
          </div>
        )}
      </form>
    </>
  )
}
