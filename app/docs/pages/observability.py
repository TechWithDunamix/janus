"""Observability: analytics, route ranking, alerts and anomalies."""

from __future__ import annotations

from typing import Any

from app.docs.blocks import code, heading, note, para, steps, table, terms

PAGES: tuple[dict[str, Any], ...] = (
    {
        "slug": "analytics",
        "section": "observability",
        "title": "Analytics",
        "summary": "Where the numbers come from, and what each table can honestly answer.",
        "blocks": [
            para(
                "Everything on the analytics screens comes from two things Caddy produces: its "
                "JSON access log and its Prometheus metrics endpoint. Janus does not sample, does "
                "not estimate, and runs no agent inside your services."
            ),
            para(
                "The pleasant consequence is that the request count on the dashboard is the "
                "request count Caddy served — there is no sampling rate to reason about, and no "
                "extrapolation. The inconvenient one is that Janus only knows what Caddy logs: "
                "if a field is not in the access log, no screen can show it, and adding one means "
                "changing the log format rather than changing Janus."
            ),

            heading("Two sources, two jobs"),
            terms([
                ("Access logs",
                 "One JSON line per request: method, host, path, status, duration, request and "
                 "response sizes, client address and headers. This is what answers request-level "
                 "questions — which client, which user agent, which individual request."),
                ("Metrics",
                 "Caddy's Prometheus endpoint. Aggregate counters, cheap and always available, "
                 "with no room for a client inside them. Good for *how many requests has this "
                 "gateway served since it started* and *how many are in flight right now*; "
                 "useless for *who*."),
            ]),
            para(
                "Metrics are also the only source that survives a log rotation you did not "
                "expect, which is why the gateway health figures come from them rather than from "
                "counting rows."
            ),

            heading("Two tables"),
            table(
                ["Table", "Grain", "Answers", "Retention"],
                [
                    ["`request_logs`", "One row per request", "Which client got the 502s", "Hours"],
                    ["`route_rollups`", "One row per route per minute", "Every chart and time range", "Months"],
                ],
            ),
            para(
                "The split exists because the two questions have wildly different costs. A "
                "30-day traffic chart over request rows would scan every request the gateway "
                "served to draw a line with 720 points on it. Over rollups it scans 720 rows per "
                "route — three orders of magnitude less work for an identical picture."
            ),
            para(
                "So the rule is: anything with a *time range* reads rollups; anything needing an "
                "*individual request* reads request rows, and is bounded by a short retention "
                "window. Screens that fall back to request-level detail say so when the range you "
                "asked for is longer than what they cover, rather than showing a shorter window "
                "as though it were the whole thing."
            ),
            note(
                "This is why the error breakdown shows per-code totals for a 30-day range but "
                "only recent traffic in its by-client table. The first comes from rollups and "
                "covers everything; the second comes from request rows and covers a week. The "
                "screen states the cutoff rather than leaving you to assume the two agree.",
                tone="info",
            ),

            heading("What a rollup contains"),
            para(
                "Each row is one route in one minute, and carries more than a request count "
                "because the alternative is going back to request rows for every question."
            ),
            table(
                ["Group", "Columns"],
                [
                    ["Volume", "requests, bytes in, bytes out, distinct clients"],
                    ["Status classes", "1xx, 2xx, 3xx, 4xx, 5xx"],
                    ["Individual statuses", "401, 403, 404, 429, 502, 503, 504"],
                    ["Edge actions", "blocked, rate limited, timeouts"],
                    ["Latency", "sum, max, and an eleven-bucket histogram"],
                ],
            ),
            para(
                "The individually broken-out statuses are the ones the error screen groups by. "
                "Deriving them from request rows would have made the errors screen the one page "
                "that could not answer a 30-day question."
            ),
            para(
                "`distinct clients` is approximate by nature and labelled as such. It is counted "
                "per minute and cannot be summed across minutes without double-counting anyone "
                "who appeared in two of them, so the interface only ever shows it per minute or "
                "recomputes uniques from request rows for short ranges."
            ),

            heading("Why the grain is one minute"),
            para(
                "A minute is the finest resolution any Janus chart draws, and a coarser grain "
                "cannot be re-cut. Fifteen-minute buckets cannot answer a fifteen-minute window "
                "aligned to *now* — you would get a window aligned to the quarter hour, which is "
                "a different question and one nobody asked."
            ),
            para(
                "Longer ranges aggregate upward at query time: a 24-hour chart sums minutes into "
                "fifteen-minute buckets, a 30-day chart into hours. Aggregating up is always "
                "possible; aggregating down never is."
            ),
            table(
                ["Range", "Bucket", "Points"],
                [
                    ["15 minutes", "1 minute", "15"],
                    ["1 hour", "1 minute", "60"],
                    ["6 hours", "5 minutes", "72"],
                    ["24 hours", "15 minutes", "96"],
                    ["7 days", "1 hour", "168"],
                    ["30 days", "1 hour", "720"],
                ],
            ),
            para(
                "The widths are chosen so every range lands between 60 and 180 points. Too few "
                "and a spike disappears into a bucket; too many and the chart is noise and the "
                "query is slow. Thirty days at hourly resolution is the one deliberate exception, "
                "because a month of hourly detail is worth the extra pixels."
            ),

            heading("Percentiles, and why there is no average column"),
            note(
                "Rollups store a latency **histogram**, not an average, because averages do not "
                "compose. The mean of per-minute means is not the mean — it weights a quiet "
                "minute the same as a busy one — and a P95 of P95s is not a percentile at all.\n\n"
                "Janus sums histograms across the requested range and interpolates once, so a "
                "percentile is always a percentile of the whole range rather than an average of "
                "percentiles.",
                tone="info",
                title="The reason for the histogram",
            ),
            para(
                "The histogram has eleven buckets with bounds at 5, 10, 25, 50, 100, 250, 500, "
                "1000, 2500 and 5000 milliseconds, plus an overflow. Nine of them cover 5ms to "
                "2.5s, which is where an API gateway actually lives; the two tails absorb "
                "everything else."
            ),
            para(
                "That gives percentiles accurate to bucket width. A P95 reported as 340ms is "
                "somewhere between 250 and 500, interpolated by where the rank falls inside the "
                "bucket. For an operations console that is the right trade: a t-digest would be "
                "more precise, would cost more to store and merge, and would not change a single "
                "decision anyone makes from this screen."
            ),
            para(
                "The overflow bucket reports its lower bound rather than a guess. A request "
                "slower than five seconds could be any amount slower, and inventing a number for "
                "it would be inventing precision the data does not contain."
            ),
            para(
                "One consequence worth knowing: the bucket bounds are a constant rather than a "
                "setting. Changing them would invalidate every rollup already written, because "
                "the stored counts would no longer mean what the new bounds say they mean."
            ),

            heading("What the collector drops"),
            para(
                "Caddy's access log contains full request and response headers. Everything "
                "outside a small allowlist is discarded before a row is written: no "
                "`Authorization`, no `Cookie`, no `X-Api-Key` value, no request or response body."
            ),
            para(
                "An API key is reduced to its prefix, which is enough to attribute traffic to it "
                "and useless for authenticating. Whether a credential was *present* is recorded; "
                "what it was is not."
            ),
            note(
                "Filtering happens at the boundary rather than at display time, because a secret "
                "that reaches the database is already leaked. Redacting on the way out would mean "
                "the secret is in the backups, in the replicas, and in whatever query somebody "
                "runs against the table directly.",
                tone="caution",
            ),
            para(
                "What is kept: user agent and referer, both truncated. They are useful for "
                "identifying a scraper and neither is a credential."
            ),

            heading("How requests are attributed to routes"),
            para(
                "The collector matches each logged request against the route table in memory, "
                "using the same precedence Caddy does: host first, then longest path, then "
                "method. It does this in Python rather than asking the database per request, "
                "because ingestion processes thousands of rows at a time and a query per row "
                "would dominate the run."
            ),
            para(
                "The matcher implements the two path forms Janus routes can have — prefix and "
                "exact. Implementing more would be implementing a matcher for configurations "
                "that cannot exist."
            ),
            para(
                "Requests matching no route are still counted, under a null route. That is where "
                "scanning shows up: a burst of 404s attributed to nothing is exactly the shape "
                "of somebody probing for `/wp-admin`, and a table that discarded unmatched "
                "traffic would be a table that cannot see it."
            ),

            heading("Ingestion, and what happens when it stops"),
            para(
                "The collector reads forward from a stored byte offset, per gateway. It advances "
                "the offset only after a successful ingest, so a crash mid-batch replays that "
                "batch rather than losing it."
            ),
            para(
                "Replaying can double-count the requests in that batch. That is the deliberate "
                "trade: for traffic analytics a slight over-count after a crash is a smaller "
                "problem than a silent hole, because a hole looks exactly like a quiet period and "
                "an over-count looks like a spike somebody will investigate."
            ),
            para(
                "A partially written final line is left for the next pass. Caddy may be midway "
                "through writing it, and parsing half a JSON object would either fail or — worse "
                "— succeed with a truncated path."
            ),
            para(
                "Log rotation is handled by tracking the file's inode alongside the offset. When "
                "the inode changes the offset resets to zero; otherwise an offset four megabytes "
                "into a freshly rolled file would skip the first four megabytes of new requests. "
                "A file that shrinks in place resets too."
            ),
            note(
                "Each gateway must read its **own** log file. The offset is per gateway, so two "
                "gateways pointed at one file both read all of it and both count everything — a "
                "gateway reporting traffic it never served, with nothing failing to say so.",
                tone="critical",
            ),

            heading("Retention"),
            table(
                ["Setting", "Default", "Governs"],
                [
                    ["`REQUEST_RETENTION_HOURS`", "168 (7 days)", "How far back request-level screens reach."],
                    ["`ROLLUP_RETENTION_DAYS`", "90", "How far back charts reach."],
                ],
            ),
            para(
                "Request rows are the expensive ones. A gateway at a hundred requests a second "
                "produces around 8.6 million rows a day, so seven days is tens of millions of "
                "rows — worth sizing before you raise it. Rollups at the same rate are a few "
                "thousand rows a day, because they are per route per minute regardless of volume."
            ),
            para(
                "Pruning runs hourly under the scheduler. It is a large delete, which is why it "
                "is on the slowest schedule of any job — competing with ingestion for the same "
                "tables would slow the thing every other number depends on."
            ),

            heading("Simulated data is labelled"),
            para(
                "Rows created by `janus seed` carry a `simulated` flag, and the flag travels with "
                "the data all the way to the screen. A range containing simulated rows says so, "
                "on the screen, next to the numbers."
            ),
            para(
                "The flag is on the row rather than inferred from the environment, so a database "
                "that has held both can still tell them apart — and so a demo estate later "
                "pointed at a real Caddy shows the transition honestly rather than retroactively "
                "claiming the old numbers were observations."
            ),

            heading("From the CLI"),
            code("""
janus analytics overview --range 24h
janus analytics routes --range 1h
janus analytics errors --range 24h
janus analytics clients
janus analytics traffic
janus collect                      # ingest immediately
"""),
            para(
                "Every one takes `--json`, which is how you get these numbers into something "
                "else. The JSON is the same structure the dashboard receives."
            ),
        ],
    },
    {
        "slug": "problematic-routes",
        "section": "observability",
        "title": "Problematic routes",
        "summary": "How the ranking is computed, and why it shows its working.",
        "blocks": [
            para(
                "The most important panel on the dashboard answers one question: **what should I "
                "look at first?** Everything else on the overview describes the estate; this "
                "ranks it."
            ),

            heading("Why not just sort by error rate"),
            para(
                "Because error rate alone produces a ranking that is technically correct and "
                "operationally useless. A route with eleven requests and eleven failures is at "
                "100%, and it will sit above a route at 3% that is failing four thousand requests "
                "an hour."
            ),
            para(
                "The eleven-request route might be a health check nobody wired up, a monitoring "
                "probe pointed at a path that moved, or a client that has been broken for six "
                "months and nobody has noticed because nobody uses it. The 3% route is your "
                "checkout. A ranking that puts them in that order is one people learn to scroll "
                "past — and a panel people scroll past is worse than no panel, because it "
                "occupies the space a useful one would have."
            ),
            para(
                "Sorting by absolute error count has the opposite failure. Your busiest route "
                "will usually have the most errors in absolute terms even when its rate is fine, "
                "so the top of the list becomes a list of your busiest routes."
            ),

            heading("The score"),
            para(
                "Four components, each normalised to roughly 0–1 against its critical threshold, "
                "then weighted:"
            ),
            table(
                ["Component", "Weight", "Measures"],
                [
                    ["Server errors", "45%", "5xx rate against `ERROR_RATE_CRITICAL`. The strongest signal, because a 5xx is your fault."],
                    ["Latency", "25%", "P95 against `LATENCY_P95_CRITICAL_MS`."],
                    ["Client errors", "15%", "4xx rate, weighted lower — often the caller's fault, and a 404-heavy route may be doing its job."],
                    ["Timeouts", "15%", "Timeouts as a share of requests. Distinct from 5xx because it points at the network or a saturated backend rather than at application code."],
                ],
            ),
            para(
                "The weighted total is then scaled by traffic share, so a route carrying most of "
                "your requests weighs above one carrying a handful. The scaling is deliberately "
                "sub-linear — a fourth root — so a small route with a genuine problem still "
                "appears rather than being buried under volume."
            ),
            para(
                "The choice of a fourth root rather than a square root or a linear weighting is "
                "a judgement rather than a derivation. Linear buries everything but your busiest "
                "route; no weighting at all puts the eleven-request route on top. A fourth root "
                "means a route with 1% of your traffic still scores about a third of what an "
                "identical route with all of it would."
            ),
            para(
                "Routes below a floor of twenty requests in the window are not ranked at all. A "
                "rate computed from nine requests is not a rate, and including it means the "
                "ranking is dominated by noise whenever traffic is low — which is exactly when "
                "you are most likely to be looking at it out of hours."
            ),

            heading("It publishes its working"),
            para(
                "Every ranked route shows its component scores and its traffic share alongside "
                "the raw figures. That is not decoration."
            ),
            note(
                "A ranking that says *this is worst* without saying why is one an operator has "
                "to take on faith — and the first time it disagrees with their intuition, they "
                "stop using it. Showing the components turns a disagreement into a conversation "
                "about weights rather than a reason to distrust the panel.",
                tone="info",
            ),
            para(
                "It also means you can tell *what kind* of problem a route has at a glance. A "
                "route topping the list on latency with zero server errors is a different "
                "investigation from one topping it on 5xx: the first is capacity or a slow "
                "dependency, the second is usually a deploy."
            ),
            para(
                "And it makes the thresholds discussable. If a route is CRITICAL at a latency "
                "your team considers normal, the component score tells you it is the latency "
                "threshold that needs changing rather than the ranking that needs ignoring."
            ),

            heading("Thresholds are configuration"),
            table(
                ["Setting", "Default", "Meaning"],
                [
                    ["`ERROR_RATE_CRITICAL`", "0.05", "5xx rate at which a route is CRITICAL. Half of it is WARNING."],
                    ["`LATENCY_P95_CRITICAL_MS`", "1000", "P95 at which a route is CRITICAL. Half of it is WARNING."],
                ],
            ),
            para(
                "These are settings rather than constants because *bad* is deployment-specific. "
                "An internal batch API where every call takes four seconds and 2% fail on "
                "contention is healthy; a public checkout with the same numbers is an incident. "
                "One threshold cannot describe both, and a product that shipped one would be "
                "wrong for most of the people using it."
            ),
            para(
                "They are global rather than per route, which is a real limitation. A gateway "
                "carrying both a checkout and a batch API has to pick a threshold that suits one "
                "of them. The workaround is two gateways, which is also the better architecture "
                "for other reasons."
            ),

            heading("Status"),
            terms([
                ("CRITICAL", "Server error rate at or above the threshold, or P95 at or above it."),
                ("WARNING", "Half the error threshold, or double it in combined 4xx and 5xx, or "
                 "half the latency threshold."),
                ("OK", "Below both, or below the traffic floor to judge at all."),
            ]),
            para(
                "The combined 4xx-and-5xx condition catches a route that is not failing on the "
                "server side but is rejecting most of what it receives. That is usually a client "
                "integration that has broken, and it is worth surfacing even though nothing on "
                "your side is technically wrong."
            ),

            heading("Investigating from the ranking"),
            para(
                "Clicking a route opens its detail view: traffic and errors over time, the "
                "latency percentile chart, the status distribution and the client breakdown, all "
                "for that route alone."
            ),
            para("That page is arranged for one sequence of questions:"),
            steps([
                "**Is this new, or has it always been like this?** The time series. A route that "
                "has been at 4% for a month is a different problem from one that was at 0.1% an "
                "hour ago.",
                "**Is it everything or a tail?** The percentile chart. A fine P50 and a bad P99 "
                "means a subset of requests — a slow query on one code path, or one backend in "
                "the pool being slower than the rest.",
                "**Is it one caller or everyone?** The client breakdown. One client generating "
                "all the errors is an integration problem; every client is yours.",
                "**Is it one status or several?** The distribution. A wall of 503s points at a "
                "dependency; a mix of 500s points at code.",
            ], ordered=True),
            para(
                "The order matters. Answering the first question wrong — assuming something is "
                "new when it is not — sends people looking at the last deploy for a problem that "
                "predates it."
            ),

            heading("Slow routes are ranked separately"),
            para(
                "The Performance screen ranks by P95, then P99, with no blending and no traffic "
                "weighting. It is a different question from *what is worst overall*, and a route "
                "can be the slowest thing on the estate while being perfectly healthy."
            ),
            para(
                "P95 rather than the mean, because a mean hides exactly the thing users complain "
                "about. A route with a 40ms median and a 4s P99 has a fine mean and a subset of "
                "users having a terrible time — and it is the subset that opens tickets."
            ),
            para(
                "The table also shows P50, max and timeout count. P50 against P95 is the shape "
                "of the distribution: close together means uniformly slow, far apart means a "
                "tail, and a tail is usually a specific thing rather than a general one."
            ),
            code("""
janus analytics problematic --range 24h
janus analytics performance --range 24h
"""),

            heading("What the ranking will not tell you"),
            steps([
                "**Why.** It ranks symptoms. The detail view narrows it; your logs and traces "
                "answer it.",
                "**Whether it matters.** A route at 12% errors might be an endpoint nobody "
                "depends on. Janus does not know what your routes are for.",
                "**Whether it is getting worse.** The ranking is a snapshot of the window. "
                "Compare two windows, or watch the series on the detail page.",
            ]),
        ],
    },
    {
        "slug": "alerts-anomalies",
        "section": "observability",
        "title": "Alerts and anomalies",
        "summary": "Two different things, kept deliberately apart.",
        "blocks": [
            terms([
                ("An alert",
                 "A threshold someone chose being crossed. 5xx above 5%; P95 above a second; an "
                 "upstream target Caddy has taken out of rotation; a gateway that has drifted."),
                ("An anomaly",
                 "Janus observing that the last few minutes do not look like the preceding hour. "
                 "Often nothing at all — traffic doubles at 09:00 every weekday and nobody needs "
                 "telling."),
            ]),
            note(
                "Presenting the second as though it were the first is how a dashboard teaches its "
                "users to ignore it. They are separate tables, separate screens and separate "
                "words, and an anomaly is promoted to an incident only by a person.",
                tone="info",
            ),
            para(
                "The distinction sounds pedantic until you have used a tool that collapses them. "
                "A single list mixing *your checkout is failing* with *Tuesday is busier than "
                "Monday* trains people to skim, and skimming is how the checkout entry gets "
                "missed."
            ),

            heading("Alerts are stateful"),
            para(
                "The evaluator opens an alert when a condition starts holding and resolves it "
                "when it stops. It does not emit a row per evaluation."
            ),
            para(
                "The alternative turns a five-minute outage into five alerts at a one-minute "
                "evaluation interval, and an operator learns very quickly to ignore that list. "
                "Stateful alerts mean the open list is a description of the present rather than a "
                "log of the past: if something is on it, it is happening now."
            ),
            para(
                "An open alert keeps its numbers current. Looking at one tells you what the error "
                "rate *is*, not what it was when the alert fired — which is the number you "
                "actually want when deciding whether to keep watching or start acting."
            ),
            para(
                "Resolution is automatic when the condition stops holding. There is no "
                "acknowledge-to-silence, because silencing an alert that is still true is a way "
                "to forget about a problem, and the resolution is the honest signal that it has "
                "stopped."
            ),

            heading("What is evaluated"),
            table(
                ["Alert", "Condition", "Severity"],
                [
                    ["`error_rate_5xx`", "A route's 5xx rate at or above the critical threshold, over 15 minutes, with at least 50 requests.", "Critical"],
                    ["`latency_p95`", "A route's P95 at or above the critical threshold, same window and floor.", "Warning"],
                    ["`upstream_unhealthy`", "Caddy has taken a target out of rotation.", "Critical"],
                    ["`sync_failed`", "A gateway is FAILED or DRIFTED.", "Critical / Warning"],
                ],
            ),
            para(
                "The fifty-request floor is the same idea as the ranking's, set higher. A rate "
                "computed from a handful of requests is not a rate, and an alert that fires on it "
                "is noise arriving at three in the morning."
            ),
            para(
                "The fifteen-minute window is a compromise. Shorter and a brief blip pages you; "
                "longer and a real outage takes too long to surface. Fifteen minutes means a "
                "sustained problem is visible within a few evaluation cycles and a thirty-second "
                "wobble is not."
            ),
            para(
                "`upstream_unhealthy` comes from Caddy's own health checkers rather than from a "
                "Janus probe, which means it agrees with the thing actually routing traffic. See "
                "[Upstreams and load balancing](/docs/upstreams) for why that matters."
            ),
            para(
                "`sync_failed` is the one that is about Janus rather than about your traffic. A "
                "DRIFTED gateway is serving something you did not deploy; a FAILED one did not "
                "take your last change. Neither is visible in the traffic figures, which is "
                "exactly why it is an alert."
            ),

            heading("The anomaly method, stated plainly"),
            para(
                "A z-score against the mean and standard deviation of the preceding hour, flagged "
                "above three deviations. Compared per route, on three metrics: requests per "
                "minute, 5xx rate and mean latency."
            ),
            para(
                "It is not a model. It does not learn, it knows nothing about the time of day, "
                "and it has no concept of a weekend or a release schedule. It will flag your "
                "Monday morning, and it will flag the traffic drop when you take a client "
                "offline for maintenance."
            ),
            para(
                "That is stated rather than hidden because a naive detector described honestly is "
                "more useful than a sophisticated one described vaguely. You can predict what it "
                "will do, which means you can predict which of its findings to ignore — and a "
                "detector whose false positives are predictable is one people keep reading."
            ),
            para(
                "Every finding shows its observation, its baseline and its deviation, so the "
                "arithmetic is checkable rather than something to believe. A finding at 3.1σ on a "
                "noisy metric and one at 12σ on a stable one are different claims, and the "
                "numbers say which is which."
            ),
            note(
                "A minimum baseline deviation guards the degenerate case. Perfectly flat traffic "
                "has σ≈0, and without a floor any change at all would be infinitely many "
                "deviations from the mean — so a gateway serving exactly 100 requests a minute "
                "would alarm at 101. The floor is the larger of 10% of the mean and 1.",
                tone="info",
            ),
            para(
                "The baseline needs at least fifteen minutes of data before anything is flagged. "
                "A route that has just been created has no baseline, and comparing its first five "
                "minutes against its first fifteen would flag every new route as anomalous."
            ),

            heading("Deduplication"),
            para(
                "One anomaly per kind per route per hour. A sustained spike is one finding rather "
                "than a new one every time the detector runs — the same reasoning as stateful "
                "alerts, applied to something that has no natural end."
            ),
            para(
                "Anomalies do not resolve, because there is nothing to resolve. An observation "
                "that the last five minutes were unusual does not become false later; it becomes "
                "old. They age out with the analytics retention."
            ),

            heading("Verdicts"),
            para(
                "An anomaly starts `unreviewed`. A person marks it `benign` or `incident`. Janus "
                "never sets a verdict itself."
            ),
            para(
                "That is the whole point of keeping anomalies separate. The system can say *this "
                "is unusual*; only a person can say *this is a problem*. Collapsing the two would "
                "mean either alerting on every unusual thing, or suppressing genuine incidents "
                "that happened to look like Monday."
            ),
            para(
                "The verdicts are also a record. A recurring pattern marked benign three times is "
                "a signal about the detector rather than about the traffic, and the security "
                "overview shows the unreviewed count so a growing backlog is visible."
            ),

            heading("Reading the security overview"),
            para(
                "Alerts and anomalies share a screen but not a panel. The alerts panel is a "
                "worklist: everything on it is currently true and something should be done. The "
                "anomalies panel is a feed: things worth a glance, most of which are nothing."
            ),
            para(
                "The counts at the top of the screen — open alerts, unreviewed anomalies — are "
                "deliberately separate for the same reason."
            ),

            heading("What is deliberately absent"),
            steps([
                "**No notification channels.** No email, no Slack, no PagerDuty. Alerts are "
                "visible in the interface and through the API; routing them to a human is what "
                "the alerting system you already have is for, and it is better at it.",
                "**No alert rules you can author.** The conditions are fixed and the thresholds "
                "are configuration. A rule builder is a small expression language, and the "
                "argument against one is the same as in [Security policies](/docs/policies).",
                "**No anomaly tuning.** No sensitivity slider. A knob that changes how many "
                "findings you get without changing what they mean mostly gets turned down until "
                "the list is empty, at which point it has achieved nothing.",
                "**No suppression windows.** You cannot silence an alert during a deploy. The "
                "honest version of that feature needs to know when your deploys are, which Janus "
                "does not.",
            ]),
            para(
                "All four are things a mature product would grow. They are absent here rather "
                "than half-present, which is the more useful state to ship in: a notification "
                "integration that works for one channel and silently drops the others is worse "
                "than none, because people rely on it."
            ),
            para(
                "The API exposes open alerts, so wiring them into an existing alerting system is "
                "a poll and a filter away."
            ),
        ],
    },
)
