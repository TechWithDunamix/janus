#!/usr/bin/env bash
#
# Janus — one-shot installer / upgrader for a systemd host (Debian/Ubuntu or
# RHEL/Fedora). Run as root from a checkout:
#
#     sudo deploy/install.sh --postgres --redis
#
# What it does, idempotently (safe to re-run for an upgrade):
#   * installs prerequisites: python venv + build tools, Caddy (the data plane
#     Janus manages), and — when asked — Redis and PostgreSQL
#   * creates the `janus` system user, /opt/janus and /etc/janus
#   * syncs this checkout into /opt/janus and builds the venv
#   * builds the front end if Node is available
#   * writes /etc/janus/janus.env on first run (generates SECRET_KEY); never
#     overwrites an existing one
#   * installs the systemd units, runs migrations, starts the web tier, the
#     scheduler, and the worker (worker only when QUEUE_BACKEND=redis)
#
# Flags:
#   --redis            install + enable a local Redis (shared job queue)
#   --postgres         install + enable a local PostgreSQL, create the janus DB
#   --no-caddy         do NOT install Caddy (you manage it elsewhere)
#   --no-build         skip the front-end build even if Node is present
#   --app-dir DIR      install location (default /opt/janus)
#   --user NAME        service account (default janus)
#   --domain HOST      write APP_URL / CORS_ORIGINS for this host on first run
#
set -euo pipefail

APP=janus
APP_DIR=/opt/janus
APP_USER=janus
DO_REDIS=0 DO_PG=0 DO_CADDY=1 DO_BUILD=1 DOMAIN=""
PY_EXTRAS="server,postgres,redis"

log()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m  %s\n' "$*" >&2; }
die()  { printf '\033[1;31mxx\033[0m  %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run as root (sudo deploy/install.sh ...)"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --redis) DO_REDIS=1;;
    --postgres) DO_PG=1;;
    --no-caddy) DO_CADDY=0;;
    --no-build) DO_BUILD=0;;
    --app-dir) APP_DIR="$2"; shift;;
    --user) APP_USER="$2"; shift;;
    --domain) DOMAIN="$2"; shift;;
    -h|--help) sed -n '2,42p' "$0"; exit 0;;
    *) die "unknown flag: $1";;
  esac
  shift
done

SRC="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
[[ -f "$SRC/pyproject.toml" && -d "$SRC/app" ]] || die "run this from the janus checkout ($SRC looks wrong)"

if   command -v apt-get >/dev/null; then PM=apt
elif command -v dnf     >/dev/null; then PM=dnf
else die "need apt-get or dnf"; fi

# A fresh Ubuntu box runs unattended-upgrades on boot and holds the dpkg lock
# for a few minutes. Wait it out rather than failing the install half way.
apt_wait() {
  [[ $PM == apt ]] || return 0
  local waited=0
  while fuser /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock \
              /var/lib/apt/lists/lock /var/cache/apt/archives/lock >/dev/null 2>&1; do
    (( waited == 0 )) && log "Waiting for another apt/dpkg process (unattended-upgrades?) to release the lock…"
    waited=1; sleep 5
  done
}

pm_install() {
  case $PM in
    apt) apt_wait; DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "$@";;
    dnf) dnf install -y "$@";;
  esac
}

log "Installing base prerequisites"
if [[ $PM == apt ]]; then
  apt_wait; apt-get update -qq
  pm_install python3 python3-venv python3-dev build-essential git curl ca-certificates rsync openssl gnupg
else
  pm_install python3 python3-devel gcc gcc-c++ make git curl ca-certificates rsync openssl
fi
PYTHON="$(command -v python3.13 || command -v python3.12 || command -v python3.11 || command -v python3)"
"$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
  || die "Python >= 3.11 required, found $($PYTHON -V)"
log "Using $($PYTHON -V) at $PYTHON"

# uv builds the venv and installs the project — the same tool the sillo release
# workflows use. Installed system-wide so every account can reach it.
if command -v uv >/dev/null; then
  log "uv already installed ($(uv --version))"
else
  log "Installing uv into /usr/local/bin"
  curl -LsSf https://astral.sh/uv/install.sh \
    | env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh
fi
UV="$(command -v uv || echo /usr/local/bin/uv)"
[[ -x "$UV" ]] || die "uv install failed"

