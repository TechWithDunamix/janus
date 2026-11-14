/**
 * Caddy plugins.
 *
 * The screen is shaped by one fact it cannot design around: Caddy is a static
 * binary and plugins are compiled in. There is no install button that could
 * honestly exist, so the page does the four things that are actually possible
 * — say what the build has, say what each missing plugin would unlock, produce
 * the exact rebuild command, and let an operator record a remote build Janus
 * cannot introspect.
 */

import { Head, router, useForm } from '@inertiajs/react'
import { useRef, useState } from 'react'
import { cx, useCan, useCopy } from '@/js/hooks'
import {
  Badge, Banner, Button, Checkbox, Field, Modal, PageHeader, Panel, PanelHeader,
  Skeleton, Textarea,
} from '@/views/ui/kit'
import { IconCheck, IconClose, IconCopy, IconExternal } from '@/views/ui/icons'

type Row = {
  key: string
  name: string
  package: string
  provides: string[]
  summary: string
  unlocks: string[]
  category: string
  homepage: string
  caveat: string
  installed: boolean
}

type Overview = {
  source: 'binary' | 'declared' | 'unknown'
  version: string | null
  error: string | null
  module_count: number
  standard_count: number
  non_standard: string[]
  unrecognised: string[]
  catalog: Row[]
  installed: Row[]
  available: Row[]
  gaps: {
    feature: string
    without: string
    plugin: string
    package: string | null
    key: string | null
    screens: string[]
  }[]
  build_command: string
  script_command: string
  requires_rebuild: boolean
}

const SOURCE_COPY: Record<string, { tone: 'positive' | 'caution' | 'neutral'; label: string; body: string }> = {
  binary: {
    tone: 'positive',
    label: 'Detected',
    body: 'Read from the caddy binary on this host. Accurate when that binary is the one this gateway runs.',
  },
  declared: {
    tone: 'caution',
    label: 'Declared',
    body: 'Recorded by an operator. Janus cannot introspect a Caddy on another host, so this is an assumption rather than an observation.',
  },
  unknown: {
    tone: 'caution',
    label: 'Unknown',
    body: 'No caddy binary reachable and nothing declared. Janus assumes core modules only and will not emit a handler outside them.',
  },
}

const CATEGORY_LABEL: Record<string, string> = {
  traffic: 'Traffic',
  security: 'Security',
  tls: 'TLS',
  observability: 'Observability',
  general: 'General',
}

