/**
 * The configuration screen.
 *
 * This is where desired state becomes running state, so it is built to make
 * the pipeline visible rather than to hide it behind one button: what would
 * change, what the generator warned about, what is live now, and what has been
 * deployed before. Deploying without seeing the diff is possible but is not
 * the default path through the screen.
 */

import { Head, useForm } from '@inertiajs/react'
import { useState } from 'react'
import { dateTime, useCan } from '@/js/hooks'
import { SyncBadge } from '@/views/ui/gateway'
import {
  Badge, Banner, Button, Empty, Field, Input, Modal, PageHeader, Panel, PanelHeader,
  Skeleton, Tabs, Table, TBody, TD, TH, THead, TR,
} from '@/views/ui/kit'

type Version = {
  id: number; version: number; status: string; summary: string | null; note: string | null
  checksum: string; origin: string; route_count: number; upstream_count: number
  author: string; created_at: string; activated_at: string | null
  validation_error: string | null
}

const STATUS_TONE: Record<string, 'positive' | 'neutral' | 'critical' | 'caution'> = {
  active: 'positive',
  superseded: 'neutral',
  rejected: 'critical',
  failed: 'critical',
  validated: 'caution',
  draft: 'neutral',
}

export default function Configuration({
  generated,
  warnings,
  diff,
  has_changes,
  active,
  sync_state,
  sync_error,
  history,
}: {
  generated: Record<string, unknown>
  warnings: string[]
  diff: string[]
  has_changes: boolean
  active: { version: number; checksum: string; activated_at: string | null; summary: string | null } | null
  sync_state: 'SYNCED' | 'SYNCING' | 'FAILED' | 'DRIFTED'
  sync_error: string | null
  history?: Version[]
}) {
  const can = useCan()
  const [tab, setTab] = useState<'diff' | 'generated'>('diff')
  const [deploying, setDeploying] = useState(false)
  const [rollingBack, setRollingBack] = useState<number | null>(null)

  return (
    <>
      <Head title="Configuration" />
      <PageHeader
        title="Configuration"
        description="Generate, validate, apply, verify. A rejected configuration never reaches the gateway; a failed apply leaves the previous one running."
        actions={
          <div className="flex items-center gap-2">
            <SyncBadge state={sync_state} />
            {can('configuration.write') && (
              <Button
                variant="primary"
                size="sm"
                disabled={!has_changes && sync_state === 'SYNCED'}
                onClick={() => setDeploying(true)}
              >
                {has_changes ? 'Review & deploy' : 'Redeploy'}
              </Button>
            )}
          </div>
        }
      />

      {sync_error && (
        <Banner tone={sync_state === 'FAILED' ? 'critical' : 'caution'} title={`Gateway is ${sync_state}`} className="mb-4">
          {sync_error}
        </Banner>
      )}

      {warnings.length > 0 && (
        <Banner tone="caution" title="Generated with warnings" className="mb-4">
          <ul className="mt-1 space-y-0.5">
            {warnings.map((warning) => (
              <li key={warning}>· {warning}</li>
            ))}
          </ul>
        </Banner>
      )}

      <div className="mb-4 grid gap-3 sm:grid-cols-3">
        <Panel>
          <div className="eyebrow">Active version</div>
          <div className="mt-1 num text-[18px] font-semibold">
            {active ? `v${active.version}` : 'none'}
          </div>
          <p className="mt-1 text-[11.5px] text-ink-faint">
            {active ? dateTime(active.activated_at) : 'Nothing has been deployed yet.'}
          </p>
        </Panel>
        <Panel>
          <div className="eyebrow">Checksum</div>
          <div className="mono mt-1 text-[14px]">{active?.checksum ?? '—'}</div>
          <p className="mt-1 text-[11.5px] text-ink-faint">Of the payload Caddy is running.</p>
        </Panel>
        <Panel>
          <div className="eyebrow">Pending changes</div>
          <div className="mt-1 num text-[18px] font-semibold">
            {has_changes ? diff.filter((l) => l.startsWith('+') || l.startsWith('-')).length - 2 : 0}
          </div>
          <p className="mt-1 text-[11.5px] text-ink-faint">
            {has_changes ? 'Lines differ from what is live.' : 'Desired state matches the gateway.'}
          </p>
        </Panel>
      </div>

      <Panel padded={false}>
        <PanelHeader
          title="Configuration"
          action={
            <Tabs
              tabs={[
                { key: 'diff', label: 'Pending diff' },
                { key: 'generated', label: 'Generated JSON' },
              ]}
              active={tab}
              onSelect={(key: string) => setTab(key as 'diff' | 'generated')}
            />
          }
        />
        <div className="max-h-[560px] overflow-auto">
          {tab === 'diff' ? (
            diff.length === 0 ? (
              <Empty title="No changes" body="The gateway is running the desired configuration." />
            ) : (
              <pre className="mono px-5 py-4 text-[12px] leading-[1.55]">
                {diff.map((line, index) => (
                  <div
                    key={index}
                    className={
                      line.startsWith('+') && !line.startsWith('+++')
                        ? 'bg-ok-soft text-ok'
                        : line.startsWith('-') && !line.startsWith('---')
                          ? 'bg-critical-soft text-critical'
                          : line.startsWith('@@')
                            ? 'text-ink-faint'
                            : 'text-ink-muted'
                    }
                  >
                    {line || ' '}
                  </div>
                ))}
              </pre>
            )
          ) : (
            <pre className="mono px-5 py-4 text-[12px] leading-[1.55] text-ink-muted">
              {JSON.stringify(generated, null, 2)}
            </pre>
          )}
        </div>
      </Panel>

      <Panel className="mt-4" padded={false}>
        <PanelHeader
          title="Version history"
          description="Every deployment creates a version holding its complete payload, which is what makes a rollback exact rather than an inverse operation."
        />
        {!history ? (
          <div className="p-5"><Skeleton rows={5} /></div>
        ) : history.length === 0 ? (
          <Empty title="No versions yet" />
        ) : (
          <Table>
            <THead>
              <TR>
                <TH>Version</TH>
                <TH>Status</TH>
                <TH>Summary</TH>
                <TH>Author</TH>
                <TH>Created</TH>
                <TH align="right">Routes</TH>
                <TH align="right" />
              </TR>
            </THead>
            <TBody>
              {history.map((version) => (
                <TR key={version.id}>
                  <TD><span className="num font-medium">v{version.version}</span></TD>
                  <TD><Badge tone={STATUS_TONE[version.status] ?? 'neutral'}>{version.status}</Badge></TD>
                  <TD className="max-w-[320px]">
                    <div className="truncate text-ink-muted">{version.summary ?? '—'}</div>
                    {version.validation_error && (
                      <div className="mt-0.5 truncate text-[11.5px] text-critical">
                        {version.validation_error}
                      </div>
                    )}
                  </TD>
                  <TD className="text-ink-muted">
                    {version.author}
                    <span className="ml-1 text-[11px] text-ink-faint">via {version.origin}</span>
                  </TD>
                  <TD className="text-ink-muted">{dateTime(version.created_at)}</TD>
                  <TD align="right" className="num">{version.route_count}</TD>
                  <TD align="right">
                    {can('configuration.rollback') &&
                      version.status !== 'active' &&
                      version.status !== 'rejected' && (
                        <Button size="xs" variant="ghost" onClick={() => setRollingBack(version.version)}>
                          Restore
                        </Button>
                      )}
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </Panel>

      {deploying && (
        <DeployModal
          changes={has_changes}
          warnings={warnings}
          onClose={() => setDeploying(false)}
        />
      )}
      {rollingBack !== null && (
        <RollbackModal version={rollingBack} onClose={() => setRollingBack(null)} />
      )}
    </>
  )
}

function DeployModal({
  changes,
  warnings,
  onClose,
}: {
  changes: boolean
  warnings: string[]
  onClose: () => void
}) {
  const form = useForm({ note: '', force: !changes })
  return (
    <Modal open title="Deploy configuration" onClose={onClose}>
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault()
          form.post('/configuration/apply', { onSuccess: onClose })
        }}
      >
        <p className="text-[12.5px] text-ink-muted">
          Janus will generate the configuration, validate it, apply it to the gateway and read it
          back to confirm. If validation fails nothing is sent; if the apply fails the gateway keeps
          running its current configuration.
        </p>
        {warnings.length > 0 && (
          <Banner tone="caution" title="Warnings">
            <ul className="mt-1 space-y-0.5">
              {warnings.map((warning) => (
                <li key={warning}>· {warning}</li>
              ))}
            </ul>
          </Banner>
        )}
        <Field label="Note" hint="Recorded against the version, for the history.">
          <Input value={form.data.note} onChange={(e) => form.setData('note', e.target.value)} autoFocus />
        </Field>
        <div className="flex justify-end gap-2 pt-2">
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" loading={form.processing}>Deploy</Button>
        </div>
      </form>
    </Modal>
  )
}

function RollbackModal({ version, onClose }: { version: number; onClose: () => void }) {
  const form = useForm({ version })
  return (
    <Modal open title={`Roll back to v${version}`} onClose={onClose}>
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault()
          form.post('/configuration/rollback', { onSuccess: onClose })
        }}
      >
        <p className="text-[12.5px] text-ink-muted">
          This redeploys v{version}&rsquo;s stored payload exactly as it was recorded — not an undo
          of the changes since. A new version is created carrying that payload, so the history reads
          forward.
        </p>
        <div className="flex justify-end gap-2 pt-2">
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="danger" loading={form.processing}>
            Roll back
          </Button>
        </div>
      </form>
    </Modal>
  )
}
