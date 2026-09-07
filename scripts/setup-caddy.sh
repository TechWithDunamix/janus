#!/usr/bin/env bash
#
# setup-caddy.sh — rebuild the local Caddy binary with the plugins Janus needs,
# swap it in, restart Caddy, and redeploy the gateway configuration.
#
# Caddy is a single static binary and plugins are compiled in, so "adding" a
# plugin means producing a new binary. This does that the least-effort way the
# host allows:
#
#   1. xcaddy, if it is already on PATH
#   2. else `go install` xcaddy first, if a Go toolchain is on PATH
#   3. else download a custom build from caddyserver.com — no toolchain needed
#
# "Removing" a plugin is the same operation with a shorter list — pass the
# plugins you want to keep, or --plain for none. It always backs the current
# binary up first (caddy.backup-<timestamp> beside it) and can restore that
# with --rollback.
#
# Usage
#   scripts/setup-caddy.sh --with github.com/mholt/caddy-ratelimit
#   scripts/setup-caddy.sh --with <pkg> --with <pkg> --restart --redeploy
#   scripts/setup-caddy.sh --plain --restart --redeploy      # a Caddy with no plugins
#   scripts/setup-caddy.sh --rollback --restart
#
# Flags
#   --with <pkg>     Go package for a plugin (repeatable). Pass every plugin you
#                    want the binary to end up with: the build replaces the
#                    binary, it does not add to or subtract from it.
#   --plain          Build/download a Caddy with no plugins. Mutually exclusive
#                    with --with. This is how you uninstall the last plugin.
#   --version <v>    Caddy version to build (e.g. v2.11.4). Default: the current
#                    binary's version, else "latest".
#   --caddy <path>   Target binary path. Default: `command -v caddy`, else
#                    /usr/local/bin/caddy.
#   --restart        Restart Caddy after installing (brew services, systemd, or a
#                    bare `caddy run` process — auto-detected).
#   --redeploy       Run `janus config apply` afterwards so the gateway serves
#                    the regenerated config (a fresh Caddy starts empty).
#   --rollback       Restore the newest caddy.backup-* next to the target.
#   --yes            Do not prompt for confirmation.
#   --dry-run        Print what would happen and exit.
#   -h, --help       This text.
#
set -euo pipefail

# --------------------------------------------------------------------------
# Args
# --------------------------------------------------------------------------
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WITH=()
VERSION=""
TARGET=""
DO_RESTART=0
DO_REDEPLOY=0
DO_ROLLBACK=0
PLAIN=0
ASSUME_YES=0
DRY_RUN=0

die()  { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }
info() { printf '\033[36m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[32m ok\033[0m %s\n' "$*"; }
run()  { if [ "$DRY_RUN" = 1 ]; then printf '   would run: %s\n' "$*"; else eval "$@"; fi; }

while [ $# -gt 0 ]; do
  case "$1" in
    --with)     WITH+=("${2:?--with needs a package}"); shift 2 ;;
    --with=*)   WITH+=("${1#*=}"); shift ;;
    --version)  VERSION="${2:?}"; shift 2 ;;
    --version=*) VERSION="${1#*=}"; shift ;;
    --caddy)    TARGET="${2:?}"; shift 2 ;;
    --caddy=*)  TARGET="${1#*=}"; shift ;;
    --restart)  DO_RESTART=1; shift ;;
    --redeploy) DO_REDEPLOY=1; shift ;;
    --rollback) DO_ROLLBACK=1; shift ;;
    --plain)    PLAIN=1; shift ;;
    --yes|-y)   ASSUME_YES=1; shift ;;
    --dry-run)  DRY_RUN=1; shift ;;
    -h|--help)  sed -n '2,42p' "$0" | sed 's/^#\{1,\} \{0,1\}//'; exit 0 ;;
    *)          die "unknown flag: $1 (see --help)" ;;
  esac
done

# --------------------------------------------------------------------------
# Resolve the target binary and a sudo prefix if the path is not writable
# --------------------------------------------------------------------------
if [ -z "$TARGET" ]; then
  TARGET="$(command -v caddy || true)"
  [ -n "$TARGET" ] || TARGET="/usr/local/bin/caddy"
fi
TARGET_DIR="$(dirname "$TARGET")"
[ -d "$TARGET_DIR" ] || die "target directory does not exist: $TARGET_DIR"

SUDO=""
if [ -e "$TARGET" ] && [ ! -w "$TARGET" ]; then SUDO="sudo"; fi
if [ ! -e "$TARGET" ] && [ ! -w "$TARGET_DIR" ]; then SUDO="sudo"; fi
[ -z "$SUDO" ] || info "writing $TARGET needs elevation; will use sudo"

