# Janus — the control plane. Not the data plane: Caddy is its own image, and
# nothing in here proxies traffic.
#
# Two stages, because the front end needs Node and the runtime does not. The
# runtime image carries Python, the compiled assets and the `caddy` binary —
# the last one so `config validate` can provision a candidate configuration in
# a throwaway process before it is sent anywhere. Without it Janus falls back
# to structural checks and says so, which is a weaker guarantee than a
# deployment should settle for.

# ---------------------------------------------------------------------------
FROM node:22-alpine AS assets
WORKDIR /build

COPY package.json package-lock.json* ./
RUN npm ci --no-audit --no-fund

COPY tsconfig.json vite.config.ts ./
COPY src/js ./src/js
COPY src/views ./src/views
RUN npm run build

# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    JANUS_ROOT=/app

# `caddy` is here for `caddy validate` and `caddy list-modules`, both of which
# Janus shells out to. It is not run as a server in this image.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates gnupg \
 && curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
      | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg \
 && echo "deb [signed-by=/usr/share/keyrings/caddy-stable-archive-keyring.gpg] https://dl.cloudsmith.io/public/caddy/stable/deb/debian any-version main" \
      > /etc/apt/sources.list.d/caddy-stable.list \
 && apt-get update && apt-get install -y --no-install-recommends caddy \
 && apt-get purge -y gnupg && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# requirements.txt pins every runtime dependency and ends with `-e .`, so the
# source (Python packages, src/app.html, src/public) must be in place first.
COPY pyproject.toml README.md requirements.txt ./
COPY src ./src
RUN pip install -r requirements.txt

COPY --from=assets /build/static/build ./static/build

# Assets are compiled into the image, so there is no dev server to reach.
ENV VITE_DEV=false

RUN useradd --create-home --uid 10001 janus \
 && mkdir -p /app/storage && chown -R janus:janus /app/storage
USER janus

EXPOSE 8000

# Touches the database rather than only confirming the process is alive: a
# container answering HTTP with a dead database should not receive traffic.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status==200 else 1)"

ENTRYPOINT ["janus"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000"]
