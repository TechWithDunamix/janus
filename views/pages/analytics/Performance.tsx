import { Head } from '@inertiajs/react'
import type { Range, RouteRow, SeriesPoint, Summary } from '@/js/types'
import { AnalyticsHeader, RouteTable, SummaryRow, TrafficChart } from '@/views/ui/analytics'
import { Panel, PanelHeader } from '@/views/ui/kit'

export default function Performance({
  range,
  summary,
  slow,
  series,
}: {
  range: Range
  summary: Summary
  slow?: RouteRow[]
  series?: SeriesPoint[]
}) {
  return (
    <>
      <Head title="Performance" />
      <AnalyticsHeader
        title="Performance"
        description="Latency, by percentile"
        range={range}
        simulated={summary.simulated}
      />

      <SummaryRow summary={summary} />

      <Panel padded={false}>
        <PanelHeader
          title="Latency over time"
          description="P95 is the line to watch: a fast median with a slow tail is what users complain about, and a mean hides exactly that."
        />
        <div className="p-5">
          <TrafficChart series={series} kind="latency" height={320} />
        </div>
      </Panel>

      <Panel className="mt-4" padded={false}>
        <PanelHeader title="Slowest routes" description="Ranked by P95, then P99." />
        <RouteTable routes={slow} columns="latency" />
      </Panel>
    </>
  )
}
