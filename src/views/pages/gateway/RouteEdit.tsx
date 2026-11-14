/**
 * The route editor.
 *
 * Grouped by what the fields *do* rather than by which column they live in:
 * matching, then handling, then transformation, then reliability, then policy.
 * That ordering is the order a request passes through them, which is the only
 * ordering that makes the whole form explainable.
 */

import { Head, router, useForm } from '@inertiajs/react'
import { useCan } from '@/js/hooks'
import {
  Button, Checkbox, Field, Input, PageHeader, Panel, PanelHeader, Select, Textarea,
} from '@/views/ui/kit'

type Option = { id: number; name?: string; hostname?: string }

export default function RouteEdit({
  route,
  upstreams,
  domains,
}: {
  route: Record<string, any>
  upstreams: Option[]
  domains: Option[]
}) {
  const can = useCan()
  const editing = Boolean(route?.id)

  const form = useForm({
    id: route?.id ?? 0,
    name: route?.name ?? '',
    slug: route?.slug ?? '',
    description: route?.description ?? '',
    path: route?.path ?? '/*',
    methods: route?.methods ?? '',
    priority: route?.priority ?? 0,
    domain_id: route?.domain_id ?? 0,
    action: route?.action ?? 'proxy',
    upstream_id: route?.upstream_id ?? 0,
    canary_upstream_id: route?.canary_upstream_id ?? 0,
    canary_percent: route?.canary_percent ?? 0,
    redirect_to: route?.redirect_to ?? '',
    redirect_status: route?.redirect_status ?? 302,
    static_body: route?.static_body ?? '',
    static_status: route?.static_status ?? 200,
    strip_prefix: route?.strip_prefix ?? '',
    rewrite_to: route?.rewrite_to ?? '',
    timeout_seconds: route?.timeout_seconds ?? 30,
    read_timeout_seconds: route?.read_timeout_seconds ?? 0,
    retries: route?.retries ?? 0,
    auth_policy: route?.auth_policy ?? 'public',
    auth_requirement: route?.auth_requirement ?? '',
    enabled: route?.enabled ?? true,
    maintenance: route?.maintenance ?? false,
  })

  const readOnly = !can('routes.write')

  return (
    <>
      <Head title={editing ? route.name : 'New route'} />
      <PageHeader
        breadcrumbs={[{ label: 'Routes', href: '/routes' }]}
        title={editing ? route.name : 'New route'}
        description={editing ? `${route.method_label ?? 'ANY'} ${route.path}` : 'Define what to match and where to send it.'}
        actions={
          editing &&
          can('routes.write') && (
            <Button
              variant="danger"
              size="sm"
              onClick={() => {
                if (confirm(`Delete route ${route.name}?`)) {
                  router.post('/routes/delete', { id: route.id })
                }
              }}
            >
              Delete
            </Button>
          )
        }
      />

      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault()
          form.post('/routes/save')
        }}
      >
        <Panel padded={false}>
          <PanelHeader title="Matching" description="Which requests this route claims." />
          <div className="grid gap-4 p-5 sm:grid-cols-2">
            <Field label="Name" required>
              <Input value={form.data.name} onChange={(e) => form.setData('name', e.target.value)} required disabled={readOnly} />
            </Field>
            <Field label="Priority" hint="Higher wins. Caddy answers with the first matching route.">
              <Input type="number" value={form.data.priority} onChange={(e) => form.setData('priority', Number(e.target.value))} disabled={readOnly} />
            </Field>
            <Field label="Path" hint="A trailing * is a prefix match: /api/orders* matches /api/orders/42." required>
              <Input value={form.data.path} onChange={(e) => form.setData('path', e.target.value)} required disabled={readOnly} />
            </Field>
            <Field label="Methods" hint="Comma-separated. Empty matches every method.">
              <Input placeholder="GET,POST" value={form.data.methods} onChange={(e) => form.setData('methods', e.target.value)} disabled={readOnly} />
            </Field>
            <Field label="Domain" hint="Empty matches every host the gateway serves.">
              <Select value={form.data.domain_id} onChange={(e) => form.setData('domain_id', Number(e.target.value))} disabled={readOnly}>
                <option value={0}>Any domain</option>
                {domains.map((domain) => (
                  <option key={domain.id} value={domain.id}>{domain.hostname}</option>
                ))}
              </Select>
            </Field>
            <Field label="Description">
              <Input value={form.data.description} onChange={(e) => form.setData('description', e.target.value)} disabled={readOnly} />
            </Field>
          </div>
        </Panel>

        <Panel padded={false}>
          <PanelHeader title="Handling" description="What happens to a matching request." />
          <div className="grid gap-4 p-5 sm:grid-cols-2">
            <Field label="Action">
              <Select value={form.data.action} onChange={(e) => form.setData('action', e.target.value)} disabled={readOnly}>
                <option value="proxy">Proxy to an upstream</option>
                <option value="redirect">Redirect</option>
                <option value="static">Static response</option>
                <option value="maintenance">Maintenance response</option>
              </Select>
            </Field>

            {form.data.action === 'proxy' && (
              <>
                <Field label="Upstream" required>
                  <Select value={form.data.upstream_id} onChange={(e) => form.setData('upstream_id', Number(e.target.value))} disabled={readOnly}>
                    <option value={0}>Select an upstream</option>
                    {upstreams.map((upstream) => (
                      <option key={upstream.id} value={upstream.id}>{upstream.name}</option>
                    ))}
                  </Select>
                </Field>
                <Field label="Canary upstream" hint="Optional. A share of traffic goes here instead.">
                  <Select value={form.data.canary_upstream_id} onChange={(e) => form.setData('canary_upstream_id', Number(e.target.value))} disabled={readOnly}>
                    <option value={0}>No canary</option>
                    {upstreams.map((upstream) => (
                      <option key={upstream.id} value={upstream.id}>{upstream.name}</option>
                    ))}
                  </Select>
                </Field>
                <Field label="Canary share" hint="Percent of matching requests sent to the canary.">
                  <Input type="number" min={0} max={100} value={form.data.canary_percent} onChange={(e) => form.setData('canary_percent', Number(e.target.value))} disabled={readOnly} />
                </Field>
              </>
            )}

            {form.data.action === 'redirect' && (
              <>
                <Field label="Redirect to" required>
                  <Input value={form.data.redirect_to} onChange={(e) => form.setData('redirect_to', e.target.value)} disabled={readOnly} />
                </Field>
                <Field label="Status">
                  <Select value={form.data.redirect_status} onChange={(e) => form.setData('redirect_status', Number(e.target.value))} disabled={readOnly}>
                    <option value={301}>301 Moved Permanently</option>
                    <option value={302}>302 Found</option>
                    <option value={307}>307 Temporary Redirect</option>
                    <option value={308}>308 Permanent Redirect</option>
                  </Select>
                </Field>
              </>
            )}

            {(form.data.action === 'static' || form.data.action === 'maintenance') && (
              <>
                <Field label="Status">
                  <Input type="number" value={form.data.static_status} onChange={(e) => form.setData('static_status', Number(e.target.value))} disabled={readOnly} />
                </Field>
                <Field label="Body" className="sm:col-span-2">
                  <Textarea rows={3} value={form.data.static_body} onChange={(e) => form.setData('static_body', e.target.value)} disabled={readOnly} />
                </Field>
              </>
            )}
          </div>
        </Panel>

        <Panel padded={false}>
          <PanelHeader title="Transformation" description="Applied before the request leaves the gateway." />
          <div className="grid gap-4 p-5 sm:grid-cols-2">
            <Field label="Strip prefix" hint="Removed from the path before proxying. /api → upstream sees /orders.">
              <Input placeholder="/api" value={form.data.strip_prefix} onChange={(e) => form.setData('strip_prefix', e.target.value)} disabled={readOnly} />
            </Field>
            <Field label="Rewrite to" hint="Replaces the path entirely. Takes precedence over strip prefix.">
              <Input value={form.data.rewrite_to} onChange={(e) => form.setData('rewrite_to', e.target.value)} disabled={readOnly} />
            </Field>
          </div>
        </Panel>

        <Panel padded={false}>
          <PanelHeader title="Reliability and policy" />
          <div className="grid gap-4 p-5 sm:grid-cols-2">
            <Field label="Timeout (seconds)">
              <Input type="number" value={form.data.timeout_seconds} onChange={(e) => form.setData('timeout_seconds', Number(e.target.value))} disabled={readOnly} />
            </Field>
            <Field label="Retries" hint="Retries are bounded by the timeout above; without one they multiply the error rate.">
              <Input type="number" value={form.data.retries} onChange={(e) => form.setData('retries', Number(e.target.value))} disabled={readOnly} />
            </Field>
            <Field label="Authentication" hint="Checked at the edge as a presence check. The credential's validity is the upstream's business — Janus never sits in the request path.">
              <Select value={form.data.auth_policy} onChange={(e) => form.setData('auth_policy', e.target.value)} disabled={readOnly}>
                <option value="public">Public</option>
                <option value="api_key">API key</option>
                <option value="jwt">JWT</option>
                <option value="user">Authenticated user</option>
                <option value="role">Role required</option>
                <option value="scope">Scope required</option>
              </Select>
            </Field>
            {(form.data.auth_policy === 'role' || form.data.auth_policy === 'scope') && (
              <Field label="Required value">
                <Input value={form.data.auth_requirement} onChange={(e) => form.setData('auth_requirement', e.target.value)} disabled={readOnly} />
              </Field>
            )}
            <Checkbox
              checked={form.data.enabled}
              onChange={(e) => form.setData('enabled', e.target.checked)}
              label="Enabled"
              disabled={readOnly}
            />
            <Checkbox
              checked={form.data.maintenance}
              onChange={(e) => form.setData('maintenance', e.target.checked)}
              label="Maintenance mode"
              hint="Answers the maintenance response instead of proxying."
              disabled={readOnly}
            />
          </div>
        </Panel>

        {!readOnly && (
          <div className="flex items-center justify-end gap-2">
            <span className="mr-auto text-[12px] text-ink-faint">
              Saving writes desired state. Deploy from Configuration to apply it.
            </span>
            <Button onClick={() => router.get('/routes')}>Cancel</Button>
            <Button type="submit" variant="primary" loading={form.processing}>
              {editing ? 'Save route' : 'Create route'}
            </Button>
          </div>
        )}
      </form>
    </>
  )
}
