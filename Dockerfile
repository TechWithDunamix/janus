# Janus — the control plane. Not the data plane: Caddy is its own image, and
# nothing in here proxies traffic.
# This image includes Caddy for local configuration validation.

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
COPY pyproject.toml README.md requirements.txt ./
COPY src ./src
RUN pip install -r requirements.txt



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
