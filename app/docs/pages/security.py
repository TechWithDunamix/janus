"""Security: access control, rate limiting, API keys and policies."""

from __future__ import annotations

from typing import Any

from app.docs.blocks import code, heading, note, para, steps, table, terms

PAGES: tuple[dict[str, Any], ...] = (
    {
        "slug": "access-control",
        "section": "security",
        "title": "Allowlists and blocklists",
        "summary": "Blocking addresses and ranges — and the deny-by-default surprise.",
        "blocks": [
            para(
                "Rules are written in Janus and enforced by Caddy. Blocking an address is not an "
                "operation Janus performs on live traffic; it is a rule written down, compiled "
                "into configuration, deployed, and enforced at the edge without a round trip to "
                "the control plane."
            ),
            para(
                "That is why a block takes effect on deployment rather than instantly, and why a "
                "blocked request costs the gateway a matcher evaluation rather than a network "
                "call. It is also why your blocklist keeps working while Janus is down — which "
                "is the property you want from the thing that refuses hostile traffic."
            ),

            heading("Addresses and ranges are the same thing"),
            para(
                "A bare address is stored as a host network: `203.0.113.42` becomes "
                "`203.0.113.42/32`, and `2001:db8::1` becomes `2001:db8::1/128`. One column, one "
                "comparison, and no code path that handles single addresses differently from "
                "ranges."
            ),
            para(
                "Ranges are canonicalised too. Writing `192.168.1.42/24` stores `192.168.1.0/24` "
                "— operators mean *that address's network*, and rejecting the input would teach "
                "nothing useful. What you typed and what was stored are both visible on the "
                "blocklist screen, so the normalisation is never a surprise."
            ),
            para(
                "An address that cannot be parsed is refused with a message rather than stored. "
                "A rule that silently matches nothing is worse than no rule, because it looks "
                "like protection."
            ),

            heading("An allowlist is a fence, not an exemption"),
            note(
                "**Any allow rule at a scope makes that scope deny-by-default.** Everything "
                "outside the allowed ranges is refused.\n\n"
                "That is what an allowlist means, and it is rarely what you want *globally* on a "
                "public gateway. A single global allow rule for your office network will make "
                "your API return 403 to every customer you have.",
                tone="critical",
                title="The most consequential thing on this page",
            ),
            para(
                "The confusion is understandable, because the word does two jobs in ordinary "
                "speech. People reach for *allow* meaning *exempt this range from my blocks* and "
                "get *only this range may connect*. Janus implements the second, because that is "
                "what an allowlist is, and says so on the screen where the rule is created."
            ),
            para(
                "If you want the first meaning, you already have it: allow beats block at the "
                "same scope, so an allow rule for `10.1.2.3` inside a block on `10.0.0.0/8` lets "
                "that one address through. The fence only applies to traffic that would otherwise "
                "have matched nothing at all."
            ),
            para(
                "The practical advice is to scope allow rules narrowly. An allowlist on an "
                "internal admin route is exactly right and does what everyone expects; the same "
                "rule with no scope is a gateway-wide outage that looks, from the outside, like "
                "your API has stopped working."
            ),
            para(
                "This was found during development of Janus itself. The demo estate shipped with "
                "a global allow rule for `10.0.0.0/8`, meaning *internal traffic is exempt from "
                "blocks*, and the result was a gateway that answered 403 to every request from "
                "the machine it was running on. The rule was correct; the scope was not."
            ),

            heading("Precedence"),
            para(
                "Allow beats block, and the narrower rule beats the broader one. Janus resolves "
                "by prefix length, so a `/32` decides over a `/8` regardless of which was created "
                "first — creation order is not part of the model, because a rule's meaning should "
                "not depend on when somebody typed it."
            ),
            table(
                ["Rules in effect", "203.0.113.42", "203.0.113.99", "198.51.100.1"],
                [
                    ["block 203.0.113.0/24", "blocked", "blocked", "allowed"],
                    ["block 203.0.113.0/24, allow 203.0.113.42", "allowed", "blocked", "allowed"],
                    ["allow 10.0.0.0/8 (global)", "blocked", "blocked", "blocked"],
                ],
            ),
            para(
                "The third row is the fence in action, and it is worth staring at. Nothing about "
                "`198.51.100.1` is blocked — it simply is not allowed, and at a scope with an "
                "allow rule that is the same thing."
            ),
            para(
                "The client detail screen shows the effective status for an address along with "
                "the rules that produced it, which is the fastest way to answer *why is this "
                "caller getting a 403* without reading the whole list."
            ),

            heading("Scope"),
            terms([
                ("Global", "Applies to every request the gateway serves. The right scope for a "
                 "block; almost never the right scope for an allow."),
                ("Domain", "Applies to requests for one hostname. Useful when one gateway serves "
                 "a public API and an internal one."),
                ("Route", "Applies to requests that matched one route. The right scope for an "
                 "allowlist — an internal-only endpoint fenced to your network, with the rest of "
                 "the gateway unaffected."),
            ]),
            para(
                "Global blocks are emitted first in the generated configuration, above every "
                "route and every policy. A blocked address never reaches a rate limiter, an "
                "authentication check or an upstream, which is both correct and the cheapest "
                "possible answer."
            ),
            para(
                "Route-scoped rules are emitted inside the route's own handler chain, so they "
                "apply only to traffic that already matched it. That ordering is what makes a "
                "route-scoped allowlist safe: it fences one endpoint without touching anything "
                "else."
            ),

            heading("Temporary blocks"),
            para(
                "A block with a duration carries an expiry. Expiry is enforced by *regenerating "
                "the configuration*, not by Caddy: Caddy has no concept of a rule with an end "
                "time, so an expired rule is simply one Janus stops emitting."
            ),
            para(
                "The scheduled job sweeps expired rules every minute and redeploys when it "
                "removes any. Two consequences follow. A temporary block ends within about a "
                "minute of its expiry rather than exactly on it — and a block does not expire at "
                "all if nothing is running the scheduler."
            ),
            note(
                "Temporary blocks are the right tool for a caller misbehaving now that probably "
                "will not be tomorrow: a scraper, a retry storm, a client with a bug somebody is "
                "fixing. Permanent blocks accumulate, and a blocklist nobody prunes eventually "
                "contains an address that has been reassigned to a customer.",
                tone="info",
            ),
            para(
                "The blocklist screen distinguishes permanent from temporary and shows when each "
                "temporary rule ends, so the list is reviewable rather than a pile."
            ),

            heading("IPv6, and the loopback trap"),
            note(
                "A browser or `curl` reaching `localhost` on a modern machine arrives as `::1`, "
                "not `127.0.0.1`. An allowlist containing only the IPv4 loopback refuses every "
                "local request and looks, from the outside, exactly like a broken rule.\n\n"
                "Allowlist both, or use the hostname you actually test with.",
                tone="caution",
            ),
            para(
                "The same applies at the other end. A caller reaching you over IPv6 has an "
                "address in a range your IPv4 blocklist does not cover, so a block on an "
                "attacker's v4 address does nothing about their v6 one. If your gateway accepts "
                "both protocols, your rules need to as well — and if you do not intend to serve "
                "IPv6, the cleanest answer is not to listen on it."
            ),

            heading("Which address is matched"),
            para(
                "Janus emits `client_ip` matchers rather than `remote_ip`. The difference matters "
                "the moment there is anything in front of your gateway: `remote_ip` is the "
                "immediate peer, which behind a load balancer is the load balancer, so a "
                "blocklist written against it would block your own infrastructure and nothing "
                "else."
            ),
            para(
                "`client_ip` respects Caddy's trusted-proxy configuration, which Janus sets "
                "explicitly and **empty by default**. With no trusted proxies, `client_ip` is the "
                "real peer and a forged `X-Forwarded-For` header is ignored."
            ),
            note(
                "That default is the safe one and it is not always the right one. If your gateway "
                "genuinely sits behind a proxy or a CDN, that proxy's address has to be trusted "
                "or every client will appear to be it — your blocklist will match nothing, your "
                "rate limits will count everyone together, and your analytics will show one very "
                "busy client.",
                tone="caution",
            ),

            heading("From the CLI"),
            code("""
janus security block 203.0.113.42 --reason "probing /.env" --minutes 360
janus security block 203.0.113.0/24 --reason "scanning range"
janus security allow 10.0.0.0/8 --reason "internal"
janus security unblock 203.0.113.42

janus security blocklist
janus security allowlist

janus config apply
"""),
            note(
                "Every rule change is desired state. `janus security block` writes the rule; "
                "`janus config apply` is what makes the gateway enforce it. During an active "
                "incident that is two commands, and forgetting the second is the most likely way "
                "to believe you have blocked something you have not.",
                tone="critical",
            ),

            heading("Auditing"),
            para(
                "Every rule change writes an audit entry with the actor, the address, the reason, "
                "the expiry and the front door it came through. Removing a rule records what was "
                "removed, so a rule that existed and no longer does is not a gap in the record."
            ),
            para(
                "Expiry is audited too, attributed to the system. An address that stopped being "
                "blocked has a reason in the log rather than simply disappearing from the list."
            ),

            heading("What blocking does and does not do"),
            para(
                "A blocked request is answered with a 403 by Caddy. It never reaches an upstream, "
                "so a block genuinely protects a backend from load. It does still consume a "
                "connection, a TLS handshake if you serve TLS, and a matcher evaluation at the "
                "gateway — blocking is not free, and a blocklist with thousands of entries is a "
                "matcher list Caddy walks on every request."
            ),
            para(
                "For volumetric attacks, a blocklist at the application gateway is the wrong "
                "layer: the packets have already crossed your network and completed a handshake. "
                "That is a job for something in front — a firewall, a scrubbing service, your "
                "provider's edge."
            ),
            para(
                "What a gateway blocklist is genuinely good at is stopping a specific, identified "
                "caller from reaching your services: a scraper, a broken integration, a "
                "credential-stuffing attempt from a known range. Those are the cases where you "
                "know who and the volume is modest."
            ),
        ],
    },
    {
        "slug": "rate-limiting",
        "section": "security",
        "title": "Rate limiting",
        "summary": "Limits by key and window — and the plugin Caddy needs to enforce them.",
        "blocks": [
            para(
                "A rate limit is a ceiling on requests per key per window. The key decides *who* "
                "is being limited, and it is the part worth thinking about hardest — a limit on "
                "the wrong key is either useless or an outage."
            ),

            heading("Enforcement needs a plugin"),
            note(
                "**Stock Caddy has no rate limiter.** `http.handlers.rate_limit` ships in the "
                "[caddy-ratelimit](https://github.com/mholt/caddy-ratelimit) plugin, not the "
                "standard binary.\n\n"
                "Janus detects which handler modules your gateway's build provides and "
                "**declines to emit a handler it knows would be rejected**. Limits you define are "
                "stored, displayed, and reported as *not enforced* — with a banner on the Rate "
                "Limits screen and a warning on every deployment.",
                tone="caution",
                title="Enforcement requires a rebuild",
            ),
            para(
                "The alternative would be to emit the handler anyway and let the deployment fail. "
                "That trades a clear message on a screen for an opaque provisioning error at "
                "deploy time, and it means one unenforceable limit blocks every other change you "
                "wanted to make in the same deployment."
            ),
            para(
                "Adding the plugin means rebuilding Caddy — see [Caddy modules](/docs/modules), "
                "which generates two forms of the command, each including every plugin already "
                "in your build. The one-shot script runs on the host Caddy lives on and does the "
                "whole job — build (with `xcaddy`) or download a custom build, back up the "
                "current binary, swap it in, restart Caddy, and redeploy:"
            ),
            code("./scripts/setup-caddy.sh --with github.com/mholt/caddy-ratelimit --restart --redeploy"),
            para("Or do it by hand with `xcaddy`, then replace the binary and restart Caddy yourself:"),
            code("xcaddy build --with github.com/mholt/caddy-ratelimit"),
            para(
                "Either way, redeploy afterwards (the script does this for you). The plugin being "
                "present does not enable your limits; Janus has to generate a configuration that "
                "uses it, which happens on the next deployment. To undo a rebuild: "
                "`./scripts/setup-caddy.sh --rollback --restart`."
            ),

            heading("Choosing a key"),
            table(
                ["Key", "Counts per", "Good for", "Fails when"],
                [
                    ["`ip`", "Client address", "Anonymous traffic", "Behind a NAT or a mobile carrier, where thousands share an address"],
                    ["`api_key`", "The presented API key", "Per-customer limits on an authenticated API", "Traffic that carries no key"],
                    ["`user`", "Authenticated identity", "Per-user limits", "The gateway does not know who the user is"],
                    ["`route`", "The route", "Protecting one expensive endpoint from everyone at once", "You wanted to limit a caller, not an endpoint"],
                    ["`domain`", "The hostname", "A shared gateway where one tenant should not exhaust it", "Tenants share a hostname"],
                    ["`global`", "Everything", "A backstop against total overload", "It punishes everyone for one caller"],
                ],
            ),
            para(
                "Keying by IP is the obvious first choice and the one that ages worst. A "
                "corporate NAT presents hundreds of users as one address, so a limit generous for "
                "an individual is restrictive for an office. A mobile carrier is worse — tens of "
                "thousands behind a handful of addresses. Where callers authenticate, key by the "
                "credential."
            ),
            para(
                "The right shape for most APIs is layered: a per-key limit as the customer-facing "
                "quota, a per-IP limit for unauthenticated traffic, and a per-route limit on the "
                "one or two endpoints that are expensive enough to protect on their own."
            ),

            heading("Windows"),
            table(
                ["Window", "Reads as", "Behaviour"],
                [
                    ["1 second", "`10/sec`", "Tight. Smooths bursts hard, and legitimate parallel requests trip it."],
                    ["60 seconds", "`100/min`", "The usual choice. Absorbs a burst while bounding sustained load."],
                    ["3600 seconds", "`10,000/hour`", "A quota rather than a rate. A client can spend it all in the first minute."],
                    ["86400 seconds", "`1,000,000/day`", "A billing-shaped limit, not a protection one."],
                ],
            ),
            para(
                "The longer the window, the less it protects you from a burst and the more it "
                "resembles a quota. A daily limit does nothing about a client that sends its "
                "entire allowance in ten seconds and takes your backend down — that is what the "
                "short window is for."
            ),
            para(
                "A common pairing is a short window to smooth bursts and a long one to bound "
                "total consumption: `20/sec` and `50,000/day` on the same key. They are two "
                "limits, evaluated independently, and both have to pass."
            ),

            heading("Burst"),
            para(
                "Burst is a momentary allowance above the sustained rate. A limit of 100/min with "
                "a burst of 20 lets a client spend 120 in a short spike and then be held to 100 "
                "until the window recovers."
            ),
            para(
                "It exists because real clients are not smooth. A page that fires eight parallel "
                "requests on load is not abusive, but against a strict per-second limit it looks "
                "identical to one. Burst is how you say *bursty is fine, sustained is not*, which "
                "is almost always what you mean."
            ),
            para(
                "A burst of roughly 10–20% of the limit is a reasonable starting point. Much more "
                "and the limit stops bounding anything on short timescales; much less and normal "
                "clients trip it."
            ),

            heading("What a limited caller sees"),
            para(
                "The response status defaults to 429, which is the correct answer and the one "
                "client libraries understand. Anything else — a 503, a 403 — makes a well-behaved "
                "client retry wrongly or give up entirely."
            ),
            para(
                "`Retry-After` tells a client when to come back. Worth sending in almost every "
                "case, because a client that backs off correctly is one you do not have to block "
                "later. Worth withholding when the caller is a scraper you would rather not help "
                "schedule."
            ),
            para(
                "You can set a custom body. A JSON body matching your API's error shape is a "
                "genuine kindness: a client that parses your errors should not have to "
                "special-case the one response that comes from the gateway rather than the "
                "application."
            ),

            heading("Analytics work without the plugin"),
            para(
                "Rate-limit analytics come from 429 responses in the access log, not from the "
                "limiter itself. So the Rate Limits screen shows violations over time, top "
                "offenders and affected routes whether or not the module is present — including "
                "limits enforced by something else entirely in front of your gateway."
            ),
            para(
                "That is useful before you install the plugin. You can see how many 429s your "
                "backends already produce, which callers would be affected by a limit you are "
                "considering, and whether the limit you have in mind would have caught last "
                "week's incident."
            ),
            para(
                "It also means the screen keeps working if you later move rate limiting to a CDN. "
                "The numbers come from what the gateway logged, not from what Janus configured."
            ),

            heading("Rate limits in policies"),
            para(
                "A security policy can apply a named limit conditionally: *if the path matches "
                "this and the caller is not in that range, rate limit*. That is how you express a "
                "limit that depends on something other than the key — see "
                "[Security policies](/docs/policies)."
            ),
            para(
                "The same module requirement applies, and the same warning appears at generation "
                "time when it is missing."
            ),

            heading("From the CLI"),
            code("""
janus ratelimit list
janus ratelimit create --name "Per key" --key api_key --limit 1000 --window 60 --burst 100
janus ratelimit create --name "Order creation" --key api_key --limit 10 --window 1
janus ratelimit update 4 --limit 2000
janus ratelimit delete 4 --yes

janus config apply
"""),

            heading("Choosing a number"),
            para(
                "The honest starting point is your own traffic. Look at the client breakdown for "
                "a busy period, find the ninety-fifth percentile of per-client request rate, and "
                "set the limit several times above it. A limit that your existing legitimate "
                "traffic would have tripped is one you will be paged about."
            ),
            para(
                "Then watch the 429 count. A limit that never fires is not protecting you from "
                "anything; a limit firing constantly is either too low or is catching a real "
                "problem you should look at rather than throttle."
            ),
        ],
    },
    {
        "slug": "api-keys",
        "section": "security",
        "title": "API keys",
        "summary": "Issuing, rotating and revoking keys — and why you only see a secret once.",
        "blocks": [
            para(
                "An API key identifies a consumer at the edge. It can carry scopes, an expiry, an "
                "owning client and a rate limit, and it is what lets analytics attribute traffic "
                "to a customer rather than to an address."
            ),
            para(
                "Keys are a gateway concept here, not a user concept. They are issued to the "
                "systems calling through your gateway, and they are distinct from the sessions "
                "your team uses to sign in to Janus itself."
            ),

            heading("The secret exists once"),
            note(
                "**The secret is shown exactly once, at creation.** Janus stores a SHA-256 digest "
                "and a short prefix. There is no code path — in the interface, the API, the CLI "
                "or the database — that can produce the secret again. If it is lost, rotate the "
                "key.",
                tone="critical",
            ),
            para(
                "This is not caution for its own sake. A control plane that could show you a key "
                "later would be storing it in a form it can read, which means anyone with the "
                "database, a backup, or a replica has every key your customers use."
            ),
            para(
                "Storing a digest means a database compromise leaks nothing usable. It also means "
                "Janus genuinely cannot help when a customer loses their key, which is the "
                "correct trade and worth telling your support team about before it comes up."
            ),
            para(
                "The interface makes the moment deliberate: the secret appears in a panel you "
                "have to dismiss, with a copy button and a warning, rather than in a toast that "
                "disappears while you are reading something else."
            ),

            heading("The prefix"),
            para(
                "Every list shows a key's leading characters — `jan_8fQ2xR…`. That is enough to "
                "recognise a key in an analytics table, a rate-limit rule or a support "
                "conversation, and far too little to authenticate with: the secret is 43 url-safe "
                "characters of entropy after the prefix."
            ),
            para(
                "The access-log collector reduces observed keys to exactly the same prefix "
                "length, which is how traffic is attributed to a key without the key ever being "
                "written somewhere readable."
            ),
            note(
                "Those two lengths have to agree, which is why they come from one constant on the "
                "model. When they disagreed during development — the issuer storing ten "
                "characters and the collector extracting sixteen — every lookup missed and no "
                "request was ever attributed to a key, with nothing failing to say so. A silent "
                "mismatch is the worst kind.",
                tone="info",
            ),

            heading("Scopes"),
            para(
                "A key can carry scopes, matched by a route's `scope` authentication policy. They "
                "are opaque strings, so they can mirror whatever your API already uses — "
                "`orders.read`, `products.write`, `admin`."
            ),
            para(
                "Enforcement of scopes at the edge requires a build that can inspect a "
                "credential. Without one, a route with a `scope` policy checks that a key is "
                "present and leaves the scope to the upstream — which is stated on the route "
                "rather than left for you to discover from behaviour."
            ),
            para(
                "Even unenforced, scopes are worth setting. They document what a key is for, they "
                "show up in the keys list, and they mean the enforcement you add later does not "
                "require reissuing every key."
            ),

            heading("Expiry"),
            para(
                "A key can expire. An expired key stops authenticating and shows as `expired` "
                "rather than being deleted, so historical analytics keep their attribution."
            ),
            para(
                "Expiry is worth using even where you have no policy requiring it. A key with no "
                "end date is one that will still be valid when the integration it was issued for "
                "has been decommissioned, the vendor has been replaced, and the person who asked "
                "for it has left."
            ),
            para(
                "A year is a reasonable default for a customer integration. Ninety days is "
                "reasonable for something internal, where reissuing is cheap."
            ),

            heading("Rotation keeps history"),
            para(
                "Rotating issues a new secret as a **new row** linked to the old one, and revokes "
                "the original."
            ),
            para(
                "Two rows rather than an in-place update, and the reason is analytics: request "
                "rows reference the key that served them, so overwriting the digest would "
                "silently re-attribute every historical request to the new secret. A key issued "
                "yesterday would appear to have been serving traffic for a year."
            ),
            para(
                "The new row carries `rotated_from`, so the lineage is visible on the keys "
                "screen. Usage figures start fresh, which is correct — they describe a "
                "credential, and this is a different credential."
            ),
            code("""
janus keys list
janus keys create --name "Acme production" --scopes products.read,orders.write --expires-in-days 365
janus keys rotate 4
janus keys revoke 4 --reason "leaked in a support ticket"
"""),
            note(
                "Rotation has no overlap window: the old secret stops working the moment the new "
                "one is issued. For a customer integration that means coordinating the swap.\n\n"
                "If you need overlap, issue a *second* key, let the customer migrate, then revoke "
                "the first. That is rotation done manually, with an overlap you control and can "
                "end when you choose.",
                tone="caution",
            ),

            heading("Revocation is final"),
            para(
                "A revoked key cannot be un-revoked. That is deliberate: revocation is what you "
                "do when a key has leaked, and a leaked credential that can be reinstated is one "
                "that can be reinstated by whoever leaked it, or by anyone who talks their way "
                "past your support desk."
            ),
            para(
                "Disabling is the reversible option, for a key you want to stop temporarily — an "
                "integration being paused, a customer in arrears, a suspicious pattern you are "
                "still investigating."
            ),
            table(
                ["State", "Authenticates", "Reversible"],
                [
                    ["active", "yes", "—"],
                    ["disabled", "no", "yes"],
                    ["expired", "no", "by changing the expiry"],
                    ["revoked", "no", "no"],
                ],
            ),

            heading("Clients"),
            para(
                "A key can be linked to a client — a named consumer. That is what turns *this "
                "prefix made forty thousand requests* into *Acme Retail made forty thousand "
                "requests*, and it is what the client detail screen is built around."
            ),
            para(
                "Clients are also created automatically by the collector when traffic arrives "
                "from an address it has not seen. Linking a key to one connects the two views: "
                "the traffic an address generated and the credential it presented."
            ),

            heading("What never contains a secret"),
            steps([
                "**The audit log.** Key fields are stripped before an entry is written, not "
                "hidden when it is displayed — so a secret is never in the table at all.",
                "**The access-log ingest.** The collector drops `Authorization`, `Cookie` and "
                "`X-Api-Key` values, keeping only the prefix.",
                "**The API.** No endpoint returns a secret except the two that create one.",
                "**The CLI.** `janus keys list` shows prefixes. There is no `--show-secret`, and "
                "adding one would require changing what is stored.",
                "**Logs.** Janus does not log request bodies, and the key creation response is "
                "not logged.",
            ]),
            para(
                "The test suite asserts several of these directly, including that a secret does "
                "not appear anywhere in the API's key listing or in any audit row."
            ),
        ],
    },
    {
        "slug": "policies",
        "section": "security",
        "title": "Security policies",
        "summary": "Conditional rules, ordering, and why there is no expression language.",
        "blocks": [
            para("Every policy reads the same way:"),
            code("IF <subject> <operator> <value> THEN <action>", lang="text"),
            para(
                "For example: `IF path matches /.env* THEN block`, or `IF cidr in_cidr "
                "10.0.0.0/8 THEN allow`, or `IF header X-Env: staging THEN block`."
            ),
            para(
                "Policies sit above the route table. A policy that blocks runs before any route "
                "matches, which is what you want for a scanner: there is no reason to evaluate "
                "nine route matchers for a request to `/.env`."
            ),

            heading("Why a closed vocabulary"),
            para(
                "The obvious alternative is an expression language — CEL, a small DSL, something "
                "with `and`, `or` and parentheses. It was considered and rejected, for two "
                "reasons."
            ),
            para(
                "The first is that a DSL means shipping a parser into the configuration path, and "
                "a parser is the last thing that should stand between an operator and their "
                "gateway. A malformed expression becomes a deployment failure at best, and at "
                "worst a rule that parses cleanly and matches nothing — protection that is not "
                "there, with nothing to say so."
            ),
            para(
                "The second is that the closed set is small enough for the generator to *prove* "
                "what it emits. Every subject maps to exactly one Caddy matcher, so Janus can "
                "tell you at generation time that a policy cannot be expressed on this build — "
                "rather than emitting something plausible and letting Caddy reject the whole "
                "deployment."
            ),
            para(
                "The cost is real and worth stating: you cannot write *if this path and that "
                "header and not this range*. What you can do is order several policies so the "
                "combination has the effect you want, which is more verbose and considerably "
                "easier to read six months later when somebody asks why a caller is being "
                "refused."
            ),

            heading("Subjects"),
            table(
                ["Subject", "Matches on", "Notes"],
                [
                    ["`ip` / `cidr` / `client`", "Client address", "Canonicalised to a network, like access-control rules. Uses `client_ip`, so it respects trusted proxies."],
                    ["`path`", "Request path", "Prefix with a trailing `*`, or exact."],
                    ["`method`", "HTTP method", "Uppercased before matching."],
                    ["`header`", "A request header", "Written `Name: value`. A `*` value matches any presence of the header."],
                    ["`api_key`", "The `X-Api-Key` header", "Matches a specific key value."],
                ],
            ),
            para(
                "`country` is accepted by the model and requires the GeoIP plugin to be "
                "expressible. Without it, the policy is stored, shown, and reported as not "
                "emitted at generation time — see [Caddy modules](/docs/modules)."
            ),

            heading("Actions"),
            terms([
                ("block", "Answer 403 and stop. The request never reaches a route."),
                ("allow", "Stop policy evaluation and continue to the route table. Ordered above "
                 "a block, this is how one caller gets through a broad rule."),
                ("require_auth", "Answer 401 when no credential is present. Narrower than putting "
                 "an auth policy on every route, and it applies before routing."),
                ("rate_limit", "Apply a named limit. Needs the rate-limit module."),
                ("log", "Record only. Emits no handler, because every request is logged anyway; "
                 "it exists so a rule can be written and its match count observed before it is "
                 "switched to blocking."),
            ]),
            para(
                "The `log` action is the one to reach for first when writing a policy against "
                "real traffic. Write it, deploy it, and watch the match count on the Policies "
                "screen for a day. A rule that matches ten thousand times a day was going to "
                "block ten thousand requests, and you would rather know that before it does."
            ),

            heading("Ordering"),
            para(
                "Policies are evaluated highest priority first, and **above the route table**. An "
                "allow ordered above a block lets a specific caller through a broad rule without "
                "either being deleted:"
            ),
            table(
                ["Priority", "Policy", "Effect"],
                [
                    ["200", "`IF cidr in_cidr 10.0.0.0/8 THEN allow`", "Internal traffic skips the rules below."],
                    ["100", "`IF path matches /.env* THEN block`", "Everyone else probing for config files is refused."],
                    ["99", "`IF path matches /wp-admin* THEN block`", "Likewise."],
                    ["50", "`IF path matches /admin* THEN require_auth`", "Admin paths need a credential before routing."],
                ],
            ),
            para(
                "Give policies distinct priorities. Two policies at the same priority have an "
                "order, but it is not one you chose, and it can change."
            ),
            para(
                "Leave gaps. Numbering 100, 200, 300 rather than 1, 2, 3 means inserting a rule "
                "between two existing ones does not require renumbering both."
            ),

            heading("Scope"),
            para(
                "Like access-control rules, a policy can be global, scoped to a domain, or scoped "
                "to a route."
            ),
            para(
                "Scoping a `block` to a route is unusual — if a caller should not reach one "
                "route, they usually should not reach any — but scoping `require_auth` or "
                "`rate_limit` to a route is exactly right, and is the main reason route scope "
                "exists."
            ),

            heading("What cannot be expressed"),
            para(
                "A policy Janus cannot turn into a Caddy matcher is reported as a warning at "
                "generation time rather than emitted and rejected. You see it on the "
                "Configuration screen, in `janus config validate`, and again at deployment."
            ),
            para(
                "The common case is a subject the build has no matcher for. Country matching "
                "needs the GeoIP plugin; a `rate_limit` action needs the rate-limit plugin. In "
                "both cases the policy is stored and displayed, and the warning names what is "
                "missing."
            ),
            note(
                "Policies count their matches, so a rule that has never matched anything is "
                "visible on the Policies screen. That is usually a sign the rule does not do what "
                "its author thought — a path prefix missing its `*`, a header name that is not "
                "the one being sent, or a range written for the wrong protocol.",
                tone="info",
            ),

            heading("Policies versus access-control rules"),
            para(
                "There is deliberate overlap: you can block an address with an IP rule or with a "
                "policy. **Use the IP rule.**"
            ),
            para(
                "It is purpose-built, it supports expiry, it has a dedicated screen showing what "
                "is currently blocked, the client detail view resolves it, and the audit trail "
                "records it as a block rather than as a policy change. Reaching for a policy to "
                "block an address means the blocklist screen no longer tells the whole story, "
                "which is the sort of thing that costs an hour during an incident."
            ),
            para(
                "Policies are for the conditions IP rules cannot express: a path, a method, a "
                "header, a credential, or a combination of an address with one of those."
            ),

            heading("A starting set"),
            para(
                "Most gateways want the same three or four policies on day one, and they are "
                "cheap:"
            ),
            steps([
                "Block probes for configuration files: `path matches /.env*`.",
                "Block probes for common admin panels you do not run: `path matches /wp-admin*`, "
                "`/phpmyadmin*`.",
                "Allow your internal range above them, at a higher priority, so an internal "
                "scanner does not trip your own rules.",
                "If you have an admin surface, `require_auth` on its path prefix.",
            ], ordered=True),
            para(
                "None of these is sophisticated. They cost one matcher evaluation each and they "
                "remove a visible amount of noise from your 404 count, which makes the genuine "
                "404s worth looking at."
            ),
        ],
    },
)
