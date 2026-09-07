"""Gateway: instances, routes, upstreams, domains and the deployment pipeline."""

from __future__ import annotations

from typing import Any

from app.docs.blocks import code, heading, note, para, steps, table, terms

PAGES: tuple[dict[str, Any], ...] = (
    {
        "slug": "gateways",
        "section": "gateway",
        "title": "Gateways",
        "summary": "Registering a Caddy instance, what Janus touches, and what it leaves alone.",
        "blocks": [
            para(
                "A gateway is one Caddy instance that Janus manages. Janus supports more than "
                "one because a real estate has more than one — an edge tier and an internal "
                "tier, a pair either side of a region boundary, or a staging gateway that "
                "should never share a route table with production."
            ),
            para(
                "Each gateway carries its own routes, upstreams, domains, security rules, "
                "configuration history and access log. Nothing is shared between them. That is "
                "deliberate: a rule that applied to every gateway would eventually be deployed "
                "to one where it did not belong, and there is no way to review a change whose "
                "scope you cannot see."
            ),

            heading("Registering one"),
            code("""
janus gateways create \\
  --name "Edge" \\
  --listen :8080 \\
  --admin-url http://10.0.0.5:2019 \\
  --region eu-west
"""),
            terms([
                ("Name", "How it appears in the picker and in every audit entry."),
                ("Listen", "The address Caddy's Janus-owned server binds. `:443` in production, "
                 "`:8080` on a laptop. Changing it is a configuration change like any other and "
                 "goes through a deployment."),
                ("Admin URL", "Where Caddy's admin API is. Leave it empty to use the global "
                 "`CADDY_ADMIN_URL` — the single-gateway case, and the default."),
                ("Region", "Free text. It appears in the picker so that two gateways with "
                 "similar names can be told apart at a glance."),
            ]),

            heading("What Janus owns inside Caddy"),
            para("Two paths, both named after the `CADDY_SERVER_NAME` setting:"),
            code("""
apps/http/servers/<name>     the routes, matchers and handlers
logging/logs/<name>          the access log Janus ingests
""", lang="text"),
            para(
                "Nothing else is ever written. There is no method on the Caddy manager that "
                "replaces the configuration root, and `tests/test_caddy.py` asserts that a Caddy "
                "carrying other servers keeps them across a Janus deployment."
            ),
            para(
                "This is what makes incremental adoption possible. You can put Janus in front of "
                "an existing Caddy that already has hand-written servers, give it a server name "
                "of its own, and let it manage only the routes you move across. The two coexist "
                "in one process, on different listen addresses or the same one with different "
                "host matchers, and neither disturbs the other."
            ),
            note(
                "The Health screen lists every server on the instance that Janus does not own. "
                "Two servers on one Caddy is a legitimate setup and a confusing one — a route "
                "you deleted in Janus can keep serving from the other server — so the split is "
                "shown rather than left for you to remember.",
                tone="info",
            ),

            heading("Reading the sync state"),
            para(
                "Every gateway carries one of four states, shown in the top bar wherever you are "
                "in the application. It is chrome rather than a screen because it changes how "
                "you should read everything else: analytics from a `DRIFTED` gateway describe "
                "traffic served by a configuration you did not deploy."
            ),
            table(
                ["State", "Means", "What to do"],
                [
                    ["SYNCED", "The gateway is running the active version.", "Nothing."],
                    ["SYNCING", "A deployment is in progress.", "Wait. It resolves in seconds."],
                    ["DRIFTED", "Caddy differs from the active version — someone edited it, or reloaded it from a file.", "Look at the Configuration diff, then either redeploy over it or adopt the change deliberately."],
                    ["FAILED", "The last deployment did not complete.", "Read the Deployments screen. The stage says whether the gateway was ever touched."],
                ],
            ),
            para(
                "`FAILED` is worth reading carefully because it does not mean the gateway is "
                "down. A deployment that failed at `validating` never reached Caddy at all; one "
                "that failed at `applying` was rejected and Caddy kept what it had. Either way "
                "traffic is being served — by an older configuration than you intended."
            ),

            heading("Maintenance mode"),
            para(
                "A gateway in maintenance answers every route with its maintenance response "
                "instead of proxying. The routes stay defined, so turning it off restores "
                "service without redeploying the route table — which matters when the route "
                "table is the thing you are in the middle of changing."
            ),
            para(
                "You can set the status code and the body. The default is a 503 with a "
                "`Retry-After`, which is the honest answer: a 503 tells a well-behaved client "
                "and a search engine that this is temporary, where a 404 or a 200 with an "
                "apology page does not."
            ),
            note(
                "Maintenance mode is the one change in Janus that deploys immediately rather "
                "than waiting for you to press Deploy. An operator turning it on is responding "
                "to something now, and a second click at that moment is a second chance to get "
                "it wrong. Everything else is desired state until you deploy it.",
                tone="caution",
            ),
            para(
                "There is also per-route and per-upstream maintenance, which is usually what you "
                "want. Draining one upstream takes a service out without touching the rest of "
                "the estate; gateway-wide maintenance is the blunt instrument for when the "
                "answer is *everything, now*."
            ),

            heading("Access logs are per gateway"),
            para(
                "Each gateway writes to its own log file, defaulting to "
                "`storage/access-<slug>.log`. You can override the path per gateway, which is "
                "what you need when Caddy runs in a container and writes to a mounted volume."
            ),
            note(
                "Pointing two gateways at one file ingests every request twice, once under each. "
                "The collector keeps a byte offset per gateway, so both read the whole file and "
                "both count everything in it — and the result is a gateway reporting traffic it "
                "never served, with nothing failing to say so. This was a real bug during "
                "development, which is why the default path includes the gateway's slug.",
                tone="critical",
                title="Never share a log file between gateways",
            ),

            heading("Multiple gateways in practice"),
            para(
                "The gateway picker in the top bar scopes almost every screen: routes, "
                "upstreams, domains, configuration, analytics and security rules all belong to "
                "the selected gateway. Team, roles and the audit log do not — those are "
                "installation-wide."
            ),
            para(
                "The selection lives in your session, not in the URL. That is a considered "
                "choice with a cost: you cannot paste a link that opens a specific gateway's "
                "route table. In exchange, no authorisation decision can ever be made from a "
                "value in a query string, which is the failure mode the alternative invites. "
                "Janus's permission model is installation-wide — everyone who can read routes "
                "can read every gateway's routes — so the selector is a convenience rather than "
                "a boundary, and it is important that it never becomes mistaken for one."
            ),

            heading("Deleting a gateway"),
            para(
                "Deleting a gateway in Janus removes its routes, upstreams, domains, rules, "
                "history and analytics. It does **not** remove the server from Caddy: the "
                "gateway keeps serving whatever it was last given, because a control plane that "
                "took down an edge as a side effect of a delete button would be a dangerous "
                "thing to own."
            ),
            para(
                "To take the traffic down as well, put the gateway into maintenance first, or "
                "delete the Janus-owned server from Caddy directly once you are sure."
            ),
        ],
    },
    {
        "slug": "routes",
        "section": "gateway",
        "title": "Routes",
        "summary": "Matching, handling, transforming — and why priority is the routing behaviour.",
        "blocks": [
            para(
                "A route says what to match and what to do with it. Janus emits routes in "
                "priority order and Caddy answers with the first one that matches, so the "
                "ordering **is** the routing behaviour rather than a way of arranging a list."
            ),
            para(
                "This page is the reference. For a walkthrough that puts several services behind "
                "one gateway from an empty route table, see "
                "[Routing several services](/docs/routing-services)."
            ),
            note(
                "A catch-all at priority 0 and a specific path at priority 100 is the difference "
                "between a working API and every request landing on the catch-all. The Routes "
                "screen is sorted by priority for exactly this reason — you are reading the match "
                "sequence, not an inventory.",
                tone="caution",
                title="Priority decides which route answers",
            ),

            heading("Matching"),
            table(
                ["Field", "Behaviour"],
                [
                    ["Path", "A trailing `*` is a prefix match: `/api/orders*` matches `/api/orders` and `/api/orders/42`. Anything else is exact. Note that `/api/orders/*` — with the slash — does **not** match the bare `/api/orders`."],
                    ["Methods", "Comma-separated. Empty matches every method, which is the right default for a catch-all and the wrong one for a route that only reads."],
                    ["Domain", "Empty matches every host the gateway serves. Setting one restricts the route to that hostname."],
                    ["Priority", "Higher wins. Ties break by creation order, which is stable but not something to rely on — give routes distinct priorities."],
                ],
            ),
            para(
                "Janus supports two path forms rather than Caddy's full matcher vocabulary. That "
                "is a deliberate narrowing: prefix and exact cover what an API gateway routes on, "
                "and every additional form is one more thing that behaves differently in Caddy "
                "than a reader expects from the screen. If you need a regular expression matcher "
                "or a header matcher on a route, that is what a [security policy](/docs/policies) "
                "is for."
            ),

            heading("A worked ordering example"),
            para("Four routes on one gateway:"),
            table(
                ["Priority", "Route", "Why there"],
                [
                    ["200", "`GET /healthz`", "Above everything, so a health probe never queues behind an auth check."],
                    ["120", "`POST /hooks/*`", "Above the API, because webhooks are public and the API is not."],
                    ["100", "`/api/*`", "The bulk of the traffic."],
                    ["0", "`/*`", "The catch-all. Answers 404 rather than letting Caddy decide."],
                ],
            ),
            para(
                "Move `/api/*` above `/hooks/*` and webhooks start requiring an API key. Move the "
                "catch-all to 150 and the entire gateway returns 404. Neither change looks "
                "dramatic on the screen, which is why the diff before a deployment is worth "
                "reading."
            ),

            heading("Handling"),
            terms([
                ("Proxy", "Forward to an upstream pool. The usual case, and the only one that "
                 "needs an upstream."),
                ("Redirect", "Answer with a 301, 302, 307 or 308 and a Location header. Useful "
                 "for retiring a path without keeping a backend alive to serve the redirect."),
                ("Static", "Answer with a fixed status and body. A catch-all that returns a JSON "
                 "404 rather than Caddy's default is a small kindness to whoever is debugging."),
                ("Maintenance", "Answer with the maintenance response. Route-scoped, so one "
                 "service can be taken out without touching the rest."),
            ]),
            para(
                "The choice of 301 versus 302 is worth a moment. A 301 is cached by browsers "
                "aggressively and is difficult to take back — if you redirect `/v1` to `/v2` "
                "permanently and then need `/v1` again, clients that saw the 301 may not ask. A "
                "302 costs a round trip and keeps the option open."
            ),

            heading("Transformation"),
            para(
                "`Strip prefix` removes the matched prefix before proxying. A route on "
                "`/api/orders*` with a `/api` strip sends `/orders/42` upstream, which is what a "
                "service that does not know it is behind a gateway expects."
            ),
            para(
                "`Rewrite to` replaces the path wholesale and takes precedence over stripping. "
                "It is the sharper tool: useful for pinning a legacy path to a new one, and easy "
                "to point at something that does not exist."
            ),
            para(
                "Request and response headers can be set or removed. Setting `X-Forwarded-*` is "
                "handled by Caddy already; the useful cases here are adding a header your "
                "backend uses to identify the gateway, and removing a `Server` header that names "
                "your framework and version to anyone who asks."
            ),

            heading("Reliability"),
            table(
                ["Setting", "What it does"],
                [
                    ["Timeout", "How long the whole request may take. Caddy gives up and answers 504 after this."],
                    ["Read timeout", "How long to wait for the upstream's response body. 0 leaves it to Caddy's default."],
                    ["Retries", "How many times to try another target when one fails."],
                ],
            ),
            note(
                "Retries without a bound multiply your error rate. A retry count on its own means "
                "a failing backend is asked three times instead of once, and every one of those "
                "attempts consumes a connection while the caller waits.\n\n"
                "Janus therefore always emits a `try_duration` alongside a retry count, tied to "
                "the route's timeout — so retries stop when the request's budget is spent rather "
                "than when the count runs out.",
                tone="caution",
                title="Why retries are bounded by the timeout",
            ),
            para(
                "Retries are also only safe for idempotent requests. Caddy will retry a POST as "
                "readily as a GET, and a payment endpoint that is retried after a timeout may "
                "well have succeeded the first time. Set retries on reads; think carefully before "
                "setting them on writes."
            ),

            heading("Authentication policy"),
            para(
                "A route can require a credential. What Janus emits is a **presence check** at "
                "the edge: is there an `Authorization` header, or an `X-Api-Key`? The "
                "credential's *validity* is the upstream's business."
            ),
            para(
                "That will look weak, so it is worth being explicit about the reasoning. "
                "Verifying a token at the edge means the gateway needs the signing key or a JWKS "
                "endpoint, and it means every request depends on that verification succeeding. "
                "The presence check costs nothing, stops unauthenticated traffic reaching your "
                "backends at all, and leaves the decision that actually matters with the service "
                "that owns the data."
            ),
            para(
                "If you want real verification at the edge, that is what the JWT plugin is for — "
                "see [Caddy modules](/docs/modules). Janus will emit a real verification handler "
                "once the build provides one, and says so on the Modules screen when it does not."
            ),

            heading("Canary traffic"),
            para(
                "A route can send a share of its traffic to a second upstream. Set a canary "
                "upstream and a percentage, and Janus expresses the split as extra targets with "
                "proportional weights rather than as a separate handler — Caddy's load balancer "
                "is already a weighted chooser, and a second mechanism would be a second thing to "
                "reason about."
            ),
            para(
                "The arithmetic is explained in [Upstreams and load balancing](/docs/upstreams), "
                "and it has a subtlety worth knowing before you rely on a 10% split meaning 10%."
            ),

            heading("From the CLI"),
            code("""
janus routes list
janus routes show 4

janus routes create --name Orders --path '/api/orders*' \\
    --methods GET,POST --upstream orders-service \\
    --priority 100 --auth api_key --strip-prefix /api

janus routes update 4 --priority 120
janus routes disable 4
janus routes delete 4

janus config diff
janus config apply
"""),
            note(
                "Everything above changes *desired state*. Nothing reaches the gateway until you "
                "deploy — which is why `config diff` belongs in the same breath as the change "
                "that preceded it.",
                tone="info",
            ),
        ],
    },
    {
        "slug": "upstreams",
        "section": "gateway",
        "title": "Upstreams and load balancing",
        "summary": "Backend pools, weights, health checks, canary splits and draining.",
        "blocks": [
            para(
                "An upstream is a named pool of backend servers. A route points at it by name, "
                "so moving a backend in or out of rotation touches one row and no routes — which "
                "is the entire reason the two are separate."
            ),

            heading("Targets and weights"),
            para(
                "Each target is a `host:port` Caddy dials, plus a weight and an enabled flag. "
                "Weights are relative: 5:3:2 sends half the traffic to the first target, a "
                "little under a third to the second, and a fifth to the third."
            ),
            note(
                "Caddy's `weighted_round_robin` gives each target `weight` **consecutive** "
                "requests, not a per-request probability. A pool weighted 5:3:2 sends five "
                "requests to the first target, then three to the second, then two to the third, "
                "and repeats.\n\n"
                "For steady traffic that is indistinguishable from a probabilistic split. For "
                "bursty traffic it is not: ten requests arriving together may all land on one "
                "target.",
                tone="info",
                title="A weight is a cycle length",
            ),
            para(
                "This has a consequence that bit during development and is worth knowing. "
                "Scaling a 5:3:2 pool for a 10% canary produces 450:270:180:100 — the same "
                "ratio, arithmetically correct, and useless: it sends the first 450 requests to "
                "one backend before the second sees any. On a gateway doing ten requests a "
                "second, that is forty-five seconds of a \"90/10 split\" being a 100/0 split."
            ),
            para(
                "Janus therefore reduces every weight vector by its greatest common divisor "
                "before emitting it. 450:270:180:100 becomes 45:27:18:10 — the identical split, "
                "reached ten times sooner."
            ),

            heading("Load balancing policies"),
            table(
                ["Policy", "Use it when"],
                [
                    ["`weighted_round_robin`", "Targets differ in capacity. The default, and the only one that reads the weights."],
                    ["`round_robin`", "Targets are identical. Simpler, and weights are ignored."],
                    ["`least_conn`", "Request durations vary a lot. Sends work to whichever target is least busy."],
                    ["`ip_hash`", "A caller should stick to one backend. Sticky by client address, which breaks behind a NAT."],
                    ["`random`", "You want no coordination at all. Fine for large pools."],
                    ["`first`", "An explicit primary and standby. Always the first healthy target in order."],
                ],
            ),
            para(
                "`least_conn` is the one people reach for and should think about. It is genuinely "
                "better when some requests take a hundred times longer than others, and it is "
                "worse when your backends have wildly different capacities, because it "
                "distributes by in-flight count rather than by capability."
            ),

            heading("Health checks"),
            para(
                "Health checking is declared in Janus and executed by Caddy. Janus reads the "
                "results back for the dashboard but never runs its own probes."
            ),
            para(
                "That is a considered boundary rather than laziness. A control plane running its "
                "own health checks would produce a second opinion about backend health — from a "
                "different network position, on a different interval — and the two would "
                "eventually disagree. That disagreement surfaces during an incident, which is "
                "precisely when nobody has the patience to work out which probe to believe."
            ),
            table(
                ["Kind", "Behaviour"],
                [
                    ["Passive", "Counts failures within a window. Above `max_fails`, Caddy takes the target out for `fail_duration`. Costs nothing — it observes real traffic."],
                    ["Active", "Polls a path on an interval and expects a status. Detects a dead backend before a user does, at the cost of constant traffic."],
                ],
            ),
            para(
                "Use both. Passive alone means the first few requests after a backend dies are "
                "the health check, and your users run it. Active alone misses a backend that "
                "answers `/healthz` cheerfully while failing every real request."
            ),
            note(
                "An active health check path should exercise something. A handler that returns "
                "200 unconditionally tells you the process is running, which you already knew "
                "from the fact that it accepted the connection.",
                tone="info",
            ),

            heading("Timeouts and connection limits"),
            terms([
                ("Dial timeout", "How long to wait for a TCP connection. Short — a backend that "
                 "cannot be connected to in a couple of seconds is down, not slow."),
                ("Max connections per host", "A ceiling on concurrent connections to each target. "
                 "0 is unlimited, which is Caddy's default and the right answer for a backend "
                 "that manages its own concurrency. Set it when a backend has a small fixed "
                 "worker pool and you would rather queue at the gateway than at the socket."),
            ]),

            heading("Canary deployments"),
            para(
                "Create a second upstream pointing at the new version, set it as a route's canary "
                "with a percentage, and deploy. Traffic splits immediately and the analytics "
                "screens attribute it to the route rather than the upstream — so watch the "
                "route's error rate and P95, and compare the two upstreams on the Health screen."
            ),
            para("A staged rollout is a sequence of deployments:"),
            steps([
                "Deploy at 5%. Watch for one traffic cycle — long enough to include whatever your "
                "quiet period is.",
                "Move to 25%. This is where capacity problems show up that 5% hid.",
                "Move to 50%, then 100%.",
                "Repoint the route's primary upstream at the new pool and remove the canary.",
            ], ordered=True),
            note(
                "Each step is a configuration version with an author and a timestamp, so the "
                "rollout leaves a record. If step three goes wrong, `janus config rollback` puts "
                "back the exact configuration from step two rather than an inverse of it.",
                tone="info",
            ),

            heading("Draining"),
            para(
                "Marking an upstream as draining takes the whole pool out without deleting it. "
                "Routes pointing at it answer 503 with a `Retry-After` rather than failing to "
                "connect, which is a better answer for a client and a clearer signal in your own "
                "analytics."
            ),
            para(
                "Disabling an individual target does the same at a finer grain, and is what you "
                "want when replacing one machine in a pool. Neither deletes anything, so putting "
                "it back is a checkbox rather than retyping an address."
            ),

            heading("What happens when a pool is empty"),
            para(
                "A route pointing at an upstream with no enabled targets answers 503 rather than "
                "failing to generate. Janus emits a static 503 handler and reports a warning at "
                "generation time, which appears on the Configuration screen and in "
                "`janus config validate`."
            ),
            para(
                "The alternative — refusing to deploy — was considered and rejected. An estate "
                "with nine working routes and one pointing at an empty pool should be able to "
                "deploy the nine."
            ),

            heading("From the CLI"),
            code("""
janus upstreams list
janus upstreams show orders-service

janus upstreams create --name orders-service \\
    --targets 10.0.1.11:8000:3,10.0.1.12:8000:2 \\
    --health-path /healthz

janus upstreams health      # refreshed from Caddy's own checkers
janus upstreams delete 4
"""),
        ],
    },
    {
        "slug": "domains",
        "section": "gateway",
        "title": "Domains and TLS",
        "summary": "Hostnames, certificate modes, and the automatic-HTTPS trap.",
        "blocks": [
            para(
                "A domain is a hostname the gateway answers on, and how TLS is provisioned for "
                "it. Routes can be scoped to a domain, which is how one gateway serves several "
                "hostnames with different route tables."
            ),

            heading("TLS modes"),
            table(
                ["Mode", "What Caddy does", "Use it when"],
                [
                    ["`auto`", "Provisions a certificate from a public CA over HTTP-01 or TLS-ALPN-01.", "The name resolves publicly to this gateway."],
                    ["`internal`", "Issues from Caddy's own local CA.", "A private network, or local development where you want real TLS."],
                    ["`custom`", "Uses a certificate supplied out of band.", "You have a certificate from somewhere else."],
                    ["`off`", "Plain HTTP.", "A laptop, or a gateway behind a TLS-terminating load balancer."],
                ],
            ),
            para(
                "`internal` is underused. Caddy will issue a certificate from its own CA and, on "
                "a machine where that CA has been trusted, browsers accept it — which gives you "
                "HTTPS locally without a self-signed certificate warning and without pointing a "
                "public name at your laptop."
            ),

            heading("The automatic-HTTPS trap"),
            note(
                "Caddy turns on automatic HTTPS as soon as a server has routes with `host` "
                "matchers — and then answers plain HTTP on the listen port with *\"Client sent an "
                "HTTP request to an HTTPS server\"*, status 400.\n\n"
                "A gateway on `:8080` with one hostname route silently stops speaking HTTP, and "
                "nothing in the configuration you wrote mentioned TLS. The first symptom is every "
                "request failing with a 400 that names a protocol you were not thinking about.",
                tone="critical",
                title="The single most confusing thing about Caddy for newcomers",
            ),
            para(
                "Janus therefore decides automatic HTTPS **explicitly**, from the domain table, "
                "rather than letting Caddy infer it from the presence of host matchers. The rule "
                "is simple:"
            ),
            steps([
                "No domains, or every domain set to `off` → automatic HTTPS is disabled and the "
                "gateway speaks plain HTTP.",
                "Any domain wanting `auto`, `internal` or `custom` → automatic HTTPS is enabled, "
                "and the `off` hostnames are listed as skipped so they keep working over HTTP.",
            ]),
            para(
                "The upshot is that TLS is something you turn on rather than something that "
                "happens to you. It also means the Domains screen is load-bearing: it is where "
                "the protocol your gateway speaks is decided."
            ),

            heading("Certificate provisioning needs to reach the internet"),
            para(
                "`auto` mode means ACME, and ACME means a challenge. HTTP-01 needs port 80 "
                "reachable from the internet on the name being issued; TLS-ALPN-01 needs port "
                "443. If neither is true — an internal gateway, a wildcard, a name that resolves "
                "only inside your network — the challenge cannot complete and Caddy will retry "
                "until it gives up."
            ),
            para(
                "The answer for those cases is DNS-01, which proves control of the name by "
                "writing a DNS record instead of serving a file. That requires a DNS provider "
                "plugin — see [Caddy modules](/docs/modules), which lists Cloudflare and Route53 "
                "— and an API token for the zone."
            ),
            note(
                "Certificate provisioning happens in Caddy, on its own schedule, and Janus does "
                "not drive it. The TLS status shown on the Domains screen is what Janus last "
                "recorded, not a live probe. When a certificate is not issuing, Caddy's own log "
                "is where the reason is.",
                tone="caution",
            ),

            heading("Domains and route scoping"),
            para(
                "A route with no domain matches every host the gateway serves. A route with a "
                "domain matches only that hostname. Mixing the two on one gateway is normal and "
                "the priority ordering still decides: a domain-scoped route at priority 100 and "
                "an unscoped route at 90 means the scoped one wins for its hostname and the "
                "unscoped one catches everything else."
            ),
            para(
                "A common shape is one gateway serving an API hostname and a CDN hostname, with "
                "routes scoped to each and a single unscoped catch-all at priority 0 that returns "
                "404 for anything else — including requests that arrive with no `Host` header or "
                "one you have never heard of."
            ),

            heading("Serving a hostname you have not defined"),
            para(
                "Caddy will answer for any hostname that reaches it, whether or not you have "
                "added it as a domain in Janus. The domain list controls TLS and route scoping; "
                "it is not an allowlist of hostnames."
            ),
            para(
                "If you want to refuse unknown hostnames, that is a catch-all route with a "
                "static response — or a security policy matching on host. Both are explicit, "
                "which is the point."
            ),

            heading("Wildcards"),
            para(
                "A wildcard domain — `*.example.com` — matches any single label. It needs a "
                "DNS-01 challenge, because a wildcard cannot be proven by serving a file on one "
                "hostname, which means a DNS provider plugin and an API token for the zone."
            ),
            para(
                "Wildcards are convenient and worth thinking about before adopting. One "
                "certificate covering every subdomain means one certificate whose compromise "
                "covers every subdomain, and it means a routing table where a typo in a hostname "
                "silently matches rather than failing."
            ),

            heading("Certificate lifecycle"),
            para(
                "Caddy renews automatically, well before expiry, and keeps its certificates and "
                "ACME account keys in its data directory. That directory must persist across "
                "restarts — a container without a volume mounted there will request fresh "
                "certificates on every start, and public CAs have rate limits that will notice."
            ),
            table(
                ["What", "Where", "Must persist"],
                [
                    ["Certificates and keys", "Caddy's data directory", "Yes"],
                    ["ACME account key", "Caddy's data directory", "Yes — losing it means re-registering"],
                    ["OCSP staples", "Caddy's data directory", "No, but re-fetching costs a round trip"],
                ],
            ),
            note(
                "The Docker Compose setup mounts `caddy-data` for exactly this reason. It is the "
                "single most common cause of hitting a CA rate limit: a container that looks "
                "stateless, restarts a few times during a deploy, and requests a new certificate "
                "each time.",
                tone="caution",
            ),

            heading("Testing without burning rate limits"),
            para(
                "Let's Encrypt has a staging environment with far higher limits and certificates "
                "no browser trusts. It is the right place to prove your DNS, your firewall and "
                "your challenge type work, before switching to production issuance."
            ),
            para(
                "For local work, `internal` mode avoids the question entirely: Caddy issues from "
                "its own CA, instantly, with no network round trip and no rate limit."
            ),

            heading("HTTP to HTTPS redirection"),
            para(
                "Caddy's automatic HTTPS includes an automatic redirect from port 80 to 443 for "
                "the hostnames it manages. That is usually what you want and occasionally not — "
                "an ACME HTTP-01 challenge from another system, or a health probe that speaks "
                "plain HTTP, will be redirected too."
            ),
            para(
                "Where that is a problem, the answer is a route: a static response or a proxy on "
                "the specific path, at a high priority, so it is matched before anything else."
            ),

            heading("Domains do not restrict which hostnames are served"),
            para(
                "Caddy answers for any hostname that reaches it, whether or not it is in the "
                "domain list. The list controls TLS provisioning and route scoping; it is not an "
                "allowlist of hostnames."
            ),
            para(
                "This matters because a request with a forged or unexpected `Host` header will be "
                "matched by any route that does not name a domain. If your gateway should refuse "
                "unknown hostnames, that is a catch-all route with a static response, or a policy "
                "matching on host — both explicit, which is the point."
            ),

            heading("Traffic by domain"),
            para(
                "The Domains screen shows request volume and bandwidth per hostname over the last "
                "day, which is the fastest way to notice that a hostname you thought was retired "
                "is still receiving traffic — and therefore still needs its certificate."
            ),
        ],
    },
    {
        "slug": "configuration",
        "section": "gateway",
        "title": "Configuration and deployments",
        "summary": "Generate, validate, apply, verify — and how rollback actually works.",
        "blocks": [
            para(
                "Everything Janus sends to a gateway goes through one pipeline, every time, with "
                "no fast path around it."
            ),
            code("generate → validate → apply → verify → mark active", lang="text"),

            heading("Every stage can stop safely"),
            terms([
                ("generate",
                 "Builds the Caddy server object from your desired state. Pure: no connection is "
                 "opened, nothing is written. A failure here is a bug in Janus, and the gateway "
                 "has not been touched."),
                ("validate",
                 "Runs the candidate through `caddy validate`, which provisions the entire "
                 "configuration in a throwaway process — the same code path a real load takes. A "
                 "failure here means the gateway was never touched."),
                ("apply",
                 "Writes the two Janus-owned subtrees through the admin API. Caddy loads "
                 "configuration atomically, so a payload it rejects leaves the previous one "
                 "serving."),
                ("verify",
                 "Reads back what is live and compares the listen address and the route list. "
                 "Catches the case where Caddy accepted the payload but is not running what was "
                 "asked for."),
                ("mark active",
                 "Supersedes the previous version and records the new one as live, inside a "
                 "transaction so a crash cannot leave two versions both claiming to be active."),
            ]),

            heading("Validation, and how confident it is"),
            para(
                "`caddy validate` is a real check. It loads the configuration through the same "
                "provisioning code a running Caddy uses, which means it catches an unknown "
                "handler, a malformed matcher, a duration in the wrong shape and a plugin the "
                "build does not have. It does not catch a route pointing at a backend that is "
                "down, because that is not a configuration error."
            ),
            note(
                "When no `caddy` binary is reachable, validation falls back to Janus's own "
                "structural checks and **says so**. `janus config validate` reports whether it "
                "was validated by Caddy itself or only by Janus, and the deployment record keeps "
                "that distinction.\n\n"
                "A caller has to be able to tell a proven configuration from a plausible one. "
                "Reporting both as \"valid\" would make the word meaningless.",
                tone="info",
            ),
            para(
                "There is a third case. If a gateway's modules were *declared* rather than "
                "detected — because Caddy runs on another host with a different build — then the "
                "local binary is not a valid judge: it would reject handlers the real gateway "
                "supports. Janus skips binary validation in that case and says why. See "
                "[Caddy modules](/docs/modules)."
            ),

            heading("Why a bad configuration cannot take the gateway down"),
            para("Three independent things, and it is worth knowing that they are independent."),
            steps([
                "**Generation and validation are inside Janus.** A configuration that fails "
                "either never reaches the gateway.",
                "**Caddy loads atomically.** A payload that fails to provision is refused whole, "
                "and the previously running configuration keeps serving. This is Caddy's "
                "behaviour rather than something Janus arranges — and because Janus's safety "
                "argument depends on it, the test suite asserts it against a real binary rather "
                "than assuming it.",
                "**Verification happens after.** If Caddy accepted a payload and is somehow not "
                "running it, the gateway is marked DRIFTED rather than SYNCED, so the dashboard "
                "does not claim a deployment succeeded when it did not.",
            ], ordered=True),
            para(
                "A failed deployment records the stage it stopped at, and the distinction "
                "matters when you are working out what state your edge is in. `validating` means "
                "the gateway was never contacted. `applying` means it was contacted and refused. "
                "Either way it is serving the previous configuration."
            ),

            heading("Versions hold the whole payload"),
            para(
                "A version is not a diff. It stores the complete generated configuration — every "
                "route, matcher and handler as it would be sent."
            ),
            para(
                "That costs storage and buys something worth more: rollback that is "
                "predictable. Restoring version 14 sends exactly the bytes version 14 sent, "
                "regardless of what your models say today. If someone deleted an upstream since, "
                "the restored configuration still contains it, because it was in the payload."
            ),
            para(
                "The alternative — regenerating version 14 from a snapshot of the models as they "
                "were — sounds equivalent and is not. It would depend on the generator behaving "
                "identically today as it did then, which is a promise no codebase can keep "
                "across upgrades."
            ),

            heading("Rollback reads forward"),
            para(
                "Rolling back creates a **new** version carrying the old payload, rather than "
                "re-activating the old version. The history therefore reads forward — \"v19, "
                "which is v14's configuration\" — instead of a version becoming active twice with "
                "two activation dates and an ambiguous story about which one was live when."
            ),
            code("""
janus config history
janus config rollback 14
"""),
            para(
                "Rolling back to a version that was *rejected* is refused: it never ran, so "
                "restoring it is not a rollback but a fresh attempt at something that already "
                "failed validation."
            ),
            note(
                "`configuration.rollback` is a permission of its own, held by Owner and "
                "Administrator but not Operator. Deploying the current desired state and "
                "reverting to a configuration from last week are different authorities, and the "
                "second one usually happens under pressure.",
                tone="info",
            ),

            heading("Drift"),
            para(
                "Drift is Caddy no longer running what Janus last deployed. Someone edited it "
                "through the admin API, reloaded it from a file, or restarted it against a "
                "different configuration."
            ),
            para(
                "Janus detects drift on a schedule and marks the gateway `DRIFTED`. It does "
                "**not** correct it. Automatic correction was considered and rejected: the reason "
                "for drift is sometimes a person fixing an outage by hand at three in the "
                "morning, and a control plane that silently reverted them would be a hazard."
            ),
            para("Putting drift right is one of two deliberate acts:"),
            steps([
                "Redeploy the desired state, discarding the hand-edit. Use `--force`, because the "
                "desired state has not changed and Janus will otherwise report nothing to do.",
                "Or change the desired state in Janus to match what was done, and deploy that — "
                "which leaves a version with an author explaining the change.",
            ]),

            heading("What counts as a change"),
            para(
                "Deploying when nothing has changed does not create a version. History is what "
                "versions exist for, and a version indistinguishable from its predecessor makes "
                "the history harder to read rather than more complete."
            ),
            note(
                "The checksum covers everything a deployment *writes* — the server object and "
                "the access-logger path — rather than just the routes. During development it "
                "covered only the server, and moving the log path reported \"no changes to "
                "deploy\" while the gateway carried on writing to the old file, with nothing to "
                "explain why the collector had gone quiet.",
                tone="caution",
            ),

            heading("Reading a diff"),
            para(
                "The Configuration screen shows a unified diff between the live payload and what "
                "would be deployed. It is a text diff over pretty-printed JSON rather than a "
                "structural comparison, on purpose: what you want before a deployment is to read "
                "the change the way you would read a code review, in context, with the lines "
                "either side."
            ),
            code("""
janus config diff
janus config show          # the whole generated payload
janus config validate      # without deploying
janus config apply --note "add reports route"
"""),

            heading("Warnings are not failures"),
            para(
                "Generation can produce warnings: a rate limit the build cannot enforce, a route "
                "with no upstream, a policy the gateway cannot express. These are shown on the "
                "Configuration screen, in `config validate`, and again at deployment."
            ),
            para(
                "They do not block the deployment. An estate with nine working routes and one "
                "misconfigured one should be able to deploy the nine — refusing everything "
                "because of one problem is how a control plane teaches people to work around it."
            ),
        ],
    },
    {
        "slug": "routing-services",
        "section": "gateway",
        "title": "Routing several services",
        "summary": "A worked walkthrough: three backends behind one gateway, from empty to serving.",
        "blocks": [
            para(
                "Every page before this one describes a piece. This one puts them together. The "
                "worked example is the most common shape an API gateway is asked for: three "
                "services behind one hostname, split by path prefix — `/auth/*` to the "
                "authentication service, `/orders/*` to orders, `/billing/*` to billing."
            ),
            para(
                "Follow it end to end and you will have a gateway that serves real traffic. More "
                "usefully, the four places this shape goes wrong are called out where you meet "
                "them rather than collected in a troubleshooting page you read afterwards."
            ),

            heading("The shape of the work"),
            para(
                "Three objects, created in this order, because each one refers to the last:"
            ),
            terms([
                ("Gateway", "The Caddy instance. You almost certainly have one already — this "
                 "walkthrough assumes it, and [Gateways](/docs/gateways) covers registering one "
                 "if not."),
                ("Upstream", "A pool of backend targets for **one** service. Three services means "
                 "three upstreams, even if each pool holds a single target today."),
                ("Route", "A match rule that sends matching requests to an upstream. Three "
                 "routes, one per service."),
            ]),
            note(
                "A route points at an upstream pool, never at a host and port directly. That "
                "indirection looks like ceremony when a service has one instance, and it is the "
                "reason adding a second instance later is an edit to a pool rather than a change "
                "to your routing table.",
                tone="info",
                title="Why routes do not name hosts",
            ),

            heading("Step 1 — an upstream per service"),
            para(
                "In the dashboard: **Gateway → Upstreams → New**. From the CLI, one command each:"
            ),
            code("""
janus upstreams create --name auth    --targets 10.0.0.11:9000 --health-path /healthz
janus upstreams create --name orders  --targets 10.0.0.12:9000 --health-path /healthz
janus upstreams create --name billing --targets 10.0.0.13:9000 --health-path /healthz
"""),
            para(
                "`--targets` takes `host:port` entries separated by commas, with an optional "
                "third `:weight` field. Replicas of the same service belong in the same pool, "
                "not in a second upstream:"
            ),
            code("""
janus upstreams create --name orders \\
    --targets 10.0.0.12:9000,10.0.0.14:9000,10.0.0.15:9000 \\
    --health-path /healthz
"""),
            para(
                "`--health-path` is worth setting now rather than later. Without it a target that "
                "has stopped answering stays in rotation until enough requests have failed "
                "against it for passive detection to notice, and those failed requests are real "
                "users. With it, Caddy polls the path and removes the target before anyone else "
                "finds out. [Upstreams and load balancing](/docs/upstreams) covers the interval "
                "and threshold settings."
            ),

            heading("Step 2 — a route per service"),
            para("In the dashboard: **Gateway → Routes → New**. From the CLI:"),
            code("""
janus routes create --name Auth    --path '/auth/*'    --upstream auth    --priority 100
janus routes create --name Orders  --path '/orders/*'  --upstream orders  --priority 100
janus routes create --name Billing --path '/billing/*' --upstream billing --priority 100
"""),
            para(
                "Quote the path in a shell. `*` is a glob character, and an unquoted `/auth/*` "
                "will be expanded by your shell against the filesystem before Janus ever sees it."
            ),
            para(
                "The three prefixes do not overlap, so they can share a priority. Priority only "
                "decides between routes that could both match the same request — which is why "
                "the next step is about the routes that *do* overlap."
            ),

            heading("Step 3 — the path matcher, exactly"),
            para(
                "Janus passes the path you type to Caddy verbatim. That means Caddy's matcher "
                "semantics apply exactly, and there is one behaviour here that surprises almost "
                "everybody the first time:"
            ),
            table(
                ["Request", "`/auth/*` matches", "`/auth*` matches"],
                [
                    ["`/auth`", "no", "yes"],
                    ["`/auth/`", "yes", "yes"],
                    ["`/auth/login`", "yes", "yes"],
                    ["`/authorize`", "no", "yes"],
                ],
            ),
            note(
                "`/auth/*` does not match a bare `/auth`. The trailing slash is part of the "
                "pattern, so a request to `/auth` falls through to whatever is below — usually "
                "the catch-all, which is why the symptom is a 404 from the gateway on one "
                "endpoint while every path beneath it works.\n\n"
                "`/auth*` catches the bare path, but it also catches `/authorize`, `/authors` and "
                "anything else beginning with those five characters.",
                tone="caution",
                title="The trailing slash is not decoration",
            ),
            para(
                "Pick deliberately. If no sibling path shares the prefix, `/auth*` is the simpler "
                "choice and covers both forms. If something like `/authorize` exists or might "
                "later, use `/auth/*` and add a second route on the exact path `/auth` at the "
                "same priority, pointing at the same upstream. A route's path is a single "
                "pattern, so two forms need two routes."
            ),

            heading("Step 4 — decide about the prefix"),
            para(
                "The question is what your service expects to receive. A request for "
                "`/auth/login` arrives at the gateway; does the auth service want `/auth/login` "
                "or `/login`?"
            ),
            table(
                ["The service expects", "What to do"],
                [
                    ["`/auth/login` — it knows its prefix", "Nothing. The path is forwarded unchanged."],
                    ["`/login` — it does not", "Add `--strip-prefix /auth`."],
                ],
            ),
            code("""
janus routes create --name Auth --path '/auth/*' \\
    --upstream auth --priority 100 --strip-prefix /auth
"""),
            para(
                "Getting this backwards produces a 404 from the **service**, not from the "
                "gateway. That distinction is the fastest way to diagnose it: if the gateway is "
                "routing correctly but the backend cannot find the path, the prefix is wrong in "
                "one direction or the other. The access log shows the upstream that answered, so "
                "a 404 with an upstream named against it is a stripping problem, and a 404 with "
                "no upstream is a matching problem."
            ),
            note(
                "Strip the prefix consistently across a service, or not at all. A service reached "
                "on `/orders/*` with stripping and `/v2/orders/*` without it has to handle both "
                "shapes, and the second one usually gets discovered in production.",
                tone="info",
            ),

            heading("Step 5 — the catch-all"),
            para(
                "With three routes deployed, a request to `/anything-else` matches nothing. Caddy "
                "answers with its own default, which is an empty 200 — not what an API should say "
                "to a request for a path that does not exist."
            ),
            para(
                "Add a catch-all: path `/*`, priority **0**, action **static**, status 404, and a "
                "JSON body your clients can parse. Priority 0 matters more than anything else on "
                "this page. Routes are emitted in priority order and Caddy answers with the first "
                "match, so a catch-all above your service routes swallows the entire gateway."
            ),
            table(
                ["Priority", "Route", "Why there"],
                [
                    ["100", "`/auth/*` → auth", "Specific prefixes, in any order between themselves."],
                    ["100", "`/orders/*` → orders", "They cannot both match one request."],
                    ["100", "`/billing/*` → billing", ""],
                    ["0", "`/*` → static 404", "Last. Answers what nothing else claimed."],
                ],
            ),
            note(
                "The static action is available in the dashboard, not from the CLI — "
                "`janus routes create` has no `--action` flag and creates proxy routes. Build the "
                "catch-all on the Routes screen, or leave it out and accept Caddy's default.",
                tone="info",
                title="Where the static action lives",
            ),

            heading("Step 6 — protect what needs it"),
            para(
                "Every route created above is `public`, which is the default and is correct for "
                "exactly one of these three services. The auth service must stay public — it is "
                "the endpoint people call to obtain a credential in the first place. The other "
                "two should require one:"
            ),
            code("""
janus routes update 2 --auth jwt
janus routes update 3 --auth jwt
"""),
            note(
                "Setting `--auth` on the auth route itself locks users out of the endpoint they "
                "log in through, and the failure looks like a broken login rather than a "
                "misconfigured gateway. It is the single most common mistake in this shape.",
                tone="critical",
                title="Leave the auth service public",
            ),
            para(
                "Remember what the edge check is: a presence check, not a verification. Janus "
                "confirms a credential was sent and lets your service decide whether it is valid. "
                "[Routes](/docs/routes) explains the reasoning, and [Caddy modules](/docs/modules) "
                "covers what changes when a JWT plugin is present in the build."
            ),

            heading("Step 7 — review, then deploy"),
            para(
                "Nothing so far has reached Caddy. Everything above changed *desired state* in "
                "Janus's database, and the gateway is still serving what it was serving before "
                "you started. Read the diff, then deploy:"
            ),
            code("""
janus config diff
janus config apply --note "route auth, orders and billing"
"""),
            para(
                "`apply` runs the full pipeline — generate, validate, apply, verify. If Caddy "
                "rejects the configuration it is refused atomically and the previous one keeps "
                "serving; you get an error and no outage. [Configuration and "
                "deployments](/docs/configuration) covers the pipeline and rollback."
            ),

            heading("Step 8 — confirm it works"),
            steps([
                "The gateway's sync state reads **SYNCED**, on the dashboard or from "
                "`janus gateways status`. Anything else means the deployment did not land.",
                "Every upstream target reads healthy — `janus upstreams health`.",
                "A real request to each prefix returns what the service returns, not a gateway error.",
                "A request to an undefined path returns your catch-all's 404.",
                "The Analytics screen shows traffic attributed to the right route. Requests "
                "landing on the catch-all that should have matched a service is the signature of "
                "a path-matcher problem.",
            ], ordered=True),

            heading("When it does not work"),
            table(
                ["Symptom", "Most likely cause"],
                [
                    ["One endpoint 404s, everything below it works", "`/x/*` not matching the bare `/x`. See step 3."],
                    ["Everything reaches one service", "A catch-all, or an over-broad prefix, above the others on priority."],
                    ["Gateway routes correctly, service 404s", "Prefix stripping set the wrong way. See step 4."],
                    ["Login broken, everything else fine", "An auth policy on the auth route. See step 6."],
                    ["Changes have no effect", "No deployment. `janus config diff` will still show them as pending."],
                    ["502 from the gateway", "The upstream target is unreachable — check `janus upstreams health` before suspecting the route."],
                ],
            ),

            heading("Tightening the routes"),
            para(
                "The three routes as created match every HTTP method, and every one of them "
                "carries the same 30-second timeout. Both are reasonable starting points and "
                "neither is right for long."
            ),
            para(
                "**Methods.** A route that only reads should say so. `--methods GET,HEAD` on a "
                "catalogue route means a `DELETE` to that path is rejected by the gateway rather "
                "than forwarded to a service that has to decide what to do with it. It also "
                "narrows what an attacker can reach when a backend has an endpoint nobody meant "
                "to expose:"
            ),
            code("""
janus routes update 2 --methods GET,POST,PATCH
"""),
            para(
                "Leave methods empty on a route that fronts a whole service, as `/orders/*` does "
                "here — an empty value matches every method, and enumerating them on a prefix "
                "route means editing the gateway every time the service grows a verb. Methods "
                "earn their keep on narrow paths, not broad ones."
            ),
            para(
                "**Timeouts.** Services differ in how long they are allowed to take, and one "
                "global number is either too tight for the slowest or too generous for the "
                "fastest. An auth check that has not answered in three seconds has failed; a "
                "billing export may legitimately take a minute:"
            ),
            code("""
janus routes update 1 --timeout 5      # auth
janus routes update 3 --timeout 60     # billing
"""),
            note(
                "`routes create` takes no timeout or method flags beyond `--methods`; timeouts "
                "are set with `routes update` or on the Routes screen. Creating a route and "
                "immediately updating it is the expected two-step, and both are recorded "
                "separately in the audit log.",
                tone="info",
            ),

            heading("Reading what was generated"),
            para(
                "Everything above is desired state expressed in Janus's own vocabulary. What "
                "Caddy receives is a JSON server object, and it is worth looking at it once so "
                "the mapping stops being a black box:"
            ),
            code("""
janus config show
"""),
            para(
                "The routes appear in the array in the order Caddy will evaluate them — priority "
                "descending, then id. Reading that array top to bottom is reading the match "
                "sequence, and a catch-all sitting anywhere but last is visible immediately in a "
                "way it is not on a screen sorted by name."
            ),
            para(
                "Each of your prefix routes becomes a matcher on `path`, a `reverse_proxy` "
                "handler naming the pool's targets, and — where you set one — a `rewrite` handler "
                "carrying `strip_path_prefix`. The upstream pool's health settings appear inside "
                "the proxy handler rather than as a separate object, which is why an upstream "
                "with no routes pointing at it generates nothing at all."
            ),

            heading("Growing this shape"),
            para(
                "The three-service layout extends in predictable directions. Adding a fourth "
                "service is another upstream and another route at the same priority. Adding "
                "replicas is targets on an existing pool. Splitting traffic to a new version of "
                "one service is a canary percentage on that service's route, which "
                "[Upstreams and load balancing](/docs/upstreams) covers."
            ),
            para(
                "Serving a second hostname is where the shape changes rather than grows. Routes "
                "carry an optional domain, and a route with no domain matches every host the "
                "gateway serves — so the moment a second hostname exists, routes that were "
                "correct while there was one host begin answering for both. "
                "[Domains and TLS](/docs/domains) covers that transition, including the automatic "
                "HTTPS behaviour that catches people the first time a host matcher appears."
            ),
            para(
                "Whichever direction it grows, every change made here — each upstream, each route, "
                "each deployment — is recorded against the account that made it, with the before "
                "and after state. When a prefix changes and something stops working a fortnight "
                "later, the audit log described in [Roles and permissions](/docs/rbac) is the "
                "fastest way to find out what moved."
            ),
        ],
    },
)
