/**
 * What every gateway screen renders when no gateway exists yet.
 *
 * A first-run state rather than an error: a fresh installation has no Caddy
 * registered, and every route table, chart and policy screen has nothing to be
 * about until one is. It says the two ways to fix that, both of which work.
 */

import { Head } from '@inertiajs/react'
import { Panel } from '@/views/ui/kit'
import { IconGateway } from '@/views/ui/icons'

export default function GatewayEmpty() {
  return (
    <>
      <Head title="No gateway" />
      <Panel className="mx-auto max-w-[520px] text-center">
        <div className="mx-auto mb-4 flex h-11 w-11 items-center justify-center rounded-full bg-brand-soft text-brand">
          <IconGateway className="h-5 w-5" />
        </div>
        <h1 className="text-[15px] font-semibold">No gateway is registered</h1>
        <p className="mt-2 text-[12.5px] text-ink-muted">
          Janus manages Caddy instances. Register one and every screen here starts having
          something to show.
        </p>
        <div className="mt-5 space-y-2 text-left">
          <p className="text-[11px] font-semibold uppercase tracking-[0.06em] text-ink-faint">
            From the command line
          </p>
          <pre className="mono overflow-x-auto rounded-md bg-sunken px-3 py-2.5 text-[12px]">
{`janus gateways create --name "Edge" --listen :8080
janus config apply`}
          </pre>
          <p className="text-[11px] font-semibold uppercase tracking-[0.06em] text-ink-faint">
            Or try it with demo data
          </p>
          <pre className="mono overflow-x-auto rounded-md bg-sunken px-3 py-2.5 text-[12px]">
{`janus seed`}
          </pre>
        </div>
      </Panel>
    </>
  )
}
