# Configure Janus from YAML

Janus can seed gateway resources from a versioned YAML file. The file creates
**desired state in Janus**; it does not deploy that state to Caddy. Use
`janus config file plan` to preview missing resources and
`janus config file apply` to create them. Then run the existing
`janus config apply --gateway SLUG` to validate and deploy each gateway.

## What the resources mean

- A **gateway** is one Caddy instance Janus manages. Its slug identifies the
  instance. Its routes, upstreams, and rate limits are scoped to it.
- An **upstream** is a pool of backend targets. A proxy route references an
  upstream by slug and sends matching requests to its targets.
- A **route** matches a path and optionally HTTP methods, then proxies or
  redirects the request. Higher `priority` routes run first.
- A **rate limit** is a request ceiling for one gateway. The rule is stored
  even if the gateway's Caddy build lacks the `caddy-ratelimit` plugin; the
  plugin is required for enforcement.
- An **API key** identifies a gateway **consumer**, not a gateway or
  upstream. Janus keys are listed at the top level. They are separate from the operator login used
  to run the setup commands. An API key name and its scopes go in YAML; its
  secret is generated at issuance and must not be committed to the file.

See [examples/janus.yml](../examples/janus.yml) for a complete sample. YAML
uses indentation for nesting and `-` for list items. Strings such as `:8080`
should be quoted because `:` has meaning in YAML.

## File structure

```yaml
version: 1
gateways:
  - slug: edge
    name: Edge
    listen: ':8080'
    upstreams:
      - slug: orders
        name: Orders API
        targets: [127.0.0.1:9000]
    routes:
      - slug: orders-api
        name: Orders route
        path: /api/orders*
        upstream: orders
api_keys:
  - name: orders-client
    scopes: [orders:read]
```

| Top-level field | Required | Purpose |
|---|---|---|
| `version` | Yes | Integer `1`, the only supported schema version. |
| `gateways` | No | List of gateways to register, with their child resources. Empty by default. |
| `api_keys` | No | List of global gateway-consumer credentials to issue. Empty by default. |

### Gateway fields

| Field | Required | Purpose |
|---|---|---|
| `slug` | Yes | Stable identity, unique across Janus. Lowercase letters, digits, and hyphens, at most 100 characters. Keep it unchanged on later applies. |
| `name` | Yes | Display name, unique across Janus, at most 100 characters. |
| `admin_url` | No | Caddy admin API URL for this instance. Omit to use Janus's global `CADDY_ADMIN_URL`. |
| `listen` | No | Address for the Janus-owned Caddy HTTP server, for example `':8080'`. Defaults to `:8080`. |
| `region` | No | Operator-facing region label, at most 60 characters. |
| `upstreams` | No | Backend pools for this gateway; defaults to empty. |
| `routes` | No | Routes for this gateway; defaults to empty. |
| `rate_limits` | No | Request limits for this gateway; defaults to empty. |

### Upstream and target fields

| Field | Required | Purpose |
|---|---|---|
| `slug` | Yes | Stable upstream identity within its gateway, at most 100 characters. |
| `name` | Yes | Pool display name, at most 100 characters. |
| `policy` | No | Caddy selection policy, such as `weighted_round_robin` (the default). Choose a policy supported by your Caddy build. |
| `health_path` | No | Path Caddy probes for active health checks, such as `/health`. Omit to disable active checks. |
| `targets` | Yes | Nonempty list of backend dials. Each dial must be unique within the pool. |

`targets` accepts `host:port` strings or mappings with a `dial` and optional
positive integer `weight` (default `1`). Weights matter with
`weighted_round_robin`; Caddy dials the target addresses, not Janus. Existing
upstream targets are not modified by a later apply.

### Route fields

