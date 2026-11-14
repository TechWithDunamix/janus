import { Head, Link, router } from '@inertiajs/react'
import { useState } from 'react'
import { ago, bytes, count, useDebounced } from '@/js/hooks'
import { useEffect } from 'react'
import {
  Badge, Empty, PageHeader, Panel, SearchInput, Table, TBody, TD, TH, THead, TR,
} from '@/views/ui/kit'

type Client = {
  id: number; kind: string; identifier: string; label: string | null
  organisation: string | null; status: string
  request_count: number; error_count: number; blocked_count: number; bytes_out: number
  first_seen_at: string | null; last_seen_at: string | null
}

const STATUS_TONE: Record<string, 'critical' | 'positive' | 'neutral'> = {
  blocked: 'critical',
  allowed: 'positive',
  active: 'neutral',
}

export default function Clients({
  clients,
  filters,
}: {
  clients: Client[]
  filters: { q: string; status: string }
}) {
  const [query, setQuery] = useState(filters.q)
  const debounced = useDebounced(query, 350)

  useEffect(() => {
    if (debounced === filters.q) return
    router.get('/security/clients', { q: debounced, status: filters.status }, {
      preserveState: true,
      preserveScroll: true,
      replace: true,
    })
  }, [debounced])

  return (
    <>
      <Head title="Clients" />
      <PageHeader
        title="Clients"
        description="Everyone the gateway has seen, whether an administrator named them or the collector discovered them."
        actions={
          <SearchInput
            value={query}
            onChange={setQuery}
            placeholder="Search by address…"
          />
        }
      />

      <Panel padded={false}>
        {clients.length === 0 ? (
          <Empty title="No clients" body="Clients appear here as traffic arrives." />
        ) : (
          <Table>
            <THead>
              <TR>
                <TH>Client</TH>
                <TH>Label</TH>
                <TH>State</TH>
                <TH align="right">Requests</TH>
                <TH align="right">Errors</TH>
                <TH align="right">Blocked</TH>
                <TH align="right">Bandwidth</TH>
                <TH align="right">Last seen</TH>
              </TR>
            </THead>
            <TBody>
              {clients.map((client) => (
                <TR key={client.id}>
                  <TD>
                    <Link href={`/security/clients/${client.id}`} className="mono hover:text-brand">
                      {client.identifier}
                    </Link>
                    <div className="text-[11px] text-ink-faint">{client.kind}</div>
                  </TD>
                  <TD className="text-ink-muted">{client.label ?? '—'}</TD>
                  <TD><Badge tone={STATUS_TONE[client.status] ?? 'neutral'}>{client.status}</Badge></TD>
                  <TD align="right" className="num">{count(client.request_count)}</TD>
                  <TD align="right" className="num text-ink-muted">{count(client.error_count)}</TD>
                  <TD align="right" className={`num ${client.blocked_count > 0 ? 'text-critical' : 'text-ink-muted'}`}>
                    {count(client.blocked_count)}
                  </TD>
                  <TD align="right" className="num text-ink-muted">{bytes(client.bytes_out)}</TD>
                  <TD align="right" className="text-ink-muted">{ago(client.last_seen_at)}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </Panel>
    </>
  )
}