export default function Modules({
  gateway,
  modules,
  adding,
  removing = [],
}: {
  gateway: { id: number; name: string }
  modules?: Overview
  adding: string[]
  removing?: string[]
}) {
  const can = useCan()
  const [selected, setSelected] = useState<string[]>(adding)
  const [dropped, setDropped] = useState<string[]>(removing)
  const [declaring, setDeclaring] = useState(false)
  const [buildMode, setBuildMode] = useState<'script' | 'xcaddy'>('script')
  const [copied, copy] = useCopy()
  const rebuildRef = useRef<HTMLDivElement>(null)

  const scrollToRebuild = () =>
    rebuildRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })

  if (!modules) {
    return (
      <>
        <Head title="Caddy modules" />
        <PageHeader title="Caddy modules" description={gateway.name} />
        <Panel><Skeleton rows={8} /></Panel>
      </>
    )
  }

  const source = SOURCE_COPY[modules.source] ?? SOURCE_COPY.unknown
  const categories = [...new Set(modules.catalog.map((r) => r.category))]

  // What the operator has staged. Nothing here touches the running gateway —
  // a compiled-in plugin can only be added or removed by rebuilding the
  // binary, so these just change the command below.
  const addedRows = modules.catalog.filter(
    (row) => !row.installed && selected.includes(row.key),
  )
  const removedRows = modules.catalog.filter(
    (row) => row.installed && dropped.includes(row.key),
  )
  const stagedCount = addedRows.length + removedRows.length

  const command = buildMode === 'script' ? modules.script_command : modules.build_command

  const sync = (nextAdd: string[], nextRemove: string[]) => {
    setSelected(nextAdd)
    setDropped(nextRemove)
    router.get(
      '/system/modules',
      { add: nextAdd.join(','), remove: nextRemove.join(',') },
      { preserveState: true, preserveScroll: true, replace: true, only: ['modules'] },
    )
  }

  const toggleAdd = (key: string) =>
    sync(
      selected.includes(key) ? selected.filter((k) => k !== key) : [...selected, key],
      dropped,
    )

  const toggleRemove = (key: string) =>
    sync(
      selected,
      dropped.includes(key) ? dropped.filter((k) => k !== key) : [...dropped, key],
    )

  // Alias kept for the add-side call sites below.
  const toggle = toggleAdd

  // Stage a change and pull the rebuild command into view, so the effect of
  // the click is visible rather than three panels down the page.
  const stage = (key: string, kind: 'add' | 'remove') => {
    const wasStaged = kind === 'add' ? selected.includes(key) : dropped.includes(key)
    if (kind === 'add') toggleAdd(key)
    else toggleRemove(key)
    if (!wasStaged) requestAnimationFrame(scrollToRebuild)
  }

  return (
    <>
      <Head title="Caddy modules" />
      <PageHeader
        title="Caddy modules"
        description={`${gateway.name} · ${modules.module_count} modules in this build`}
        actions={
          can('gateway.write') && (
            <Button size="sm" onClick={() => setDeclaring(true)}>
              Declare a remote build
            </Button>
          )
        }
      />

      {/* The constraint the whole screen is shaped by. */}
      <Banner tone="info" title="Plugins are compiled into the binary" className="mb-4">
        Caddy is a single static binary, so a plugin cannot be installed into a running
        gateway. Adding one means rebuilding with <span className="mono">xcaddy</span>,
        replacing the binary and restarting Caddy. Janus tells you exactly what to build.
      </Banner>

      <div className="mb-4 grid gap-3 sm:grid-cols-3">
        <Panel>
          <div className="eyebrow">Build information</div>
          <div className="mt-1 flex items-center gap-2">
            <span className="num text-[18px] font-semibold">{modules.version ?? 'unknown'}</span>
            <Badge tone={source.tone}>{source.label}</Badge>
          </div>
          <p className="mt-1.5 text-[11.5px] leading-[1.5] text-ink-faint">{source.body}</p>
          {modules.error && (
            <p className="mt-1.5 text-[11.5px] text-caution">{modules.error}</p>
          )}
        </Panel>
        <Panel>
          <div className="eyebrow">Plugins present</div>
          <div className="mt-1 num text-[18px] font-semibold">
            {modules.installed.length} of {modules.catalog.length}
          </div>
          <p className="mt-1.5 text-[11.5px] text-ink-faint">
            From the plugins Janus knows about.
          </p>
        </Panel>
        <Panel>
          <div className="eyebrow">Features unavailable</div>
          <div
            className={cx(
              'mt-1 num text-[18px] font-semibold',
              modules.gaps.length > 0 && 'text-caution',
            )}
          >
            {modules.gaps.length}
          </div>
          <p className="mt-1.5 text-[11.5px] text-ink-faint">
            Janus capabilities this build cannot support.
          </p>
        </Panel>
      </div>

      {modules.gaps.length > 0 && (
        <Panel className="mb-4" padded={false}>
          <PanelHeader
            title="What this build cannot do"
            description="Janus never emits a handler it knows would be rejected, so these fail loudly here rather than quietly at the gateway."
          />
          <ul className="divide-y divide-line">
            {modules.gaps.map((gap) => (
              <li key={gap.feature} className="flex flex-wrap items-start gap-3 px-5 py-3.5">
                <span className="dot mt-1.5 bg-caution" />
                <div className="min-w-0 flex-1">
                  <p className="text-[13px] font-medium text-ink">{gap.feature}</p>
                  <p className="mt-0.5 text-[12.5px] leading-[1.55]">{gap.without}</p>
                  <p className="mt-1 text-[11.5px] text-ink-faint">
                    Affects: {gap.screens.join(', ')}
                  </p>
                </div>
                {gap.key && (
                  selected.includes(gap.key) ? (
                    <span className="inline-flex shrink-0 items-center gap-1.5 text-[11.5px] font-medium text-brand">
                      <IconCheck className="h-3.5 w-3.5" />
                      Staged
                      <button
                        type="button"
                        onClick={() => toggle(gap.key!)}
                        className="text-ink-faint underline underline-offset-2 hover:text-ink"
                      >
                        undo
                      </button>
                    </span>
                  ) : (
                    <Button size="xs" onClick={() => stage(gap.key!, 'add')}>
                      Add {gap.plugin}
                    </Button>
                  )
                )}
              </li>
            ))}
          </ul>
        </Panel>
      )}

      {categories.map((category) => (
        <Panel key={category} className="mb-4" padded={false}>
          <PanelHeader title={CATEGORY_LABEL[category] ?? category} />
          <ul className="divide-y divide-line">
            {modules.catalog
              .filter((row) => row.category === category)
              .map((row) => (
                <li key={row.key} className="px-5 py-4">
                  <div className="flex flex-wrap items-start gap-3">
                    <Checkbox
                      checked={
                        row.installed ? !dropped.includes(row.key) : selected.includes(row.key)
                      }
                      disabled={row.installed}
                      onChange={() => toggle(row.key)}
                      label=""
                    />
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-[13px] font-semibold text-ink">{row.name}</span>
                        {row.installed && dropped.includes(row.key) ? (
                          <Badge tone="critical">staged for removal</Badge>
                        ) : row.installed ? (
                          <Badge tone="positive" dot>installed</Badge>
                        ) : selected.includes(row.key) ? (
                          <Badge tone="brand">selected</Badge>
                        ) : null}
                        {row.homepage && (
                          <a
                            href={row.homepage}
                            target="_blank"
                            rel="noreferrer"
                            className="inline-flex items-center gap-1 text-[11.5px] text-ink-faint hover:text-brand"
                          >
                            <IconExternal className="h-3 w-3" />
                            source
                          </a>
                        )}
                      </div>
                      <p className="mt-1 text-[12.5px] leading-[1.55]">{row.summary}</p>
                      <p className="mono mt-1.5 text-[11.5px] text-ink-faint">{row.package}</p>

                      {row.unlocks.length > 0 && !row.installed && (
                        <ul className="mt-2 space-y-1">
                          {row.unlocks.map((item) => (
                            <li key={item} className="flex gap-2 text-[12px] text-ink-muted">
                              <span className="dot mt-[0.5em] bg-brand/50" />
                              {item}
                            </li>
                          ))}
                        </ul>
                      )}
                      {row.caveat && (
                        <p className="mt-2 rounded-[var(--radius-sm)] bg-sunken px-3 py-2 text-[11.5px] leading-[1.5] text-ink-muted">
                          {row.caveat}
                        </p>
                      )}
                    </div>
                    {can('gateway.write') && row.installed && (
                      dropped.includes(row.key) ? (
                        <button
                          type="button"
                          onClick={() => toggleRemove(row.key)}
                          className="shrink-0 text-[11.5px] font-medium text-ink-faint underline underline-offset-2 hover:text-ink"
                        >
                          keep
                        </button>
                      ) : (
                        <Button
                          size="xs"
                          variant="danger"
                          className="shrink-0"
                          onClick={() => stage(row.key, 'remove')}
                        >
                          Remove
                        </Button>
                      )
                    )}
                  </div>
                </li>
              ))}
          </ul>
        </Panel>
      ))}

      <div ref={rebuildRef} className="scroll-mt-6">
      <Panel padded={false}>
        <PanelHeader
          title={
            <span className="flex items-center gap-2">
              Rebuild command
              {stagedCount > 0 && (
                <Badge tone="brand">{stagedCount} staged</Badge>
              )}
            </span>
          }
          description={
            buildMode === 'script'
              ? 'One command on the host Caddy runs on: build (or download) the binary, back up the current one, swap it in, restart Caddy, redeploy.'
              : 'Build the binary yourself with xcaddy, then replace it and restart Caddy by hand.'
          }
          action={
            <Button size="xs" onClick={() => copy(command)}>
              {copied ? <IconCheck className="h-3.5 w-3.5" /> : <IconCopy className="h-3.5 w-3.5" />}
              {copied ? 'Copied' : 'Copy'}
            </Button>
          }
        />
        <div className="p-5">
          <div className="mb-3 inline-flex rounded-full border border-line p-0.5 text-[11.5px] font-medium">
            {(['script', 'xcaddy'] as const).map((mode) => (
              <button
                key={mode}
                type="button"
                onClick={() => setBuildMode(mode)}
                className={cx(
                  'rounded-full px-3 py-1 transition',
                  buildMode === mode ? 'bg-brand text-on-primary' : 'text-ink-muted hover:text-ink',
                )}
              >
                {mode === 'script' ? 'One-shot script' : 'Manual xcaddy'}
              </button>
            ))}
          </div>

          {addedRows.length > 0 && (
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <span className="text-[11.5px] font-medium text-ink-faint">Adding:</span>
              {addedRows.map((row) => (
                <button
                  key={row.key}
                  type="button"
                  onClick={() => toggleAdd(row.key)}
                  className="inline-flex items-center gap-1 rounded-full bg-brand-soft px-2.5 py-1 text-[11.5px] font-medium text-brand transition hover:bg-brand/15"
                  title={`Don't add ${row.name}`}
                >
                  {row.name}
                  <IconClose className="h-3 w-3" />
                </button>
              ))}
            </div>
          )}
          {removedRows.length > 0 && (
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <span className="text-[11.5px] font-medium text-ink-faint">Removing:</span>
              {removedRows.map((row) => (
                <button
                  key={row.key}
                  type="button"
                  onClick={() => toggleRemove(row.key)}
                  className="inline-flex items-center gap-1 rounded-full bg-critical-soft px-2.5 py-1 text-[11.5px] font-medium text-critical transition hover:bg-critical/15"
                  title={`Keep ${row.name}`}
                >
                  {row.name}
                  <IconClose className="h-3 w-3" />
                </button>
              ))}
            </div>
          )}
          <pre className="overflow-x-auto rounded-[var(--radius-sm)] border border-line bg-sunken px-4 py-3.5">
            <code className="mono text-[12.5px] leading-[1.6] text-ink">
              {command}
            </code>
          </pre>

          {buildMode === 'script' ? (
            <p className="mt-3 text-[12px] leading-[1.55] text-ink-muted">
              <span className="font-medium text-ink">Runs where Caddy lives.</span>{' '}
              It uses <span className="mono">xcaddy</span> if a Go toolchain is present and
              downloads a custom build from caddyserver.com if not, keeps a timestamped
              backup, and can be reversed with{' '}
              <span className="mono">scripts/setup-caddy.sh --rollback --restart</span>.
              {removedRows.length > 0 && (
                <>
                  {' '}Since you just staged a removal, if you have not rebuilt yet the
                  fastest undo after running this is that same{' '}
                  <span className="mono">--rollback</span>.
                </>
              )}
            </p>
          ) : (
            <p className="mt-3 text-[12px] leading-[1.55] text-ink-muted">
              <span className="font-medium text-ink">Every plugin is listed on purpose.</span>{' '}
              <span className="mono">xcaddy</span> produces a fresh binary from exactly the
              packages named, so a command listing only what you are adding silently drops
              the plugins the current binary has — a build that succeeds and behaves like a
              downgrade.
            </p>
          )}
          {modules.unrecognised.length > 0 && (
            <Banner tone="caution" title="Not included" className="mt-3">
              This build has {modules.unrecognised.length} non-standard module(s) Janus
              does not recognise, so the command above is incomplete for it:{' '}
              <span className="mono">{modules.unrecognised.join(', ')}</span>
            </Banner>
          )}
          <p className="mt-3 text-[12px] text-ink-faint">
            {buildMode === 'script'
              ? 'For a Caddy on another host, run the script there (it needs this checkout), or use the manual steps.'
              : 'Replace the binary and restart Caddy. Janus re-detects on the next visit.'}
          </p>
        </div>
      </Panel>
      </div>

      {stagedCount > 0 && (
        <div className="sticky bottom-4 z-10 mt-4">
          <Banner
            tone="brand"
            className="card-shadow"
            title={`${stagedCount} change${stagedCount > 1 ? 's' : ''} staged for the rebuild command`}
            action={
              <div className="flex gap-2">
                <Button size="xs" onClick={scrollToRebuild}>View command</Button>
                <Button size="xs" variant="ghost" onClick={() => sync([], [])}>Clear</Button>
              </div>
            }
          >
            {[
              addedRows.length > 0 && `add ${addedRows.map((r) => r.name).join(', ')}`,
              removedRows.length > 0 && `remove ${removedRows.map((r) => r.name).join(', ')}`,
            ]
              .filter(Boolean)
              .join('; ')}
            {' '}— nothing changes on the running gateway until you rebuild the binary with
            that command and restart Caddy.
          </Banner>
        </div>
      )}

      {declaring && (
        <DeclareModal
          current={modules.source === 'declared' ? modules.catalog.filter((r) => r.installed) : []}
          onClose={() => setDeclaring(false)}
        />
      )}
    </>
  )
}

