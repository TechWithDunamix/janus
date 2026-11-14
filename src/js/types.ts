/**
 * The shapes the server sends.
 *
 * Written by hand rather than generated, and kept deliberately partial: these
 * describe the props the pages actually read, not every column of every model.
 * A generated mirror of the database would be larger, would need regenerating
 * on every migration, and would tell a reader nothing about which fields a
 * screen depends on.
 */

export type SyncState = 'SYNCED' | 'SYNCING' | 'FAILED' | 'DRIFTED'
export type Health = 'healthy' | 'warning' | 'critical'

export type Gateway = {
  id: number
  name: string
  slug: string
  region: string | null
  enabled: boolean
  sync_state: SyncState
  sync_error: string | null
  maintenance: boolean
  simulated: boolean
  listen: string
  last_synced_at: string | null
}

export type SharedProps = {
  auth: {
    user: { id: number; name: string; email: string; title: string | null; is_superuser: boolean } | null
    permissions: string[]
    roles: string[]
  }
  gateways: { list: Gateway[]; current: Gateway | null }
  app: { name: string; env: string; simulated: boolean }
  flash?: Record<string, string>
  errors?: Record<string, string>
}

export type Range = {
  key: string
  label: string
  since: string
  until: string
  bucket_seconds: number
}

export type Delta = { absolute: number; percent: number | null } | null

export type Summary = {
  requests: number
  successful: number
  errors_4xx: number
  errors_5xx: number
  error_rate: number
  server_error_rate: number
  rps: number
  avg_latency_ms: number
  max_latency_ms: number
  p50: number
  p95: number
  p99: number
  bytes_in: number
  bytes_out: number
  blocked: number
  rate_limited: number
  timeouts: number
  active_routes: number
  total_routes: number
  active_upstreams: number
  simulated: boolean
  deltas: Record<string, Delta>
}

export type SeriesPoint = {
  t: string
  requests: number
  rps: number
  successful: number
  errors_4xx: number
  errors_5xx: number
  error_rate: number
  avg_latency_ms: number
  p95: number
  p99: number
  bytes_out: number
  blocked: number
  rate_limited: number
}

export type RouteRow = {
  id: number | null
  name: string
  slug: string | null
  label: string
  method: string
  path: string
  domain: string | null
  upstream: string | null
  enabled: boolean
  health: Health
  requests: number
  rps: number
  error_rate: number
  server_error_rate: number
  errors_4xx: number
  errors_5xx: number
  avg_latency_ms: number
  p50: number
  p95: number
  p99: number
  max_latency_ms: number
  bytes_out: number
  blocked: number
  rate_limited: number
  timeouts: number
}

export type ProblematicRoute = RouteRow & {
  score: number
  status: 'CRITICAL' | 'WARNING' | 'OK'
  components: {
    server_errors: number
    latency: number
    client_errors: number
    timeouts: number
    traffic_share: number
  }
}

export type Alert = {
  id: number
  kind: string
  severity: string
  title: string
  detail: string | null
  observed: number | null
  threshold: number | null
  opened_at: string
}

export type Anomaly = {
  id: number
  kind: string
  detail: string | null
  observed: number
  baseline: number
  deviation: number
  verdict: string
  detected_at: string
}

export type Deployment = {
  id: number
  version: number | null
  summary: string | null
  status: string
  stage: string
  error: string | null
  origin: string
  actor: string
  started_at: string
  finished_at?: string | null
  duration_ms: number | null
}

export type GatewayHealth = {
  reachable: boolean
  version: string | null
  error: string | null
  simulated: boolean
  sync_state: SyncState
  sync_error: string | null
  last_synced_at: string | null
  targets_total: number
  targets_unhealthy: number
  unhealthy: { dial: string; upstream_id: number; error: string | null }[]
  foreign_servers: string[]
  capabilities: string[]
  rate_limiting_available: boolean
}
