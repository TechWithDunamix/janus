"""Getting started: what Janus is, how it is put together, and first run."""

from __future__ import annotations

from typing import Any

from app.docs.blocks import code, columns, heading, note, para, steps, table, terms

PAGES: tuple[dict[str, Any], ...] = (
    {
        "slug": "introduction",
        "section": "start",
        "title": "What Janus is",
        "summary": "A control plane for Caddy — and, deliberately, not a proxy.",
        "blocks": [
            para(
                "Janus manages [Caddy](https://caddyserver.com). It owns the desired "
                "configuration of your gateways — routes, upstreams, domains, rate limits, "
                "access policies — validates that configuration, deploys it, verifies it "
                "landed, and then reads back what actually happened at the edge."
            ),
            para(
                "Caddy does the proxying. **No Janus process sits in the request path.** That "
                "is the single most important thing to understand about the architecture, and "
                "almost everything else in this documentation follows from it."
            ),

            heading("The consequence of staying out of the request path"),
            para(
                "A great many gateway products are the gateway. You point traffic at them, they "
                "consult a database or a policy engine, and they forward the request. That "
                "design has one enormous appeal — every decision can be made freshly, per "
                "request, against live state — and one enormous cost: the control plane becomes "
                "a hard dependency of every request your users make."
            ),
            para(
                "Janus takes the other side of that trade. Policy is compiled into "
                "configuration ahead of time and handed to Caddy, which enforces it without "
                "consulting anything. The costs are real and worth stating plainly: a change "
                "takes effect on deployment rather than instantly, and any rule that cannot be "
                "expressed as Caddy configuration cannot be expressed at all."
            ),
            para("What you get in exchange is a list worth having:"),
            columns([
                ("The gateway outlives the control plane",
                 "Stop Janus, and traffic keeps flowing on the last configuration deployed. A "
                 "control plane outage becomes a management outage rather than an outage."),
                ("Latency is Caddy's, not Janus's",
                 "There is no database lookup, no policy evaluation and no network hop added to "
                 "a request. The p99 you measure is the proxy's and your backend's."),
                ("Blast radius is bounded by a deployment",
                 "A bad rule affects traffic when you deploy it, not the moment somebody saves "
                 "a form. Between those two points there is a diff to read and a validation to fail."),
                ("The gateway can be restarted without Janus",
                 "Caddy holds its own configuration. Recovering the gateway does not mean "
                 "recovering the control plane first."),
            ]),

            heading("What Janus actually does"),
            para(
                "Four responsibilities, and it is useful to keep them distinct because they "
                "fail differently and are owned by different permissions."
            ),
            terms([
                ("Configuration",
                 "Holds the desired state of every gateway and turns it into Caddy JSON. "
                 "Generates, validates, deploys, verifies, versions and rolls back. This is the "
                 "part with the safety machinery, because it is the part that can break an edge."),
                ("Identity and authorisation",
                 "Who may do what, enforced identically on the dashboard, the JSON API and the "
                 "CLI — because all three reach the same permission check."),
                ("Observability",
                 "Reads Caddy's access log and metrics, folds them into per-minute rollups, and "
                 "answers questions about traffic, errors, latency and clients. Purely a reader; "
                 "it writes nothing to the gateway."),
                ("Security policy",
                 "Allowlists, blocklists, rate limits, API keys and conditional rules — expressed "
                 "in Janus, compiled into configuration, enforced by Caddy."),
            ]),

            heading("What Janus is not"),
            steps([
                "**Not a proxy.** It never forwards a request. Adding it to the request path "
                "would make every request depend on the control plane being up, which is the "
                "property this design exists to avoid.",
                "**Not a service mesh.** It manages a north-south edge — traffic entering your "
                "estate — not east-west traffic between services. There is no sidecar and no "
                "per-service identity.",
                "**Not a Caddy replacement.** Caddy's configuration remains the source of truth "
                "for *behaviour*. Janus is the source of truth for *intent*, and reconciles the "
                "two. Where they disagree, Janus reports drift rather than quietly winning.",
                "**Not a CDN or a cache.** Caching is available as a Caddy plugin — see "
                "[Caddy modules](/docs/modules) — but Janus stores nothing and serves nothing.",
                "**Not an API management platform.** There is no developer portal, no monetisation "
                "and no schema registry. It manages a gateway, not a product around one.",
            ]),

            heading("Janus is a tenant of your Caddy, not its owner"),
            para(
                "This deserves stating early because it shapes how you can adopt Janus. It "
                "writes exactly two paths inside Caddy's configuration, both named after the "
                "`CADDY_SERVER_NAME` setting:"
            ),
            code("""
apps/http/servers/<name>     the routes, matchers and handlers
logging/logs/<name>          the access log Janus ingests
""", lang="text"),
            para(
                "Nothing else is ever written. There is no code path in this project that "
                "replaces the configuration root, and the test suite asserts it. A Caddy "
                "instance can carry other servers, other apps and other loggers configured by "
                "hand or by something else entirely, and Janus will read them for drift "
                "detection while leaving them exactly alone."
            ),
            para(
                "In practice this means you can put Janus in front of an existing Caddy without "
                "handing it everything. Give it a server name, let it manage the routes you want "
                "managed, and keep the rest where it is. The Health screen lists every server on "
                "the instance that Janus does not own, so the split is visible rather than "
                "something you have to remember."
            ),
            note(
                "The corollary: if you delete a route in Janus and deploy, it disappears from "
                "the Janus-owned server — but a route with the same path in a *different* server "
                "on the same Caddy keeps serving. Two servers on one instance is a legitimate "
                "setup and a confusing one; the Health screen exists partly to make it visible.",
                tone="caution",
                title="What co-tenancy means when you delete something",
            ),

            heading("Where the numbers come from"),
            para(
                "Everything on the analytics screens is derived from two things Caddy produces: "
                "its JSON access log and its Prometheus metrics endpoint. Janus does not sample, "
                "does not estimate, and does not run an agent inside your services."
            ),
            para(
                "That has a pleasant consequence and an inconvenient one. The pleasant one is "
                "that the request count on the dashboard is the request count Caddy served — "
                "there is no sampling rate to reason about. The inconvenient one is that Janus "
                "only knows what Caddy logs: if a field is not in the access log, no screen can "
                "show it, and adding one means changing the log format rather than changing "
                "Janus."
            ),
            para(
                "It also means analytics have a floor on freshness. The collector reads the log "
                "on an interval — fifteen seconds by default — so the dashboard is seconds "
                "behind rather than live. For an operations console that is the right trade: a "
                "true streaming pipeline would need a broker, and a broker is a component that "
                "can fail in a way that loses your traffic data."
            ),

            heading("Who Janus is for"),
            para(
                "It assumes a team that already runs Caddy, or is willing to, and that wants "
                "the routing table to be reviewed rather than edited in place. The features that "
                "get the most attention here — versioned configuration, diffs, rollback, an "
                "audit trail with before and after values, a CLI that cannot bypass the "
                "permission model — are all features about *change control* rather than about "
                "proxying. If your gateway configuration is a file that one person edits and "
                "reloads, Janus is a large amount of machinery for a problem you do not have."
            ),
            para(
                "Where it earns its place is the case where several people change the same edge, "
                "where a bad routing change is expensive, and where somebody eventually asks "
                "what changed at 03:12 and why."
            ),

            heading("Where to go next"),
            steps([
                "[Architecture](/docs/architecture) — the layers, the boundaries, and why each "
                "one is where it is.",
                "[Getting started](/docs/getting-started) — from nothing to a gateway serving "
                "traffic.",
                "[Configuration and deployments](/docs/configuration) — the safety machinery, in "
                "detail.",
            ]),
        ],
    },
    {
        "slug": "architecture",
        "section": "start",
        "title": "Architecture",
        "summary": "The layers, the boundaries between them, and why each is where it is.",
        "blocks": [
            code("""
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
""", lang="text"),

            heading("The direction of travel"),
            para(
                "Configuration flows one way: the Janus database, through the configuration "
                "engine, into Caddy. Nothing flows back. What comes the other way is only ever "
                "*compared* — drift detection reads Caddy's live configuration and reports a "
                "difference, but never merges it in."
            ),
            para(
                "That asymmetry is deliberate and it is worth understanding why, because the "
                "alternative is superficially attractive. A control plane that adopted "
                "hand-edits would be more forgiving: edit Caddy directly in an emergency, and "
                "Janus catches up. The problem is what it does to history. If the live "
                "configuration can flow back into the desired state, then a version in the "
                "history is no longer a record of what someone decided — it is a record of what "
                "the gateway happened to be running when the reconciler last looked. Rolling "
                "back to version 14 would restore something nobody ever chose."
            ),
            para(
                "So Janus reports drift and stops. Putting it right is an explicit act: either "
                "redeploy the desired state over the hand-edit, or change the desired state to "
                "match what somebody did and deploy that. Both leave a version with an author."
            ),

            heading("The layers"),
            terms([
                ("The frontend",
                 "React over Inertia. It talks to Janus and never to Caddy. That is structural "
                 "rather than a convention: no Caddy address is ever serialised into a page "
                 "object, and the only HTTP client for the admin API lives server-side in "
                 "`app/caddy/transport.py`. A browser with a route to the admin API is a browser "
                 "that can reconfigure your edge."),
                ("The Sillo application",
                 "Routing, middleware, sessions, authentication, permissions, the ORM, "
                 "validation, the console, the queue and the scheduler are all the framework's. "
                 "Janus contains no router, no ORM, no auth framework and no CLI framework of "
                 "its own — see any of those and it is a bug."),
                ("The route layer",
                 "`routes/web/` renders Inertia pages; `routes/api/` answers JSON. Both are thin. "
                 "A handler authorises, parses, calls a service and renders; the decision about "
                 "what actually happens is one layer down."),
                ("The services",
                 "`app/services/` is where operations live: the configuration pipeline, analytics "
                 "queries, security rules, API keys, alerts, health. The dashboard and the API "
                 "both call these, which is what makes an operation mean the same thing however "
                 "it was reached."),
                ("The Caddy layer",
                 "`app/caddy/` is the only code that knows Caddy exists. `config_builder.py` is a "
                 "pure function from desired state to JSON — no I/O, no database. `manager.py` is "
                 "the only object that opens a connection. `transport.py` is the HTTP client and "
                 "the in-process simulator."),
                ("The collector",
                 "`app/collector/` reads Caddy's access log and metrics endpoint. It is the only "
                 "writer of analytics rows, which is why redaction can be enforced in one place."),
                ("The models",
                 "`database/models/` in five groups: identity, gateway, security, analytics and "
                 "configuration. The split is by lifecycle rather than by screen — analytics rows "
                 "are written by a job and pruned on a schedule, gateway rows are written by "
                 "people and versioned."),
            ]),

            heading("Why the Caddy layer is isolated so strictly"),
            para(
                "Every Caddy-specific fact lives under one package: that the admin API uses POST "
                "for an idempotent write, that durations are Go duration strings, that "
                "`weighted_round_robin` takes a positional weights array, that a bad load is "
                "rejected atomically. None of that leaks upward."
            ),
            para(
                "The immediate benefit is testability. `config_builder.py` opens no connections, "
                "so the entire generation layer can be exercised against any estate shape without "
                "a Caddy anywhere — which is why the test suite can assert things like *routes "
                "are emitted in priority order* cheaply and exhaustively."
            ),
            para(
                "The longer-term benefit is that the boundary is where a second data plane would "
                "go. Nothing above `app/caddy/` knows what a `reverse_proxy` handler is; services "
                "deal in routes, upstreams and policies. That does not make supporting another "
                "proxy free — the generator is genuinely Caddy-shaped — but it does mean the "
                "surface to reimplement is one package rather than the whole application."
            ),

            heading("The two processes that are not the web server"),
            para(
                "Janus runs three kinds of process and only one of them serves HTTP."
            ),
            table(
                ["Process", "Does", "How many"],
                [
                    ["`janus serve`", "The dashboard and the JSON API", "As many as you like"],
                    ["`janus scheduler`", "Ingestion, health refresh, drift detection, alert evaluation, pruning", "Exactly one"],
                    ["`janus worker`", "Drains the job queues", "As many as you like"],
                ],
            ),
            para(
                "The scheduler is singular on purpose. Its jobs are not idempotent in the way "
                "that matters for concurrency: two schedulers ingesting the same access log would "
                "each advance their own byte offset and double-count everything between them. "
                "Running one is a constraint the deployment has to honour, and it is why the "
                "scheduler is a separate process rather than a thread inside the web server — a "
                "thread would be multiplied by however many web replicas you run."
            ),

            heading("What happens on a request, end to end"),
            steps([
                "A request arrives at **Caddy**, which matches it against the Janus-owned server's "
                "route list and applies whatever handlers that route carries.",
                "Caddy proxies it to an upstream, or answers it itself for a redirect, a static "
                "response, a block or a maintenance page.",
                "Caddy writes one JSON line to the access log. Janus is not involved in any of "
                "the above.",
                "Within fifteen seconds the **collector** reads new lines, parses them, drops "
                "every header outside a small allowlist, attributes each request to a route and a "
                "client, and writes a request row plus a per-minute rollup.",
                "The **dashboard** queries rollups for anything with a time range on it, and "
                "request rows only for questions that genuinely need individual records.",
            ], ordered=True),
            note(
                "Step 3 is the one to notice. The request has already been served and answered "
                "before Janus knows it existed. Nothing on the analytics screens is in the "
                "critical path, which is why an ingestion backlog shows up as stale charts rather "
                "than as slow requests.",
                tone="info",
            ),

            heading("Failure modes, by layer"),
            table(
                ["What fails", "What happens"],
                [
                    ["Janus web process", "Dashboard and API are down. Traffic is unaffected; Caddy keeps serving the last deployed configuration."],
                    ["Janus scheduler", "Analytics go stale and drift is not detected. Traffic unaffected. The log keeps being written, so ingestion catches up when it returns."],
                    ["Janus database", "Dashboard errors and deployments are impossible. Traffic unaffected."],
                    ["Caddy admin API", "Deployments fail at the apply stage and the gateway is marked `FAILED`. Traffic unaffected — the running configuration is untouched."],
                    ["Caddy itself", "Traffic is down. This is the only failure in the table that your users see."],
                    ["An upstream", "Caddy's health checks take it out of rotation; the route continues on remaining targets, or answers 503 if none are left."],
                ],
            ),
            para(
                "The shape of that table is the argument for the architecture. Five of the six "
                "rows are invisible to the people using your API."
            ),

            heading("Deliberate omissions"),
            para(
                "Some things are absent on purpose, and it is more useful to say so than to leave "
                "you wondering."
            ),
            steps([
                "**No message broker.** The collector reads a file with a stored byte offset. "
                "That works with Caddy on another host over a shared volume, needs no extra "
                "component, and survives Janus being down because the log keeps being written.",
                "**No time-series database.** Per-minute rollups in the same relational database "
                "answer every chart Janus draws. A second datastore would be a second thing to "
                "operate for a workload that is a few thousand rows a day.",
                "**No agents.** Nothing is installed alongside your services.",
                "**No push from Caddy.** Caddy has no mechanism to push access logs, so polling "
                "is not a design choice so much as the available option — but it is a good fit, "
                "because a poller that falls behind recovers by reading faster.",
            ]),
        ],
    },
    {
        "slug": "getting-started",
        "section": "start",
        "title": "Getting started",
        "summary": "From nothing to a gateway serving traffic, and what each step is for.",
        "blocks": [
            para(
                "This page goes from an empty directory to real traffic flowing through a "
                "Caddy that Janus configured. It is worth doing in order the first time: each "
                "step explains a piece of the model you will meet again later."
            ),

            heading("Requirements"),
            table(
                ["What", "Why"],
                [
                    ["Python 3.11+", "Janus itself."],
                    ["Node 22+", "Building the frontend. Not needed at runtime once assets are built."],
                    ["Caddy", "The data plane. Also used for `caddy validate`, which is how Janus proves a configuration before deploying it."],
                    ["Postgres", "Production only. SQLite is the default and is fine for a laptop."],
                ],
            ),
            note(
                "Caddy on the same host as Janus is not required, but it helps: `caddy validate` "
                "and `caddy list-modules` run the local binary. Without one, Janus falls back to "
                "structural validation and assumes a core-only build — and says so both times "
                "rather than pretending otherwise.",
                tone="info",
            ),

            heading("Install"),
            code("""
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,server]"
npm install
"""),

            heading("Create the schema"),
            code("./janus migrate"),
            para(
                "This creates missing tables *and* reconciles missing columns, then seeds the "
                "permission catalogue and the five default roles. The column reconciliation "
                "matters after an upgrade: creating a schema only creates missing tables, so a "
                "column added to an existing model would otherwise be silently absent until a "
                "query failed at runtime with `no such column`."
            ),

            heading("Create the first account"),
            code("./janus admin create"),
            note(
                "This command works exactly once. It refuses the moment any account exists, and "
                "points at `janus users create` — which goes through the API and therefore "
                "through the permission model.\n\n"
                "Without that refusal, the CLI would be a permanent way around authorisation for "
                "anyone with shell access. It is true that shell access already implies database "
                "access, so this is not a security boundary in the strict sense; it is the "
                "difference between *someone determined enough to write SQL* and *a command in "
                "the help text*.",
                tone="caution",
                title="Why this command works exactly once",
            ),

            heading("Start Caddy"),
            para(
                "Janus needs a Caddy with its admin API reachable. Start it with an empty "
                "configuration — Janus writes the rest:"
            ),
            code("caddy run --config docker/caddy-bootstrap.json"),
            para("That bootstrap file is three lines of substance:"),
            code("""
{
  "admin": { "listen": "0.0.0.0:2019" },
  "apps": { "http": { "servers": {} } }
}
""", lang="json"),
            para(
                "It configures no routes on purpose. Anything defined here would be a second "
                "source of truth, and Janus would report it as drift on the next poll. It also "
                "carries no comments, because Caddy's JSON loader rejects unknown fields at the "
                "config root — including a `\"//\"` key, which fails with "
                "`loading initial config: json: unknown field \"//\"`."
            ),
            note(
                "The admin API is a full-control endpoint: anyone who can reach it can rewrite "
                "your edge. Bind it to loopback or a private interface, never to a public one. "
                "In the Docker Compose setup it is exposed only on the compose network.",
                tone="critical",
                title="The admin API is not a read-only endpoint",
            ),

            heading("Start Janus"),
            code("""
npm run dev                # Vite dev server, on :5173
./janus serve              # Janus, on :8000
"""),
            para(
                "Two processes in development because the frontend is served by Vite with hot "
                "reload. In production you run `npm run build` once and Janus serves the compiled "
                "assets itself — see [Deploying Janus](/docs/deployment)."
            ),
            para(
                "Janus talks to `http://localhost:2019` by default. Point it elsewhere with "
                "`CADDY_ADMIN_URL`, or per gateway with the admin URL on the gateway itself."
            ),

            heading("Build an estate"),
            para(
                "Sign in, and work through the Gateway section in this order. Each step depends "
                "on the one before it."
            ),
            steps([
                "**Register a gateway.** Give it a name and the address Caddy's Janus-owned "
                "server should listen on — `:8080` to start with.",
                "**Add a domain.** Set its TLS mode to `off` for local work. This matters more "
                "than it looks: see the note below.",
                "**Create an upstream.** A named pool with at least one `host:port` target. "
                "Weights are relative, and 5:3:2 means what it looks like.",
                "**Create a route.** Path, methods, the upstream to send it to, and a priority. "
                "Higher priority wins, and the first match answers.",
                "**Deploy.** Configuration → Review & deploy. Read the diff, then apply.",
            ], ordered=True),
            note(
                "Caddy turns on automatic HTTPS as soon as a server has routes with `host` "
                "matchers — and then answers plain HTTP on the listen port with *\"Client sent an "
                "HTTP request to an HTTPS server\"*. A gateway on `:8080` with one hostname route "
                "silently stops speaking HTTP, and nothing in the configuration mentioned TLS.\n\n"
                "Janus therefore decides this explicitly from the domain table. With every domain "
                "set to `off`, automatic HTTPS is disabled. This is the single most common "
                "first-run confusion, which is why it is called out here rather than only in "
                "[Domains and TLS](/docs/domains).",
                tone="caution",
                title="Set TLS to off for local work",
            ),

            heading("Or start with demo data"),
            code("./janus seed"),
            para(
                "Creates a gateway, seven upstreams, nine routes, security rules, forty-eight "
                "hours of traffic, and one account per role so the permission model can be tried "
                "rather than read about."
            ),
            table(
                ["Account", "Password", "Role"],
                [
                    ["owner@janus.local", "janus-development-owner", "Owner"],
                    ["admin@janus.local", "janus-development-administrator", "Administrator"],
                    ["operator@janus.local", "janus-development-operator", "Operator"],
                    ["analyst@janus.local", "janus-development-analyst", "Analyst"],
                    ["viewer@janus.local", "janus-development-viewer", "Viewer"],
                ],
            ),
            note(
                "The seeded *traffic* is marked `simulated` on every row and labelled as such in "
                "the interface. The gateway, the routes and the rules are real — point them at "
                "your Caddy and deploy. An observability tool that showed invented traffic as "
                "observation would be worse than one that showed nothing, so the flag travels "
                "with the data all the way to the screen.\n\n"
                "The demo upstreams point at addresses that do not exist on your machine, so "
                "routes will answer 502 until you repoint them. That is the correct behaviour, "
                "and it is a good first look at how a failing upstream surfaces.",
                tone="caution",
            ),

            heading("Confirm it works"),
            code("""
# Through the gateway, not through Janus
curl -H 'Host: api.example.local' http://localhost:8080/api/products

# What Janus thinks the gateway is doing
./janus gateways status
./janus config diff
"""),
            para(
                "Then generate some traffic and watch it arrive. The collector runs every fifteen "
                "seconds under the scheduler; to pull immediately:"
            ),
            code("""
./janus collect
./janus analytics overview --range 15m
"""),

            heading("Running the background jobs"),
            para(
                "In development, `janus work` runs every scheduled job once — ingestion, health, "
                "drift, alerts, pruning — which is usually what you want while trying things out. "
                "In a real deployment you run the scheduler as its own process."
            ),
            code("""
./janus work              # everything once, on demand
./janus scheduler         # continuously, on a schedule
"""),

            heading("Common first-run problems"),
            terms([
                ("Every request returns 400 with an HTTPS message",
                 "Automatic HTTPS is on. Set your domains' TLS mode to `off`, or reach the "
                 "gateway over https."),
                ("Every request returns 403",
                 "You have a global allow rule, which makes the whole gateway deny-by-default. "
                 "See [Allowlists and blocklists](/docs/access-control)."),
                ("Routes return 502",
                 "The upstream targets are not reachable from the Caddy host. Check "
                 "`janus upstreams health`."),
                ("Analytics stay empty",
                 "Either Caddy is not writing where Janus reads — compare the Settings screen "
                 "with the deployed configuration — or the collector has not run. Try "
                 "`janus collect`."),
                ("`no such column` after an upgrade",
                 "Run `janus migrate`. Creating the schema only creates missing tables."),
            ]),

            heading("Where to go next"),
            steps([
                "[Routes](/docs/routes) — matching, handling and why priority is not decoration.",
                "[Configuration and deployments](/docs/configuration) — the safety machinery.",
                "[Roles and permissions](/docs/rbac) — before you invite anyone.",
            ]),
        ],
    },
)