# --------------------------------------------------------------------------
# Caddy — the data plane Janus manages (unless --no-caddy)
# --------------------------------------------------------------------------
if [[ $DO_CADDY == 1 ]]; then
  if command -v caddy >/dev/null; then
    log "Caddy already installed"
  else
    log "Installing Caddy"
    if [[ $PM == apt ]]; then
      pm_install debian-keyring debian-archive-keyring apt-transport-https
      curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
      curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
      apt_wait; apt-get update -qq && pm_install caddy
    else
      dnf install -y 'dnf-command(copr)' && dnf copr enable -y @caddy/caddy && pm_install caddy
    fi
  fi
  systemctl enable --now caddy
  # Janus drives Caddy's admin API. It listens on 127.0.0.1:2019 by default,
  # which is what deploy/janus.env.example points CADDY_ADMIN_URL at.
  sleep 1
  curl -fsS http://127.0.0.1:2019/config/ >/dev/null 2>&1 \
    && log "Caddy admin API reachable on 127.0.0.1:2019" \
    || warn "Caddy admin API not answering on :2019 yet — check 'journalctl -u caddy'."
else
  warn "Skipping Caddy. Set CADDY_ADMIN_URL in the env file to your Caddy's admin API."
fi

# --------------------------------------------------------------------------
# Redis
# --------------------------------------------------------------------------
if [[ $DO_REDIS == 1 ]]; then
  if command -v redis-server >/dev/null; then log "Redis already installed"
  else log "Installing Redis"; [[ $PM == apt ]] && pm_install redis-server || pm_install redis; fi
  systemctl enable --now "$([[ $PM == apt ]] && echo redis-server || echo redis)"
fi

# --------------------------------------------------------------------------
# PostgreSQL
# --------------------------------------------------------------------------
if [[ $DO_PG == 1 ]]; then
  if command -v psql >/dev/null && systemctl list-unit-files | grep -q '^postgresql'; then
    log "PostgreSQL already installed"
  else
    log "Installing PostgreSQL"
    if [[ $PM == apt ]]; then pm_install postgresql
    else pm_install postgresql-server postgresql-contrib
         [[ -d /var/lib/pgsql/data/base ]] || postgresql-setup --initdb; fi
  fi
  systemctl enable --now postgresql
  log "Ensuring the janus role and database exist"
  # Set the password every run (CREATE if missing, else ALTER) so the URL this
  # script writes always matches the role, even on a re-run.
  DB_PASS="$(openssl rand -hex 16)"
  sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
DO \$\$ BEGIN
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'janus') THEN
    ALTER ROLE janus LOGIN PASSWORD '${DB_PASS}';
  ELSE
    CREATE ROLE janus LOGIN PASSWORD '${DB_PASS}';
  END IF;
END \$\$;
SQL
  sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='janus'" | grep -q 1 \
    || sudo -u postgres createdb -O janus janus
  NEW_DATABASE_URL="postgres://janus:${DB_PASS}@127.0.0.1:5432/janus"
fi

# --------------------------------------------------------------------------
# service user + directories
# --------------------------------------------------------------------------
if ! id "$APP_USER" >/dev/null 2>&1; then
  log "Creating system user $APP_USER"
  useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
fi
mkdir -p "$APP_DIR" "/etc/$APP" "$APP_DIR/storage" "$APP_DIR/storage/exports"

# --------------------------------------------------------------------------
# sync code
# --------------------------------------------------------------------------
log "Syncing $SRC -> $APP_DIR"
rsync -a --delete \
  --exclude '.git' --exclude '.venv' --exclude '.cache' --exclude 'node_modules' \
  --exclude 'storage/*.db*' --exclude 'storage/*.log' --exclude 'storage/*.gz' \
  --exclude '__pycache__' --exclude 'coverage.json' --exclude '*.log' \
  "$SRC"/ "$APP_DIR"/
install -d -o "$APP_USER" -g "$APP_USER" "$APP_DIR/.cache"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

# --------------------------------------------------------------------------
# venv (uv)
# --------------------------------------------------------------------------
log "Building the virtualenv with uv and installing janus[$PY_EXTRAS]"
run_uv() { sudo -u "$APP_USER" env HOME="$APP_DIR" UV_CACHE_DIR="$APP_DIR/.cache/uv" "$UV" "$@"; }
run_uv venv --python "$PYTHON" "$APP_DIR/.venv"
run_uv pip install --python "$APP_DIR/.venv/bin/python" --prerelease=allow -e "$APP_DIR[$PY_EXTRAS]"

