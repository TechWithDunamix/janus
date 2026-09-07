# Deploying Janus

A systemd deployment for a single Debian/Ubuntu or RHEL/Fedora host. Janus is
the control plane; **Caddy is the data plane** and the installer sets it up
unless you pass `--no-caddy`.

```bash
git clone https://github.com/TechWithDunamix/janus && cd janus
sudo deploy/install.sh --postgres --redis --domain janus.example.com
```

The script is **idempotent** — to upgrade, `git pull` and run it again.

## What gets installed

| Path | What |
|---|---|
| `/opt/janus` | the app + its virtualenv (`.venv`) |
| `/etc/janus/janus.env` | configuration, read by every unit; `SECRET_KEY` generated on first run |
| `janus` (system user) | runs every service; no login shell |
| Caddy | installed + enabled; admin API on `127.0.0.1:2019` (what Janus drives) |

### systemd units

| Unit | Role |
|---|---|
| `janus-migrate.service` | `oneshot` — `janus migrate`. Everything else requires it. |
| `janus-web.service` | the dashboard + JSON API: `uvicorn app.main:app --workers ${JANUS_WEB_WORKERS}`. |
| `janus-scheduler.service` | the periodic jobs — access-log ingestion, upstream health, drift detection, alerts, retention. **Exactly one** across the deployment. |
| `janus-worker.service` | drains the queues the scheduler and dashboard dispatch to. Only useful with `QUEUE_BACKEND=redis`; the installer leaves it disabled otherwise. |
| `janus.target` | convenience: `systemctl restart janus.target`. |

### Queue backend

- **`QUEUE_BACKEND=redis`** (recommended, `--redis`): the scheduler enqueues jobs
  and `janus-worker` runs them. Web, scheduler and worker can be separate hosts.
- **`QUEUE_BACKEND=memory`**: single process — the scheduler runs jobs in-process
  and `janus-worker` is left disabled.

## Common operations

```bash
systemctl status janus-web janus-scheduler
journalctl -u janus-web -f
systemctl restart janus.target                 # after editing /etc/janus/janus.env

sudo -u janus /opt/janus/.venv/bin/janus admin create you@example.com
sudo -u janus /opt/janus/.venv/bin/janus config show          # generated Caddy config
sudo -u janus /opt/janus/.venv/bin/janus config apply
```

## Caddy

The installer starts Caddy with its default config (admin API on `:2019`).
Janus writes **only** the server block it owns (`apps/http/servers/<name>`) and
its access log; it never PUTs the config root. If your Caddy runs elsewhere,
pass `--no-caddy` and set `CADDY_ADMIN_URL` in `/etc/janus/janus.env`.

The Janus *dashboard* still needs its own TLS front — a separate Caddy site or
proxy pointing at `127.0.0.1:8000`.

## Front end

`static/build/` is not committed. `install.sh` builds it with `npm ci && npm run
build` if Node is on the host; otherwise build elsewhere and `rsync` it into
`/opt/janus/`.

## Uninstall

```bash
systemctl disable --now janus-web janus-scheduler janus-worker janus-migrate
rm /etc/systemd/system/janus-*.service /etc/systemd/system/janus.target
systemctl daemon-reload
rm -rf /opt/janus /etc/janus
userdel janus
# then drop the janus postgres db/role; leave Caddy if other things use it
```
