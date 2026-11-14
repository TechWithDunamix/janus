/**
 * The audit log.
 *
 * Every row can be expanded to show the before and after values, which is the
 * whole reason the table exists. "An operator changed a rate limit" answers
 * nothing during an incident; "changed it from 100/min to 10,000/min at 03:12
 * from 10.2.4.9, through the CLI" answers most of it.
 */

import { Head, router } from '@inertiajs/react'
import { Fragment, useEffect, useState } from 'react'
import { dateTime, useDebounced } from '@/js/hooks'
import {
  Badge, Empty, PageHeader, Panel, SearchInput, Select,
  Table, TBody, TD, TH, THead, TR,
} from '@/views/ui/kit'

type Event = {
  id: number; action: string; actor: string; resource_type: string
  resource_id: string | null; resource_label: string | null
  origin: string; ip: string | null
  before: Record<string, unknown> | null
  after: Record<string, unknown> | null
  created_at: string
}

const ORIGIN_TONE: Record<string, 'brand' | 'neutral'> = { cli: 'brand', web: 'neutral' }

export default function AuditLog({
  events,
  filters,
  resource_types,
}: {
  events: Event[]
  filters: { resource: string; actor: string; q: string }
  resource_types: string[]
}) {
  const [query, setQuery] = useState(filters.q)
  const debounced = useDebounced(query, 350)
  const [expanded, setExpanded] = useState<number | null>(null)

  useEffect(() => {
    if (debounced === filters.q) return
    router.get('/team/audit', { ...filters, q: debounced }, {
      preserveState: true,
      preserveScroll: true,
      replace: true,
    })
  }, [debounced])

  return (
    <>
      <Head title="Audit log" />
      <PageHeader
        title="Audit log"
        description="Who changed what, from where, and what it looked like before."
        actions={
          <div className="flex items-center gap-2">
            <Select
              value={filters.resource}
              onChange={(event) =>
                router.get('/team/audit', { ...filters, resource: event.target.value }, {
                  preserveState: true,
                  replace: true,
                })
              }
              className="w-[160px]"
            >
              <option value="">All resources</option>
              {resource_types.map((type) => (
                <option key={type} value={type}>{type}</option>
              ))}
            </Select>
            <SearchInput value={query} onChange={setQuery} placeholder="Filter by action…" />
          </div>
        }
      />

      <Panel padded={false}>
        {events.length === 0 ? (
          <Empty title="Nothing recorded" body="Actions that change state appear here." />
        ) : (
          <Table>
            <THead>
              <TR>
                <TH>When</TH>
                <TH>Actor</TH>
                <TH>Action</TH>
                <TH>Resource</TH>
                <TH>Via</TH>
                <TH align="right">From</TH>
              </TR>
            </THead>
            <TBody>
              {events.map((event) => (
                // A fragment with a key, because each event renders a row and
                // — when expanded — a second detail row beneath it.
                <Fragment key={event.id}>
                  <TR
                    onClick={() => setExpanded(expanded === event.id ? null : event.id)}
                    className="cursor-pointer"
                  >
                    <TD className="text-ink-muted">{dateTime(event.created_at)}</TD>
                    <TD>{event.actor}</TD>
                    <TD><span className="mono">{event.action}</span></TD>
                    <TD className="max-w-[280px] truncate text-ink-muted">
                      {event.resource_label ?? event.resource_type}
                    </TD>
                    <TD><Badge tone={ORIGIN_TONE[event.origin] ?? 'neutral'}>{event.origin}</Badge></TD>
                    <TD align="right"><span className="mono text-ink-muted">{event.ip ?? '—'}</span></TD>
                  </TR>
                  {expanded === event.id && (event.before || event.after) && (
                    <TR>
                      <TD colSpan={6} className="bg-sunken">
                        <div className="grid gap-4 py-2 sm:grid-cols-2">
                          <div>
                            <p className="eyebrow mb-1.5">Before</p>
                            <pre className="mono overflow-x-auto text-[11.5px] text-ink-muted">
                              {event.before ? JSON.stringify(event.before, null, 2) : '—'}
                            </pre>
                          </div>
                          <div>
                            <p className="eyebrow mb-1.5">After</p>
                            <pre className="mono overflow-x-auto text-[11.5px] text-ink-muted">
                              {event.after ? JSON.stringify(event.after, null, 2) : '—'}
                            </pre>
                          </div>
                        </div>
                      </TD>
                    </TR>
                  )}
                </Fragment>
              ))}
            </TBody>
          </Table>
        )}
      </Panel>

      <p className="mt-3 text-[12px] text-ink-faint">
        Secrets never reach this table. Password, key and token fields are stripped before an entry
        is written, not hidden when it is displayed.
      </p>
    </>
  )
}
