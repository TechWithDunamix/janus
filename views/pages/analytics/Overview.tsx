import { Head } from '@inertiajs/react'
import type { Range, RouteRow, SeriesPoint, Summary } from '@/js/types'
import { AnalyticsHeader, Breakdown, RouteTable, Section, SummaryRow, TrafficChart } from '@/views/ui/analytics'
import { Panel, PanelHeader } from '@/views/ui/kit'
import { StackedBar } from '@/views/ui/charts'

type Traffic = {
  by_domain: { label: string; requests: number; bytes_out: number }[]
  by_method: { label: string; requests: number; bytes_out: number }[]
  by_client: { label: string; requests: number; bytes_out: number }[]
  by_route: RouteRow[]
  detail_since: string
  detail_truncated: boolean
}

export default function Overview({
  range,
  summary,
  series,
  traffic,
  status_distribution,
}: {
  range: Range
  summary: Summary
  series?: SeriesPoint[]
  traffic?: Traffic
  status_distribution?: { label: string; count: number; share: number }[]
}) {
  return (
    <>
      <Head title="Analytics" />
      <AnalyticsHeader
        title="Analytics"
        description="Traffic, errors and how they are distributed."
        range={range}
        simulated={summary.simulated}
      />

      <SummaryRow summary={summary} />

      <Panel padded={false}>
        <PanelHeader title="Over time" />
        <div className="p-5">
          <TrafficChart series={series} kind="traffic" height={320} />
        </div>
      </Panel>

      {status_distribution && (
        <Panel className="mt-4">
          <PanelHeader title="Status distribution" />
          <div className="pt-4">
            <StackedBar
              segments={[
                { label: '2xx', value: status_distribution.find((s) => s.label === '2xx')?.count ?? 0, color: 'var(--color-ok)' },
                { label: '3xx', value: status_distribution.find((s) => s.label === '3xx')?.count ?? 0, color: 'var(--color-series-2)' },
                { label: '4xx', value: status_distribution.find((s) => s.label === '4xx')?.count ?? 0, color: 'var(--color-caution)' },
                { label: '5xx', value: status_distribution.find((s) => s.label === '5xx')?.count ?? 0, color: 'var(--color-critical)' },
              ]}
            />
          </div>
        </Panel>
      )}

      <Section>
        <Breakdown title="By domain" rows={traffic?.by_domain} />
        <Breakdown title="By method" rows={traffic?.by_method} />
      </Section>

      <Section>
        <Breakdown title="By client" rows={traffic?.by_client} />
        <Breakdown title="Bandwidth by domain" rows={traffic?.by_domain} unit="bytes" />
      </Section>

      <Panel className="mt-4" padded={false}>
        <PanelHeader
          title="By route"
          description={
            traffic?.detail_truncated
              ? 'Request-level breakdowns cover only the retention window; route figures cover the whole range.'
              : undefined
          }
        />
        <RouteTable routes={traffic?.by_route} />
      </Panel>
    </>
  )
}