| Field | Required | Purpose |
|---|---|---|
| `slug` | Yes | Stable route identity within its gateway, at most 120 characters. |
| `name` | Yes | Display name, at most 120 characters. |
| `path` | No | Caddy path matcher; defaults to `/*` and must start with `/`. A more specific route should have higher priority than a catch-all. |
| `methods` | No | Comma-separated HTTP methods such as `GET,POST`; omission matches all methods. |
| `priority` | No | Nonnegative integer, default `0`; higher routes are evaluated first. |
| `action` | No | `proxy` (default) or `redirect`. A proxy needs `upstream`; a redirect needs `redirect_to`. |
| `upstream` | For proxy | Slug of an upstream in this gateway. It may already exist or be declared earlier in this file. |
| `redirect_to` | For redirect | Location to send the client to. |
| `auth_policy` | No | `public` (default), `api_key`, `jwt`, `user`, `role`, or `scope`. The generated Caddy route checks that the relevant credential header is present; it does not verify the secret or its scopes. Your upstream must do that. |
| `timeout_seconds` | No | Positive request timeout in seconds, default `30`. |
| `retries` | No | Nonnegative number of proxy retries, default `0`. |
| `strip_prefix` | No | Path prefix to remove before proxying. |

Routes are created in the Janus database first. They do not affect live
traffic until `janus config apply --gateway SLUG` succeeds. The YAML format
currently covers only route fields supported by Janus's route-create API.
Manage domains, rewrites, headers, canaries, maintenance, and other route
settings in the dashboard or API.

### Rate-limit fields

| Field | Required | Purpose |
|---|---|---|
| `name` | Yes | Identity within the gateway, at most 120 characters. |
| `key` | No | Counter identity: `ip` (default), `api_key`, `user`, `route`, `domain`, `client`, or `global`. |
| `limit` | Yes | Positive request count allowed per window. |
| `window_seconds` | No | Positive window duration, default `60` seconds. |
| `burst` | No | Nonnegative extra allowance above the sustained rate, default `0`. |

### API-key fields

| Field | Required | Purpose |
|---|---|---|
| `name` | Yes | Stable key label, unique among keys in the file, at most 120 characters. |
| `scopes` | No | List of unique scope strings. Empty by default. These are application-specific edge scopes for routes using scope authentication; they are not Janus operator permissions. |
| `expires_in_days` | No | Nonnegative lifetime starting when the key is issued. `0` or omission means no expiry. Reapply does not renew an existing key. |

Unlike Beacn's fixed scope catalogue, Janus stores scope strings supplied by
the operator. Choose names meaningful to your upstream authorization logic.
The generated Caddy route does not compare these scopes. A key's `jan_...`
secret is returned only at creation or explicit rotation. A later list or
apply can show its prefix but cannot recover the secret.

## Plan, apply, then deploy

```bash
janus login --url https://janus.example.com
janus config file plan path/to/janus.yml
janus config file plan path/to/janus.yml --json
janus config file apply path/to/janus.yml
janus config validate --gateway edge
janus config apply --gateway edge --yes
```

The operator needs read permissions for the included resource types; apply
also needs their write permissions. The commands use Janus's authenticated
HTTP API and retain its audit trail. File validation rejects unknown or
duplicate fields and invalid values before any write. `plan` reports
`create` or `skip` for every declared resource. Apply creates gateways,
upstreams, routes, rate limits, then keys. It prints each newly issued full
key immediately as `NEW KEY name: jan_...`; save it to your secret manager and
avoid putting this output in shared logs.

Apply is repeatable for missing-resource setup. Existing gateways, upstreams,
routes, and rate limits are skipped by identity, even if their YAML fields
changed. An active same-name key with the same scopes is skipped; inactive or
differently scoped same-name keys cause an error. Use a new name for a
replacement, then retire the old key separately. If a request fails partway,
rerun apply: completed resources will be skipped. This setup step does not
remove resources absent from the file.

`janus config file apply` **does not deploy**. Review the generated
configuration with `janus config show` or `janus config validate`, then run
`janus config apply --gateway SLUG` for each gateway. Caddy validation and
deployment can still fail, for example when the installed Caddy build lacks a
required plugin; Janus leaves the previous live configuration in place.
