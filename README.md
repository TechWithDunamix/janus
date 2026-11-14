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

Requires Python 3.11+, Node 22+ (or Bun) to build the dashboard, and `caddy` on
your PATH.

```bash
git clone https://github.com/TechWithDunamix/janus.git && cd janus
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt   # pinned deps + this project (editable)
npm install                           # or: bun install
cp .env.example .env                  # local settings; real env vars always win

janus migrate              # schema + permission catalogue
janus admin create         # the first account; refuses once one exists
```

**Requirements files.** `requirements.txt` pins every runtime dependency
(Sillo, sillo-inertia, Tortoise, httpx, uvicorn, asyncpg, redis, …) and ends
with `-e .`, which installs Janus itself in editable mode: that puts `src/` on
the Python path so `uvicorn main:app`, `python -m cli` and the `janus` command
work from any directory. Run `pip install -r …` from the project root.
`requirements-dev.txt` adds pytest and ruff. Both are generated from
`pyproject.toml` by `scripts/update-requirements.sh` (`make requirements`,
needs [uv](https://docs.astral.sh/uv/)); with uv, `uv venv && uv pip install
-r requirements-dev.txt` is the fast path.

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
npm run dev                # Vite, on :5173 (or `npm run build` and VITE_DEV=false)
janus serve --reload       # Janus, on :8000
```

Define a gateway, upstreams and routes, then `janus config apply`.

**Or try it with demo data:** `janus seed` creates a gateway, seven upstreams, nine
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
| `/opt/janus` | the app + its virtualenv (`.venv`, the project installed with its `server`, `postgres` and `redis` extras) |
| `/etc/janus/janus.env` | configuration, read by every unit — and auto-loaded by the `janus` CLI, so `sudo -u janus janus …` hits the same database as the service |
| `janus` (system user) | runs every service; no login shell |
| Caddy | installed + enabled; admin API on `127.0.0.1:2019` |
| `static/build/` | front end built with [Bun](https://bun.sh) (installed if absent; `--no-build` to skip) |

**systemd units**

| Unit | Role |
|---|---|
| `janus-migrate.service` | `oneshot` — `janus migrate`; the only process that creates schema |
| `janus-web.service` | dashboard + JSON API: `uvicorn main:app --workers ${JANUS_WEB_WORKERS}` |
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

### On a server, step by step (no Docker, no installer)

The same result as the installer, by hand, on Debian/Ubuntu. The unit files in
`deploy/systemd/` expect the code in `/opt/janus`, a `janus` system user and
settings in `/etc/janus/janus.env`; adjust them if you choose other paths.

**1. Packages**

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-dev build-essential git \
                    postgresql redis-server caddy
curl -fsSL https://bun.sh/install | sudo env BUN_INSTALL=/usr/local bash   # front-end build
python3 --version          # 3.11 or newer
```

Rate limiting needs Caddy built with the `caddy-ratelimit` plugin;
`scripts/setup-caddy.sh` rebuilds the local binary (see *Plugins* above).

**2. User, code and dependencies**

```bash
sudo useradd --system --home-dir /opt/janus --shell /usr/sbin/nologin janus
sudo git clone https://github.com/TechWithDunamix/janus.git /opt/janus
sudo mkdir -p /opt/janus/storage && sudo chown -R janus:janus /opt/janus
cd /opt/janus
sudo -u janus python3 -m venv .venv
sudo -u janus .venv/bin/pip install --upgrade pip
sudo -u janus .venv/bin/pip install -r requirements.txt
sudo -u janus bun install --frozen-lockfile && sudo -u janus bun run --bun build
#   or, with Node 22+: sudo -u janus npm ci && sudo -u janus npm run build
```

**3. PostgreSQL**

```bash
DB_PASS=$(openssl rand -hex 16)
sudo -u postgres psql -c "CREATE ROLE janus LOGIN PASSWORD '$DB_PASS';"
sudo -u postgres createdb -O janus janus
```

**4. Settings** — start from the template and set the values in the table
below (`SECRET_KEY` from `openssl rand -hex 32`, `DATABASE_URL` with
`$DB_PASS`, your domain in `APP_URL`/`CORS_ORIGINS`):

```bash
sudo mkdir -p /etc/janus
sudo cp /opt/janus/deploy/janus.env.example /etc/janus/janus.env
sudo chown root:janus /etc/janus/janus.env && sudo chmod 0640 /etc/janus/janus.env
sudoedit /etc/janus/janus.env
```

systemd keeps everything after `=`, so keep comments on their own lines.

**5. Caddy** — the data plane. Janus manages Caddy only through its admin API
(`127.0.0.1:2019`, the packaged service's default) and writes nothing else. Caddy
writes each gateway's access log into `/opt/janus/storage/`, which Janus then
reads. Let the `caddy` user write there, and make new files inherit the `janus`
group (setgid); Janus asks Caddy to create them group-readable (`0640`):

```bash
sudo usermod -aG janus caddy
sudo chown janus:janus /opt/janus/storage && sudo chmod 2770 /opt/janus/storage
sudo systemctl enable --now caddy && sudo systemctl restart caddy
curl -s http://127.0.0.1:2019/config/ >/dev/null && echo "caddy admin API up"
```

**6. Schema, first account, services**

```bash
sudo -u janus /opt/janus/.venv/bin/janus migrate
sudo -u janus /opt/janus/.venv/bin/janus admin create you@example.com
sudo cp /opt/janus/deploy/systemd/janus-*.service /opt/janus/deploy/systemd/janus.target \
        /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now janus.target
curl -s http://127.0.0.1:8000/health
journalctl -u janus-web -f          # also: -u janus-scheduler, -u janus-worker
```

The CLI reads `/etc/janus/janus.env` itself, so it acts on the same database as
the services. `janus-worker` only has work with `QUEUE_BACKEND=redis`; run
exactly one `janus-scheduler`.

**7. TLS for the dashboard** — Janus listens on `127.0.0.1:8000`. Put a site in
front of it, for example in Caddy (a separate site from the gateways Janus
manages):

```
janus.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

or nginx:

```nginx
server {
    listen 443 ssl http2;
    server_name janus.example.com;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

**8. Upgrades and backups**

```bash
cd /opt/janus && sudo -u janus git pull
sudo -u janus .venv/bin/pip install -r requirements.txt
sudo -u janus bun install --frozen-lockfile && sudo -u janus bun run --bun build
sudo systemctl restart janus.target            # janus-migrate runs first

sudo -u postgres pg_dump -Fc janus > janus-$(date +%F).dump   # backup
```

Back up `/etc/janus/janus.env` with the database. Every deployed Caddy
configuration is stored as a version in the database, so a restore plus
`janus config apply` rebuilds the gateways.

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
[`src/core/config.py`](src/core/config.py).

## Layout

Everything that runs lives in `src/`: the Python packages and the React front
end side by side; feature areas are under `src/modules/`. Every module, class
and function carries a docstring.

| Path | Contains |
|---|---|
| [`src/main.py`](src/main.py) | ASGI entry point: `uvicorn main:app` |
| [`src/app.html`](src/app.html) | The Inertia root HTML view |
| [`src/js/`](src/js/) | Front-end entry, hooks, types, and `app.css` — the design tokens, the whole visual identity |
| [`src/views/`](src/views/) | React screens, layouts and the UI kit |
| [`src/public/`](src/public/) | Static files served at `/static` (favicon) |
| [`src/core/`](src/core/) | Settings, paths, app assembly, Inertia, jobs and the queue |
| [`src/database/`](src/database/) | Database wiring, models (identity, gateway, security, analytics, configuration), demo seeds |
| [`src/routes/web/`](src/routes/web/) | Inertia pages, form actions and the route guard |
| [`src/routes/api/`](src/routes/api/) | The JSON API — what the CLI calls |
| [`src/cli/`](src/cli/) | The `janus` console, on `sillo.console` |
| [`src/modules/caddy/`](src/modules/caddy/) | The only code that talks to Caddy. `manager.py` the interface, `config_builder.py` a pure desired-state → JSON function, `transport.py` the HTTP client and simulator, `modules.py` plugin detection |
| [`src/modules/services/`](src/modules/services/) | Configuration pipeline, analytics queries, security, API keys, alerts, health |
| [`src/modules/collector/`](src/modules/collector/) | Access-log and Prometheus ingestion |
| [`src/modules/auth/`](src/modules/auth/) | Permission catalogue, default roles, the one `require()` |
| [`src/modules/gateways/`](src/modules/gateways/) | Which gateway a request is acting on |
| [`src/modules/docs/`](src/modules/docs/) | The in-dashboard documentation pages |
| [`tests/`](tests/) | The test suite |
| [`deploy/`](deploy/) | Installer, systemd units, production env template |
| [`docker/`](docker/) | Caddy bootstrap config for Compose and manual setups |
| [`scripts/`](scripts/) | `setup-caddy.sh` (rebuild Caddy with plugins), `update-requirements.sh` |
| `static/build/`, `storage/` | Generated: compiled front end; SQLite, access logs, collector offsets |

## Tests

```bash
pytest            # 252 tests
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
