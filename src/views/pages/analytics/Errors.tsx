import { Head } from '@inertiajs/react'
import { count } from '@/js/hooks'
import type { Range, RouteRow, SeriesPoint, Summary } from '@/js/types'
import { AnalyticsHeader, RouteTable, Section, SummaryRow, TrafficChart } from '@/views/ui/analytics'
import { Empty, Panel, PanelHeader, Skeleton, Table, TBody, TD, TH, THead, TR } from '@/views/ui/kit'

type Errors = {
  codes: { code: string; count: number }[]
  by_route: (RouteRow & { errors: number })[]
  by_client: { client_ip: string; errors: number }[]
  by_method: { method: string; errors: number }[]
  detail_since: string
  detail_truncated: boolean
}

/** Which status codes are worth alarm colouring rather than plain text. */
const SEVERE = new Set(['500', '502', '503', '504', 'other 5xx'])

export default function ErrorsPage({
  range,
  summary,
  errors,
  series,
}: {
  range: Range
  summary: Summary
  errors?: Errors
  series?: SeriesPoint[]
}) {
  return (
    <>
      <Head title="Errors" />
      <AnalyticsHeader
        title="Error analytics"
        description="What is failing, and for whom"
        range={range}
        simulated={summary.simulated}
      />

      <SummaryRow summary={summary} />

      <Panel padded={false}>
        <PanelHeader title="Errors over time" />
        <div className="p-5">
          <TrafficChart series={series} kind="traffic" height={300} />
        </div>
      </Panel>

      <Section>
        <Panel padded={false}>
          <PanelHeader title="By status code" />
          {!errors ? (
            <div className="p-5"><Skeleton rows={5} /></div>
          ) : errors.codes.length === 0 ? (
            <Empty title="No errors in this range" />
          ) : (
            <Table>
              <THead>
                <TR>
                  <TH>Code</TH>
                  <TH align="right">Count</TH>
                </TR>
              </THead>
              <TBody>
                {errors.codes.map((row) => (
                  <TR key={row.code}>
                    <TD>
                      <span className={`mono ${SEVERE.has(row.code) ? 'text-critical' : ''}`}>
                        {row.code}
                      </span>
                    </TD>
                    <TD align="right" className="num">{count(row.count)}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          )}
        </Panel>

        <Panel padded={false}>
          <PanelHeader
            title="By client"
            description={
              errors?.detail_truncated
                ? 'Request-level detail only reaches back as far as retention allows.'
                : undefined
            }
          />
          {!errors ? (
            <div className="p-5"><Skeleton rows={5} /></div>
          ) : errors.by_client.length === 0 ? (
            <Empty title="No errors attributed to a client" />
          ) : (
            <Table>
              <THead>
                <TR>
                  <TH>Client</TH>
                  <TH align="right">Errors</TH>
                </TR>
              </THead>
              <TBody>
                {errors.by_client.map((row) => (
                  <TR key={row.client_ip}>
                    <TD><span className="mono">{row.client_ip}</span></TD>
                    <TD align="right" className="num">{count(row.errors)}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          )}
        </Panel>
      </Section>

      <Panel className="mt-4" padded={false}>
        <PanelHeader title="By route" description="Routes with any error in this range." />
        <RouteTable routes={errors?.by_route} />
      </Panel>
    </>
  )
}