confirm() {
  [ "$ASSUME_YES" = 1 ] && return 0
  [ "$DRY_RUN" = 1 ] && return 0
  printf '%s [y/N] ' "$1"
  read -r reply < /dev/tty || reply=""
  case "$reply" in [yY]|[yY][eE][sS]) return 0 ;; *) die "aborted" ;; esac
}

# --------------------------------------------------------------------------
# Restart the running Caddy — brew services, systemd, or a bare `caddy run`
# process, whichever is in use. Defined early so --rollback can call it too.
# --------------------------------------------------------------------------
restart_caddy() {
  info "restarting Caddy"
  if command -v brew >/dev/null 2>&1 && brew services list 2>/dev/null | grep -Eq '^caddy[[:space:]]+started'; then
    run "brew services restart caddy"
  elif command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet caddy 2>/dev/null; then
    run "sudo systemctl restart caddy"
  else
    local pid cwd cmd
    pid="$(pgrep -f 'caddy run' | head -n1 || true)"
    if [ -z "$pid" ]; then
      info "no running 'caddy run' process found — start Caddy yourself"
      return 0
    fi
    if [ -r "/proc/$pid/cwd" ]; then
      cwd="$(readlink "/proc/$pid/cwd")"
      cmd="$(tr '\0' ' ' < "/proc/$pid/cmdline")"
    else
      cwd="$(lsof -a -d cwd -p "$pid" -Fn 2>/dev/null | sed -n 's/^n//p' | head -n1)"
      cmd="$(ps -o command= -p "$pid")"
    fi
    [ -n "$cwd" ] || cwd="$REPO_ROOT"
    info "  pid $pid, cwd $cwd"
    info "  cmd: $cmd"
    run "kill '$pid'"
    sleep 1
    run "cd '$cwd' && nohup $cmd > '$cwd/caddy.restart.log' 2>&1 &"
    sleep 1
  fi

  local admin="${CADDY_ADMIN_URL:-http://127.0.0.1:2019}"
  for _ in $(seq 1 20); do
    if curl -sf -o /dev/null "$admin/config/"; then ok "admin API up at $admin"; return 0; fi
    sleep 0.5
  done
  info "admin API at $admin did not answer within 10s — check the restart log"
}

# --------------------------------------------------------------------------
# Rollback
# --------------------------------------------------------------------------
if [ "$DO_ROLLBACK" = 1 ]; then
  backup="$(ls -1t "$TARGET".backup-* 2>/dev/null | head -n1 || true)"
  [ -n "$backup" ] || die "no backup found next to $TARGET"
  info "restoring $backup -> $TARGET"
  confirm "Restore the previous Caddy binary?"
  run "$SUDO cp -p '$backup' '$TARGET'"
  ok "restored"
  [ "$DO_RESTART" = 1 ] && restart_caddy
  exit 0
fi

if [ "${#WITH[@]}" -eq 0 ] && [ "$PLAIN" != 1 ]; then
  die "nothing to build: pass --with <package>, or --plain for a no-plugin Caddy, or --rollback"
fi
if [ "${#WITH[@]}" -gt 0 ] && [ "$PLAIN" = 1 ]; then
  die "--plain builds a Caddy with no plugins; do not also pass --with"
fi

# --------------------------------------------------------------------------
# Default version from the current binary
# --------------------------------------------------------------------------
if [ -z "$VERSION" ] && [ -x "$TARGET" ]; then
  VERSION="$("$TARGET" version 2>/dev/null | awk '{print $1}' || true)"
fi
[ -n "$VERSION" ] || VERSION="latest"

# --------------------------------------------------------------------------
# Build (or download) into a temp file
# --------------------------------------------------------------------------
TMP="$(mktemp "${TMPDIR:-/tmp}/caddy.XXXXXX")"
trap 'rm -f "$TMP"' EXIT

build_with_xcaddy() {
  local xc="$1"; shift
  local args=(build)
  [ "$VERSION" != "latest" ] && args+=("$VERSION")
  if [ "${#WITH[@]}" -gt 0 ]; then
    for pkg in "${WITH[@]}"; do args+=(--with "$pkg"); done
  fi
  args+=(--output "$TMP")
  info "building: $xc ${args[*]}"
  run "'$xc' ${args[*]}"
}

download_custom_build() {
  local os arch url
  case "$(uname -s)" in
    Darwin) os=darwin ;;
    Linux)  os=linux ;;
    *)      die "unsupported OS for the download API: $(uname -s). Install Go and re-run." ;;
  esac
  case "$(uname -m)" in
    x86_64|amd64)  arch=amd64 ;;
    arm64|aarch64) arch=arm64 ;;
    armv7l)        arch=armv7 ;;
    *)             die "unsupported arch for the download API: $(uname -m). Install Go and re-run." ;;
  esac
  url="https://caddyserver.com/api/download?os=${os}&arch=${arch}"
  if [ "${#WITH[@]}" -gt 0 ]; then
    for pkg in "${WITH[@]}"; do
      url="${url}&p=$(printf '%s' "$pkg" | sed 's:/:%2F:g')"
    done
  fi
  [ "$VERSION" != "latest" ] && url="${url}&version=${VERSION}"
  if [ "${#WITH[@]}" -eq 0 ]; then
    info "downloading a plain Caddy (no plugins):"
  else
    info "downloading a custom build (no Go toolchain found):"
  fi
  info "  $url"
  run "curl -fSL --retry 3 --connect-timeout 20 '$url' -o '$TMP'"
}

