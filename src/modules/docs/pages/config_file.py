"""YAML resource setup guide in the Janus dashboard."""

from __future__ import annotations

from typing import Any

from modules.docs.blocks import code, heading, note, para, steps, table

PAGES: tuple[dict[str, Any], ...] = (
    {
        "slug": "yaml-configuration",
        "section": "operations",
        "title": "Configure Janus from YAML",
        "summary": "Seed gateways, upstreams, routes, rate limits and API keys, then deploy to Caddy.",
        "blocks": [
            para(
                "A YAML file seeds Janus's desired state. `janus config file apply` creates "
                "missing resources through the control API; `janus config apply` separately "
                "validates and deploys that state to Caddy."
            ),
            heading("The resource model"),
            steps(
                [
                    "A **gateway** is one managed Caddy instance. Its upstreams, routes, and rate "
                    "limits live under it.",
                    "An **upstream** is a pool of backend targets. A proxy route references its slug.",
                    "A **route** matches a path and methods, then proxies or redirects. Higher "
                    "priority runs first.",
                    "A **rate limit** sets a request ceiling. Caddy needs its rate-limit plugin to "
                    "enforce the stored rule.",
                    "An **API key** identifies a gateway consumer, separate from the "
                    "operator token used by `janus login`. It is not owned by one gateway.",
                ]
            ),
            code(
                """
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
""",
                lang="yaml",
            ),
            para(
                "Indent fields beneath their parent. `-` begins a list item, and square "
                "brackets are compact list syntax. The top level accepts integer `version: 1`, "
                "optional `gateways`, and optional global `api_keys`."
            ),
            heading("Gateway fields"),
            table(
                ["Field", "Meaning"],
                [
                    [
                        "`slug` / `name`",
                        "Required stable slug and unique display name; each at most 100 characters. Slugs use lowercase letters, digits, hyphens.",
                    ],
                    [
                        "`admin_url`",
                        "Caddy admin URL. Omit to use Janus's global `CADDY_ADMIN_URL`.",
                    ],
                    ["`listen`", "Janus-owned Caddy server address; defaults to `:8080`."],
                    ["`region`", "Optional operator-facing region label, at most 60 characters."],
                    [
                        "`upstreams` / `routes` / `rate_limits`",
                        "Optional lists of resources scoped to this gateway.",
                    ],
                ],
            ),
            heading("Upstreams and targets"),
            table(
                ["Field", "Meaning"],
                [
                    [
                        "`slug` / `name`",
                        "Required upstream identity and display name; at most 100 characters each.",
                    ],
                    ["`policy`", "Caddy selection policy; defaults to `weighted_round_robin`."],
                    ["`health_path`", "Active health probe path. Omit to disable active probes."],
                    [
                        "`targets`",
                        "Required nonempty list of unique `host:port` strings or `{dial, weight}` mappings. Weight is a positive integer, default `1`.",
                    ],
                ],
            ),
            heading("Routes"),
            table(
                ["Field", "Meaning"],
                [
                    [
                        "`slug` / `name`",
                        "Required route identity and display name; at most 120 characters each.",
                    ],
                    [
                        "`path` / `methods`",
                        "Path defaults to `/*`; methods is a comma-separated list such as `GET,POST`, or omit for any method.",
                    ],
                    [
                        "`priority`",
                        "Nonnegative integer, default `0`; higher routes are evaluated first.",
                    ],
                    [
                        "`action`",
                        "`proxy` by default (requires an `upstream` slug) or `redirect` (requires `redirect_to`).",
                    ],
                    [
                        "`auth_policy`",
                        "`public` by default, or `api_key`, `jwt`, `user`, `role`, or `scope`. Janus's generated Caddy route checks credential-header presence; the upstream must verify validity and scopes.",
                    ],
                    [
                        "`timeout_seconds` / `retries`",
                        "Positive timeout, default `30`; nonnegative retry count, default `0`.",
                    ],
                    ["`strip_prefix`", "Optional path prefix to remove before proxying."],
                ],
            ),
            heading("Rate limits"),
            table(
                ["Field", "Meaning"],
                [
                    ["`name` / `limit`", "Required name and positive request ceiling."],
                    [
                        "`key`",
                        "Counter identity: `ip` (default), `api_key`, `user`, `route`, `domain`, `client`, or `global`.",
                    ],
                    [
                        "`window_seconds` / `burst`",
                        "Positive window, default `60` seconds; nonnegative burst, default `0`.",
                    ],
                ],
            ),
            heading("API keys and one-time secrets"),
            table(
                ["Field", "Meaning"],
                [
                    [
                        "`name`",
                        "Required key label, at most 120 characters. Same-name active keys with the same scopes are skipped on reapply.",
                    ],
                    [
                        "`scopes`",
                        "Optional list of unique application-specific strings; empty by default. The generated Caddy route does not compare these scopes; the upstream must check them.",
                    ],
                    [
                        "`expires_in_days`",
                        "Optional nonnegative lifetime from issuance. Omit or use `0` for no expiry; reapply does not renew a key.",
                    ],
                ],
            ),
            note(
                "The YAML file contains key definitions, never secrets. On first creation "
                "Janus prints `NEW KEY name: jan_...`. Store it immediately in a secret manager; "
                "Janus stores only its hash and cannot retrieve it later.",
                tone="critical",
            ),
            heading("Plan, seed, deploy"),
            code("""
janus login --url https://janus.example.com
janus config file plan path/to/janus.yml
janus config file apply path/to/janus.yml
janus config validate --gateway edge
janus config apply --gateway edge --yes
"""),
            para(
                "Plan validates the entire file and previews `create` or `skip`; `--json` "
                "gives structured output. Apply creates missing gateways, upstreams, routes, "
                "rate limits and keys. Both use the operator's permissions and audit trail. "
                "Existing resources are skipped by identity, even if fields in YAML changed."
            ),
            para(
                "An inactive or differently scoped same-name key causes an error; use a new "
                "key name for replacement. A partially failed apply can be rerun. File apply "
                "does not remove resources or deploy to Caddy. Review the generated config, "
                "then run `janus config apply --gateway SLUG` for each gateway."
            ),
            para(
                "This format covers the fields supported by Janus's create API. Manage "
                "domains, header rewrites, canaries, maintenance, and other settings in the "
                "dashboard or API. See [Gateways](/docs/gateways), [CLI reference](/docs/cli), "
                "and [Rate limiting](/docs/rate-limiting)."
            ),
        ],
    },
)
