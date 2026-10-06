# Every task this project needs, discoverable with `make` on its own.
#
# Assumes an ACTIVE virtual environment (python -m venv .venv && source
# .venv/bin/activate): every target runs against that environment's python,
# pip and console scripts — never the system interpreter. The commands are
# plain pip/uvicorn/janus invocations rather than anything bespoke, so you can
# always read what a target does and run it by hand when you need to vary it.

.DEFAULT_GOAL := help
.PHONY: help check-venv env install build setup deploy migrate seed admin \
        config-show config-apply scheduler worker cli dev serve caddy-setup \
        caddy-run test typecheck lint format check clean

# uv-managed venvs (what deploy/install.sh and `uv venv` create) have no pip
# inside them — prefer uv when it is on PATH, fall back to python -m pip.
PY  := python
PIP := $(shell command -v uv >/dev/null 2>&1 && echo "uv pip install --python python" || echo "python -m pip install")
JANUS   := ./janus
APP     := app.main:app
HOST    ?= 127.0.0.1
PORT    ?= 8000
WORKERS ?= 1
# The plugin Janus most often needs — stock Caddy has no rate limiter.
PLUGIN  ?= github.com/mholt/caddy-ratelimit
# bun when present (what deploy/install.sh uses), npm otherwise.
NODE    := $(shell command -v bun 2>/dev/null || command -v npm 2>/dev/null)

help:  ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

# -- guard ----------------------------------------------------------------

check-venv:  ## Fail unless a virtual environment is active
	@test -n "$$VIRTUAL_ENV" || { \
	  echo "  no active virtual environment."; \
	  echo "  run: python -m venv .venv && source .venv/bin/activate"; \
	  exit 1; }

# -- setup ----------------------------------------------------------------

# The docker-compose hostnames in .env.example (postgres, redis, caddy) do not
# resolve outside a container network — a venv deployment runs everything on
# this host, so they are pointed at 127.0.0.1 here. Edit .env if yours live
# elsewhere.
env: check-venv  ## Create .env: fresh SECRET_KEY, local service hosts
	@test -f .env || cp .env.example .env
	@$(PY) -c "import pathlib,re,secrets; p=pathlib.Path('.env'); t=p.read_text(); t=re.sub(r'^SECRET_KEY=$$|^SECRET_KEY=change-me.*$$', 'SECRET_KEY='+secrets.token_hex(32), t, flags=re.M); t=t.replace('@postgres:5432','@127.0.0.1:5432').replace('redis:6379','127.0.0.1:6379').replace('http://caddy:2019','http://127.0.0.1:2019'); t=re.sub(r'^CADDY_ACCESS_LOG=.*$$', 'CADDY_ACCESS_LOG=storage/access.log', t, flags=re.M); p.write_text(t)" \
	  && echo "  .env ready (SECRET_KEY filled in; docker hostnames pointed at 127.0.0.1 — edit .env if yours live elsewhere)"

install: check-venv  ## Install Python packages and front-end dependencies
	$(PIP) -e '.[server,postgres,redis,dev]'
	@test -n "$(NODE)" || { echo "  need bun or npm on PATH for the front end"; exit 1; }
	$(NODE) install

build:  ## Build the front end into static/build
	@test -n "$(NODE)" || { echo "  need bun or npm on PATH for the front end"; exit 1; }
	$(NODE) run build

setup: env install build migrate  ## Everything except running: .env, packages, front end, database

deploy: check-venv setup  ## Install everything, build, migrate — then deploy the app
	# The foreground command is inlined rather than `$(MAKE) serve` because make
	# executes $(MAKE) lines even under `-n` — a dry run would really restart
	# systemd on a production host.
	@if command -v systemctl >/dev/null 2>&1 \
	   && systemctl list-unit-files 2>/dev/null | grep -q '^janus-web\.service'; then \
	  echo "==> restarting the janus systemd units"; \
	  sudo systemctl restart janus.target; \
	  echo "==> deployed. health: curl -s http://127.0.0.1:$(PORT)/health"; \
	else \
	  echo "==> no janus systemd units installed — running in the foreground"; \
	  echo "==> analytics and health need the scheduler too: run 'make scheduler' (and 'make worker' when QUEUE_BACKEND=redis)"; \
	  uvicorn $(APP) --host $(HOST) --port $(PORT) --workers $(WORKERS); \
	fi

# -- database & operations -----------------------------------------------

migrate:  ## Schema + permission catalogue
	$(JANUS) migrate

seed:  ## Demo gateway, routes, traffic and one account per role
	$(JANUS) seed

admin:  ## Create the first account (refuses once one exists). make admin e=you@example.com
	@test -n "$(e)" || (echo "  usage: make admin e=you@example.com"; exit 1)
	$(JANUS) admin create $(e)

config-show:  ## Show the generated Caddy configuration
	$(JANUS) config show

config-apply:  ## Deploy the configuration to the gateway
	$(JANUS) config apply

scheduler:  ## Periodic jobs — ingestion, health, drift, alerts. Exactly one.
	$(JANUS) scheduler

worker:  ## Drain the job queues (needs QUEUE_BACKEND=redis)
	$(JANUS) worker

cli:  ## Any CLI command: make cli ARGS="routes list"
	$(JANUS) $(ARGS)

# -- caddy ----------------------------------------------------------------

# Rebuilds the Caddy binary with the plugin(s), backs the old one up, restarts
# and re-applies the gateway config. PLAIN=1 builds a stock Caddy (how you
# uninstall a plugin); pass extra flags via ARGS, e.g. ARGS="--yes".
caddy-setup: check-venv  ## Build Caddy with the plugins Janus needs, restart, redeploy
	./scripts/setup-caddy.sh $(if $(PLAIN),--plain,--with $(PLUGIN)) --restart --redeploy $(ARGS)

caddy-run:  ## Start Caddy with an empty bootstrap config (admin API on :2019)
	caddy run --config docker/caddy-bootstrap.json

# -- running -------------------------------------------------------------

dev:  ## Reloadable server; run `npm run dev` alongside it (Vite, VITE_DEV=true)
	$(JANUS) serve --reload --host 127.0.0.1 --port $(PORT)

serve:  ## Run in the foreground as production would (HOST/PORT/WORKERS overridable)
	uvicorn $(APP) --host $(HOST) --port $(PORT) --workers $(WORKERS)

# -- quality -------------------------------------------------------------

test:  ## Run the test suite
	pytest -q

typecheck:  ## Type-check the front end
	$(NODE) run typecheck

lint:  ## Check style and lint rules
	ruff check .

format:  ## Apply formatting and fixable lint rules
	ruff format .
	ruff check --fix .

check: lint typecheck test  ## Everything CI runs

clean:  ## Remove caches and build artefacts (static/build is kept — it is what production serves)
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache .ruff_cache dist build *.egg-info