if command -v xcaddy >/dev/null 2>&1; then
  build_with_xcaddy "$(command -v xcaddy)"
elif command -v go >/dev/null 2>&1; then
  info "no xcaddy on PATH; installing it with go install"
  run "go install github.com/caddyserver/xcaddy/cmd/xcaddy@latest"
  XCADDY="$(command -v xcaddy || echo "$(go env GOPATH)/bin/xcaddy")"
  [ -x "$XCADDY" ] || die "xcaddy install did not produce a binary at $XCADDY"
  build_with_xcaddy "$XCADDY"
else
  download_custom_build
fi

if [ "$DRY_RUN" = 1 ]; then ok "dry run complete"; exit 0; fi

chmod +x "$TMP"

# --------------------------------------------------------------------------
# Sanity-check the new binary before it replaces anything
# --------------------------------------------------------------------------
"$TMP" version >/dev/null 2>&1 || die "the built binary does not run"
NEWMODS="$("$TMP" list-modules 2>/dev/null || true)"
missing=0
if [ "${#WITH[@]}" -gt 0 ]; then
  for pkg in "${WITH[@]}"; do
    case "$pkg" in
      *caddy-ratelimit*) grep -q '^http.handlers.rate_limit$'          <<<"$NEWMODS" || missing=1 ;;
      *caddy-jwt*)       grep -q 'authentication.providers.jwt'        <<<"$NEWMODS" || missing=1 ;;
      *coraza-caddy*)    grep -q '^http.handlers.waf'                  <<<"$NEWMODS" || missing=1 ;;
      *caddy-maxmind*|*geolocation*) grep -q 'maxmind_geolocation'    <<<"$NEWMODS" || missing=1 ;;
    esac
  done
fi
[ "$missing" = 0 ] || die "the built binary is missing an expected module; not installing it"
ok "built $("$TMP" version | awk '{print $1}') with $(grep -c . <<<"$NEWMODS") modules"

# --------------------------------------------------------------------------
# Back up and swap
# --------------------------------------------------------------------------
if [ -e "$TARGET" ]; then
  BACKUP="$TARGET.backup-$(date +%Y%m%d-%H%M%S)"
  info "backing up $TARGET -> $BACKUP"
  run "$SUDO cp -p '$TARGET' '$BACKUP'"
fi
confirm "Replace $TARGET with the new build?"
run "$SUDO install -m 0755 '$TMP' '$TARGET'"
ok "installed $TARGET"
command -v caddy >/dev/null 2>&1 && caddy list-modules 2>/dev/null | grep -q . && ok "$(caddy version | awk '{print $1}') active on PATH"

# --------------------------------------------------------------------------
# Restart
# --------------------------------------------------------------------------
[ "$DO_RESTART" = 1 ] && restart_caddy

# --------------------------------------------------------------------------
# Redeploy the gateway configuration
# --------------------------------------------------------------------------
if [ "$DO_REDEPLOY" = 1 ]; then
  info "redeploying gateway configuration"
  if [ -x "$REPO_ROOT/janus" ] && "$REPO_ROOT/janus" config apply --yes 2>/dev/null; then
    ok "redeployed via janus config apply"
  else
    info "  janus CLI unavailable or not logged in; deploying directly"
    PYTHONPATH="$REPO_ROOT" python - <<'PY'
import asyncio
from database.config import database
from database.models import Gateway
from app.services.configuration import deploy

async def main():
    async with database(generate_schemas=False):
        for gw in await Gateway.all():
            r = await deploy(gw, origin="cli", note="setup-caddy.sh rebuild", force=True)
            tag = "ok" if r.ok else "FAILED"
            print(f"  [{tag}] {gw.name}: stage={r.stage}"
                  + (f" v{r.version.version}" if getattr(r, 'version', None) else "")
                  + (f" — {r.error}" if r.error else ""))
            for w in r.warnings:
                print(f"        warn: {w}")

asyncio.run(main())
PY
  fi
fi

ok "done"
echo
echo "Verify:  caddy list-modules | grep -E 'rate_limit|jwt|waf'"
echo "         curl -s \${CADDY_ADMIN_URL:-http://127.0.0.1:2019}/config/ | grep -c rate_limit"
echo "Roll back:  $0 --rollback --restart"