function DeclareModal({ current, onClose }: { current: Row[]; onClose: () => void }) {
  const form = useForm({
    modules: current.flatMap((r) => r.provides).join(', '),
  })
  return (
    <Modal open title="Declare a remote build" onClose={onClose}>
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault()
          form.post('/system/modules/declare', { onSuccess: onClose })
        }}
      >
        <p className="text-[12.5px] leading-[1.6] text-ink-muted">
          <span className="mono">caddy list-modules</span> asks the binary on the machine
          Janus runs on, which says nothing about a Caddy on another host. If this gateway
          runs elsewhere, record what you built and Janus will use that instead of guessing.
        </p>
        <Field
          label="Module ids"
          hint="Comma-separated, as caddy list-modules prints them. Leave empty to clear and return to local detection."
        >
          <Textarea
            rows={4}
            className="font-mono"
            placeholder="http.handlers.rate_limit, http.authentication.providers.jwt"
            value={form.data.modules}
            onChange={(e) => form.setData('modules', e.target.value)}
          />
        </Field>
        <p className="text-[11.5px] text-ink-faint">
          Get the list with <span className="mono">caddy list-modules</span> on the gateway host.
        </p>
        <div className="flex justify-end gap-2 pt-2">
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" loading={form.processing}>
            Record
          </Button>
        </div>
      </form>
    </Modal>
  )
}
