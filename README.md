# Janus

Janus is a terminal operated control plane for Caddy gateways. It manages gateway configuration, security rules, team access and traffic analytics through a CLI and a terminal interface. Caddy remains the data plane. Janus serves a bearer authenticated JSON API and health endpoint for the CLI and automation; it has no browser dashboard.

## Install and start

Requires Python 3.11+. Install Caddy for validation and gateway operation. PostgreSQL and Redis are available for production; local development can use SQLite.

```sh
cd janus
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
# Set DATABASE_URL and CADDY_ADMIN_URL for your environment.
janus migrate
janus admin create --email you@example.com
janus serve --host 127.0.0.1 --port 8000
```

In another terminal, run `janus login --email you@example.com`. Set `JANUS_URL` when connecting to a remote control plane. Local commands read `$JANUS_ENV_FILE`, `/etc/janus/janus.env`, then `./.env`. Run one `janus scheduler` process for periodic work, and `janus worker` when using a queued worker backend.

## Two ways to operate

**Commands** do one thing and exit, so they work in scripts and CI. Lists print aligned tables; add `--json`
for machine-readable output. `janus commands` lists them all, `janus commands routes` one group, and `--help`
on any command shows its options. Passwords come from a hidden prompt, or `$JANUS_PASSWORD` where there is no
terminal.

**`janus tui`** is the console for watching and operating gateways live. It signs in with the same
`janus login` and permissions, and has a tab for each area:

| Tab | What you see | Keys |
| --- | --- | --- |
| Overview | Caddy reachability and sync, traffic, errors, latency, blocked and limited requests, open alerts, recent deploys | |
| Requests | the request log, newest first; filter with `/api`, `status:5xx`, `status:404`, `method:POST`, `client:1.2.3.4` | `/` filter · Enter open |
| Routes | routes by priority | `t` on/off · `x` delete |
| Upstreams | pools, targets and their health | `t` on/off |
| Domains | hostnames and TLS state | `t` on/off · `x` delete |
| Security | allow/block rules and clients by traffic | `b` block (the selected client, or any address) · `x` remove rule · Enter client detail |
| Policies | `IF subject operator value THEN action` rules | `t` on/off · `x` delete |
| Config | the pending diff and the version history | `D` deploy |
| Audit | who changed what | |

Changes are saved as desired state; `D` deploys them after you have seen the diff. `M` puts the whole gateway
into maintenance (or takes it out) and deploys at once. `g` switches gateway, `r` refreshes, `l` pauses the
live refresh, `q` quits. Anything that cannot be undone asks first.

## Command map

| Area | Examples |
| --- | --- |
| Session | `login`, `logout`, `whoami` |
| At a glance | `overview`, `requests --status 5xx`, `requests --follow` |
| Gateways | `gateways list`, `gateways show`, `gateways status`, `gateways create`, `gateways delete`, `gateways maintenance on/off` |
| Routing | `routes list`, `routes show`, `routes create`, `routes update`, `routes enable`, `routes disable`, `routes delete` |
| Upstreams | `upstreams list`, `upstreams show`, `upstreams create`, `upstreams update`, `upstreams health`, `upstreams delete` |
| Domains | `domains list`, `domains create HOSTNAME --tls auto`, `domains update ID`, `domains delete ID` |
| Security | `security overview`, `security clients`, `security client ID`, `security block`, `security unblock`, `security allow`, `security blocklist`, `security allowlist`, `ratelimit list/create/update/delete`, `keys list/create/rotate/revoke` |
| Policies | `policies list`, `policies create NAME --subject path --operator matches --value '/admin*' --action block`, `policies update ID`, `policies delete ID` |
| Team | `users list/create/enable/disable/delete/role`, `roles list/create/permissions/assign`, `audit list` |
| Analytics | `analytics overview/routes/errors/clients/traffic/problematic/performance` |
| Configuration | `config show`, `config validate`, `config diff`, `config apply`, `config history`, `config rollback`, `config file plan`, `config file apply` |
| Operations | `modules list/build/declare`, `migrate`, `collect`, `work`, `scheduler`, `worker`, `serve` |

All administrative API commands authenticate as a user and enforce that user's permissions. `admin create` is restricted to initial local bootstrap. See `janus commands GROUP` and individual `--help` for exact arguments.

## Deployment

`deploy/install.sh` installs the Python service and systemd units. The Docker image includes Python and Caddy for config validation; it does not build JavaScript. Configure TLS at the proxy for remote API access. The control API is at `/api` and service health at `/health`.

- [Configuration file](docs/CONFIG_FILE.md)
- [Example config](examples/janus.yml)
- [Deployment guide](deploy/README.md)