# --------------------------------------------------------------------------
# front end
# --------------------------------------------------------------------------
if [[ $DO_BUILD == 1 ]] && command -v npm >/dev/null; then
  log "Building the front end"
  ( cd "$APP_DIR" && sudo -u "$APP_USER" npm ci --silent && sudo -u "$APP_USER" npm run build --silent )
elif [[ ! -f "$APP_DIR/static/build/.vite/manifest.json" ]]; then
  warn "No front-end build present and Node is unavailable — build static/build/ elsewhere and copy it in."
fi

# --------------------------------------------------------------------------
# env file
# --------------------------------------------------------------------------
ENV_FILE="/etc/$APP/$APP.env"
if [[ -f "$ENV_FILE" ]]; then
  log "Keeping existing $ENV_FILE"
  # ...but if --postgres just rotated the local DB password, keep the URL in sync.
  [[ -n "${NEW_DATABASE_URL:-}" ]] && sed -i "s|^DATABASE_URL=.*|DATABASE_URL=${NEW_DATABASE_URL}|" "$ENV_FILE"
else
  log "Writing $ENV_FILE (first run)"
  install -m 0640 -o root -g "$APP_USER" "$APP_DIR/deploy/$APP.env.example" "$ENV_FILE"
  sed -i "s|^SECRET_KEY=.*|SECRET_KEY=$(openssl rand -hex 32)|" "$ENV_FILE"
  [[ -n "${NEW_DATABASE_URL:-}" ]] && sed -i "s|^DATABASE_URL=.*|DATABASE_URL=${NEW_DATABASE_URL}|" "$ENV_FILE"
  [[ $DO_REDIS == 0 ]] && sed -i "s|^QUEUE_BACKEND=redis|QUEUE_BACKEND=memory|" "$ENV_FILE"
  if [[ -n "$DOMAIN" ]]; then
    sed -i "s|^APP_URL=.*|APP_URL=https://$DOMAIN|;s|^CORS_ORIGINS=.*|CORS_ORIGINS=https://$DOMAIN|" "$ENV_FILE"
  fi
fi
chmod 0640 "$ENV_FILE"; chown root:"$APP_USER" "$ENV_FILE"

# --------------------------------------------------------------------------
# systemd
# --------------------------------------------------------------------------
log "Installing systemd units"
for u in "$APP_DIR"/deploy/systemd/*; do
  install -m 0644 "$u" "/etc/systemd/system/$(basename "$u")"
done
if [[ "$APP_DIR" != /opt/janus || "$APP_USER" != janus ]]; then
  sed -i "s|/opt/janus|$APP_DIR|g; s|^User=janus|User=$APP_USER|; s|^Group=janus|Group=$APP_USER|; s|janus:janus|$APP_USER:$APP_USER|" \
    /etc/systemd/system/janus-*.service
fi
systemctl daemon-reload

log "Running database migrations"
systemctl restart janus-migrate.service

log "Starting the web tier and the scheduler"
systemctl enable --now janus-web.service janus-scheduler.service
systemctl restart janus-web.service janus-scheduler.service

if grep -q '^QUEUE_BACKEND=redis' "$ENV_FILE"; then
  log "Starting the job worker (QUEUE_BACKEND=redis)"
  systemctl enable --now janus-worker.service
  systemctl restart janus-worker.service
else
  warn "QUEUE_BACKEND is not redis — janus-worker left disabled. The scheduler runs jobs in-process."
  systemctl disable --now janus-worker.service 2>/dev/null || true
fi

cat <<DONE

$(printf '\033[1;32m✔  Janus is installed.\033[0m')

  status : systemctl status janus-web janus-scheduler
  logs   : journalctl -u janus-web -f
  config : $ENV_FILE
  health : curl -s http://127.0.0.1:$(grep -oP '^JANUS_PORT=\K.*' "$ENV_FILE")/health

Next:

  1. Create the first operator:
       sudo -u $APP_USER $APP_DIR/.venv/bin/janus admin create you@example.com   # or: janus user create ...

  2. Point Janus at your Caddy in $ENV_FILE (CADDY_ADMIN_URL) if it is not the
     local instance on :2019, then in the dashboard create a gateway and deploy.

  3. Put the dashboard behind TLS (a second Caddy site, or a separate proxy).

To upgrade later: git pull in this checkout, then re-run this script.
DONE
