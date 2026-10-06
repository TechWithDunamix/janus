<div align="center">

# Janus

**API gateway control plane and observability platform**

Manages [Caddy](https://caddyserver.com). Owns your routing configuration, validates it,
deploys it, verifies it landed — then reads back what actually happened at the edge.

[Architecture](#architecture) · [Quick start](#quick-start) · [How it works](#how-it-works) · [CLI](#cli) · [Deploying](#deploying)

</div>

---

Caddy does the proxying. **No Janus process sits in the request path.** That is the
one thing to understand about this design, and everything else follows from it: if
the control plane is down, your gateway keeps serving traffic on the last
configuration it was given.

Built on the [Sillo](https://sillo.build) framework, which supplies the routing,
middleware, sessions, authentication, permissions, ORM, validation, console, queue and
scheduler. Janus adds a domain on top of it and no framework of its own.

## Architecture

```
                         JANUS
                    Control Plane
                          |
        +-----------------+------------------+
        |                 |                  |
       Auth          Configuration       Analytics
        |                 |                  |
        +-----------------+------------------+
                          |
                          ▼
                    Caddy Data Plane
                          |
             +------------+------------+
             |            |            |
             ▼            ▼            ▼
          Service A    Service B    Service C
```

Configuration flows one way — the Janus database, through the configuration engine,
into Caddy. What comes back is only ever *compared*: drift detection reports a
difference but never merges it in. A control plane that adopted hand-edits would make
its own history a lie.

## Quick start

Requires Python 3.11+, Node 22+, and `caddy` on your PATH.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,server]"
npm install

./janus migrate            # schema + permission catalogue
./janus admin create       # the first account; refuses once one exists
```

The same pipeline — and every day-two task — is in the [`Makefile`](Makefile):
`make help` lists them. With a virtual environment active, `make deploy`
installs the packages, builds the front end, writes `.env`, migrates, and then
deploys the app (systemd units when installed, foreground otherwise), and
`make caddy-setup` rebuilds Caddy with the plugins Janus needs.

Start Caddy with an empty configuration and its admin API open — Janus writes the rest:

```bash
caddy run --config docker/caddy-bootstrap.json
```

Then Janus:

```bash
npm run dev                # Vite, on :5173
./janus serve              # Janus, on :8000
```

Define a gateway, upstreams and routes, then `./janus config apply`.

**Or try it with demo data:** `./janus seed` creates a gateway, seven upstreams, nine
routes, security rules, 48 hours of traffic, and one account per role.

> Seeded *traffic* is marked `simulated` on every row and labelled in the UI. The
> gateway itself is real — point it at your Caddy and deploy. An observability tool
> that showed invented traffic as observation would be worse than one showing nothing.

## Features

| | |
|---|---|
| **Gateway** | Multiple Caddy instances, routes with priority ordering, upstream pools with weighted load balancing and health checks, domains and TLS modes, header transformation, rewrites, redirects, timeouts, retries, canary traffic splitting, maintenance mode |
| **Configuration** | Versioned deployments holding the complete payload, diffs, rollback, drift detection, four sync states |
| **Analytics** | Per-request records and per-minute rollups from Caddy's access log and metrics. Traffic, errors, latency percentiles, bandwidth, clients — by route, domain, method, client and API key, over seven ranges |
| **Security** | Allowlists and blocklists by address or CIDR, scoped and expiring. Rate limits by IP, key, user, route or domain. Conditional policies. API keys with scopes, rotation and revocation. Threshold alerts and statistical anomalies, kept separate |
| **Team** | Users, roles, permissions, sessions, sign-in activity, audit trail with before/after values |
| **Modules** | Detects which Caddy plugins a build provides, explains what each missing one would unlock, and generates the rebuild command — either `scripts/setup-caddy.sh` (build-or-download, back up, swap, restart, redeploy in one shot) or a plain `xcaddy` line. Add and Remove are staged on the screen |
| **Docs** | 20 pages — about 24,000 words — served at `/docs` in their own reading layout |
| **CLI** | A first-class `janus` command covering all of the above |

## How it works

### Janus writes only what Janus owns

Two paths inside Caddy's configuration, both named after `CADDY_SERVER_NAME`:

```
apps/http/servers/<name>     the routes, matchers and handlers
logging/logs/<name>          the access log Janus ingests
```

Nothing else is ever written — there is no code path that replaces the configuration
root. A Caddy instance can carry other servers, apps and loggers; Janus reads the whole
configuration for drift detection and replaces only those two subtrees. The Health
screen lists any foreign servers it finds, so you can see Janus is a tenant rather than
the owner.

### A bad configuration cannot take down a running gateway

Three things, not one:

1. **Generation and validation happen inside Janus.** A configuration that fails either
   never reaches the gateway.
2. **`caddy validate` runs the real thing.** When a binary is reachable the candidate is
   provisioned in a throwaway process — the same code path a real load takes. Without
   one, Janus falls back to structural checks and *says which it did*. A caller has to
   be able to tell a proven configuration from a plausible one.
3. **Caddy loads atomically.** A rejected payload leaves the previous configuration
   serving. That is Caddy's behaviour, and `tests/test_caddy.py` asserts it against a
   real binary so the claim stays true.

A failed deployment records the stage it stopped at: `validating` means the gateway was
never touched, `applying` means it refused and kept what it had.

### Rollback sends stored bytes

A version is not a diff — it holds the complete generated configuration. Restoring
version 14 sends exactly what version 14 sent, regardless of what the models say now.
Rolling back creates a *new* version carrying that payload, so history reads forward.

### Rate limiting needs a plugin, and Janus says so

Stock Caddy has no `http.handlers.rate_limit` — it ships in
[caddy-ratelimit](https://github.com/mholt/caddy-ratelimit). Janus detects which handler
modules a build provides and **declines to emit a handler it knows would be rejected**.
Limits are stored, displayed, and reported as *not enforced*, with a banner on the
screen and a warning on every deployment. Rate-limit analytics work either way, because
they come from 429s in the access log.

### Plugins are compiled in, so there is no install button

Caddy is a single static binary: plugins are compiled in, not loaded at runtime. Janus
therefore does the four things that are honestly possible — **detects** what a build
provides from `caddy list-modules` and the Go module graph, **explains** what each
missing plugin would unlock, **generates** the `xcaddy` command, and **records** what a
remote build has when it cannot introspect one.

```console
$ janus modules list
  Caddy    v2.11.4
  Modules  128 (132 standard)
  Source   detected from the local caddy binary

 PLUGIN                  STATUS   PROVIDES
 Rate limiting           —        http.handlers.rate_limit
 JWT authentication      —        http.authentication.providers.jwt
```

The generated command includes **every plugin already installed**, because `xcaddy`
builds from exactly the packages named — a command listing only the new plugin produces
a binary missing everything the current one has, which succeeds and behaves like a
downgrade.

The screen offers two forms of that command. `scripts/setup-caddy.sh` does the whole
job on the host Caddy runs on — build with `xcaddy` if Go is present, otherwise download
a custom build from caddyserver.com, then back up the current binary, swap the new one
in, restart Caddy, and redeploy; `--plain` builds a stock Caddy (how you uninstall the
last plugin) and `--rollback` restores the backup. The other form is the bare `xcaddy`
line, if you would rather drive the swap yourself.

```console
$ ./scripts/setup-caddy.sh --with github.com/mholt/caddy-ratelimit --restart --redeploy
```

A remote gateway is a different binary, so its modules can be *declared* rather than
detected. Janus labels which, and skips local `caddy validate` for a declared build —
this host's binary is not a valid judge of one you have told Janus is different.

### An allowlist is a fence, not an exemption

Any allow rule at a scope makes that scope **deny-by-default** — everything outside the
allowed ranges is refused. That is what an allowlist means, and it is rarely what you
want globally on a public gateway. Scope allow rules to a route or a domain. The UI says
so where the rule is created, because the failure mode is a gateway answering 403 to
everyone.

### The analytics split

| Table | Grain | Answers | Retention |
|---|---|---|---|
| `request_logs` | One row per request | *Which client got the 502s* | Hours |
| `route_rollups` | One row per route per minute | Every chart and time range | Months |

Rollups carry a **latency histogram**, not an average, because averages do not compose:
the mean of per-minute means is not the mean, and a P95 of P95s is not a percentile.
Percentiles sum histograms across the range and interpolate once.

Each gateway reads its **own** log file. Pointing two at one file ingests every request
twice, once under each — a gateway reporting traffic it never served, silently.

The collector drops everything except a small header allowlist before writing a row: no
`Authorization`, no `Cookie`, no API key value. A key is reduced to its prefix, so
traffic can be attributed to it without the key being readable in a table the whole team
can see. That filtering happens at the boundary, because a secret that reaches the
database is already leaked.

### The CLI is not an RBAC bypass

Every administrative command works through the same JSON API the dashboard's
authorization guards:

```console
$ janus routes delete 4 --yes
✗ You do not have permission to do that (routes.write).
```

That is structural, not a check inside each command: `ApiCommand` has no database
connection, and `tests/test_cli.py` asserts every command outside a named list of local
ones is built on it. The exceptions — `migrate`, `seed`, `serve`, `worker`,
`scheduler`, `collect` — do nothing a signed-in user could do instead. `admin create`
is the bootstrap and **refuses once any account exists**.

Roles are `sillo.permissions.Group` rows, permissions are `Permission` rows, and one
`require()` is the choke point the route guard, the API and the CLI all reach.

### Alerts and anomalies are different things

An **alert** is a threshold someone chose being crossed. An **anomaly** is Janus
observing that the last few minutes do not look like the preceding hour — often nothing
at all. Presenting the second as the first is how a dashboard teaches people to ignore
it, so they are separate tables, screens and words. An anomaly is promoted to an
incident only by a person.

## CLI

```bash
janus login                      # token stored 0600 under ~/.config/janus
janus whoami · logout

janus gateways   list|show|create|delete|status
janus routes     list|show|create|update|delete|enable|disable
janus upstreams  list|show|create|delete|health
janus config     show|validate|diff|apply|history|rollback
janus security   block|unblock|allow|deny|blocklist|allowlist
janus ratelimit  list|create|update|delete
janus keys       list|create|revoke|rotate
janus users      list|create|enable|disable|delete|role
janus roles      list|create|permissions|assign
janus analytics  overview|routes|errors|clients|traffic|problematic|performance
janus migrate|seed|work|collect|serve|worker|scheduler
```

Every command takes `--json`. Destructive commands confirm, take `--yes` for
automation, and **refuse rather than assume yes** in a non-interactive shell without it.
Passwords are always prompted, never flags — a password on the command line ends up in
shell history and the process list.

```console
$ janus analytics problematic

─── MOST PROBLEMATIC ROUTES ────────────────────────────────────────

POST/GET /api/orders*
  Error Rate  17.33%
  5xx         26
  P95         461ms
  P99         497ms
  Requests    150
  Status      CRITICAL
```

The ranking blends server errors, latency and timeouts weighted by traffic share, and
publishes its component scores. A route at 100% errors and eleven requests is not your
biggest problem; a ranking that cannot explain itself is one operators stop trusting.

## Deploying

### On a server (systemd)

One command on a fresh Debian/Ubuntu or RHEL/Fedora host installs **everything**
— Python dependencies (with the `server`, `postgres` and `redis` extras),
PostgreSQL, Redis, Caddy (the data plane), the built front end and the systemd
units:

```bash
git clone https://github.com/TechWithDunamix/janus && cd janus
sudo deploy/install.sh --postgres --redis --domain janus.example.com
```

Caddy is installed by default — Janus drives its admin API on
`127.0.0.1:2019`. Pass `--no-caddy` only if you manage Caddy elsewhere (then
set `CADDY_ADMIN_URL` yourself). The script is **idempotent** — to upgrade,
`git pull` and run it again. It uses [uv](https://docs.astral.sh/uv/) for the
virtualenv, installing it into `/usr/local/bin` if absent.

**What gets installed**

| Path | What |
|---|---|
| `/opt/janus` | the app + its virtualenv (`.venv`, deps from `.[server,postgres,redis]`) |
| `/etc/janus/janus.env` | configuration, read by every unit — and auto-loaded by the `janus` CLI, so `sudo -u janus janus …` hits the same database as the service |
| `janus` (system user) | runs every service; no login shell |
| Caddy | installed + enabled; admin API on `127.0.0.1:2019` |
| `static/build/` | front end built with [Bun](https://bun.sh) (installed if absent; `--no-build` to skip) |

**systemd units**

| Unit | Role |
|---|---|
| `janus-migrate.service` | `oneshot` — `janus migrate`; the only process that creates schema |
| `janus-web.service` | dashboard + JSON API: `uvicorn app.main:app --workers ${JANUS_WEB_WORKERS}` |
| `janus-scheduler.service` | access-log ingestion, upstream health, drift, alerts — **exactly one** |
| `janus-worker.service` | drains job queues; enabled only with `QUEUE_BACKEND=redis` |
| `janus.target` | convenience: `systemctl restart janus.target` |

**`.env` setup**

On first run the installer writes `/etc/janus/janus.env` from
[`deploy/janus.env.example`](deploy/janus.env.example), generates a
`SECRET_KEY`, and points `DATABASE_URL` / `QUEUE_BACKEND` at the local
services. It **never overwrites** an existing file, so re-running the
installer keeps your configuration. What a server cares about:

| var | value on a server |
|---|---|
| `APP_ENV` / `APP_DEBUG` | `production` / `false` — production refuses to start otherwise |
| `SECRET_KEY` | generated by the installer — Janus refuses the development default |
| `APP_URL` / `CORS_ORIGINS` | `https://<domain>` — set by `--domain` |
| `COOKIE_SECURE` | `true` |
| `DATABASE_URL` | local PostgreSQL (`--postgres` creates DB and password) |
| `DB_GENERATE_SCHEMAS` | `false` — only `janus-migrate.service` creates schema |
| `QUEUE_BACKEND` / `REDIS_URL` | `redis` / local — keeps `janus-worker` enabled |
| `CADDY_ADMIN_URL` | `http://127.0.0.1:2019` — the Caddy the installer set up |
| `CADDY_ACCESS_LOG` | `/opt/janus/storage/access.log` — what the collector reads; must be readable by Janus |
| `CADDY_SIMULATE` | `false` — Janus refuses to start in production with the simulator on |
| `JANUS_HOST` / `JANUS_PORT` | `127.0.0.1:8000` — behind your reverse proxy |
| `VITE_DEV` | `false` (serve the built `static/build/` assets) |

Edit the file, then apply: `systemctl restart janus.target`. The Janus
dashboard still needs its own TLS front — a Caddy site or proxy pointing at
`127.0.0.1:8000`.

**First run**

```bash
sudo -u janus /opt/janus/.venv/bin/janus admin create you@example.com
curl -s http://127.0.0.1:8000/health
sudo -u janus /opt/janus/.venv/bin/janus config show   # generated Caddy config
sudo -u janus /opt/janus/.venv/bin/janus config apply  # deploy it to the gateway
```

Unit details, queue-backend choices and uninstall steps:
[`deploy/README.md`](deploy/README.md). For local development copy the root
[`.env.example`](.env.example) to `.env` instead.

### Docker

```bash
docker compose up --build
```

Caddy, Postgres, Redis, Janus, a worker and a scheduler. No Kubernetes, no Kafka, no
Elasticsearch — a control plane and an analytics store need neither. Caddy writes its
access log to a shared volume and the collector reads from it; that is the whole
analytics pipeline, with no broker, agent or sidecar.

### Processes

| Process | Does | How many |
|---|---|---|
| `janus serve` | Dashboard and API | As many as you like |
| `janus scheduler` | Ingestion, health, drift, alerts | Exactly one |
| `janus worker` | Drains job queues | As many as you like |

Only the web process owns the schema — workers run with `DB_GENERATE_SCHEMAS=false`,
because creating tables concurrently races on the implicit row type behind
`CREATE TABLE IF NOT EXISTS` and the error names nothing about the race.

With `APP_ENV=production`, Janus **refuses to start** if the secret key is the
development default, if debug is on, if cookies are not secure, or if the Caddy
simulator is enabled. Every one of those is a way to be quietly insecure, and a log line
at startup is not where anyone will see it.

Settings are documented in [`.env.example`](.env.example) (local development),
[`deploy/janus.env.example`](deploy/janus.env.example) (server) and
[`app/config.py`](app/config.py).

## Layout

| Path | Contains |
|---|---|
| [`app/caddy/`](app/caddy/) | The only code that talks to Caddy. `manager.py` the interface, `config_builder.py` a pure desired-state → JSON function, `transport.py` the HTTP client and simulator |
| [`app/services/`](app/services/) | Configuration pipeline, analytics queries, security, API keys, alerts, health |
| [`app/collector/`](app/collector/) | Access-log and Prometheus ingestion |
| [`app/authz.py`](app/authz.py) | Permission catalogue, default roles, the one `require()` |
| [`app/docs/`](app/docs/) | The 19 in-dashboard documentation pages |
| [`app/cli/`](app/cli/) | The `janus` console, on `sillo.console` |
| [`database/models/`](database/models/) | Identity, gateway, security, analytics, configuration |
| [`routes/web/`](routes/web/) | Inertia pages and the route guard |
| [`routes/api/`](routes/api/) | The JSON API — what the CLI calls |
| [`views/`](views/) | React screens, layouts, UI kit |
| [`js/app.css`](js/app.css) | Design tokens. The whole visual identity |

## Tests

```bash
pytest            # 244 tests
npm run typecheck
ruff check .
```

The Caddy tests run twice over: against an in-process simulator for the pipeline, and —
when a `caddy` binary is present — against the real thing for the claims Janus makes
about Caddy's behaviour.

## Documentation

Twenty pages at `/docs`, in their own reading layout rather than inside the operational
shell — docs are read, not operated, and the dashboard's twenty-eight links beside a page
of prose is navigation nobody uses. Sections cover getting started, the gateway, security,
observability and operations, with search, prev/next and an on-page outline.

Pages are authored as structured blocks rather than Markdown, so they render with the same
components as the rest of the application and there is no parser or sanitiser in the path.
`tests/test_docs.py` asserts unique slugs, known sections, resolving internal links, and
that no page uses a block type the renderer has no branch for.

## Design

The interface is the commerce dashboard's component system — same props, same geometry,
same density — recoloured with [nobus.io](https://www.nobus.io)'s palette: a vivid blue
(`#0664f7`), deep navy ink (`#000026`), blue-tinted canvas (`#f9fafe`), and their green
and orange for state. Components name semantic tokens and never a hue, which is why
re-skinning forty screens meant editing one block in `js/app.css`.
