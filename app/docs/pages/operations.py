"""Operations: roles, the CLI, Caddy modules, deployment and troubleshooting."""

from __future__ import annotations

from typing import Any

from app.docs.blocks import code, heading, note, para, steps, table, terms

PAGES: tuple[dict[str, Any], ...] = (
    {
        "slug": "rbac",
        "section": "operations",
        "title": "Roles and permissions",
        "summary": "The five default roles, and why the CLI cannot get around them.",
        "blocks": [
            para(
                "Authorisation is the framework's rather than Janus's own. Roles are permission "
                "groups, permissions are rows, and one `require()` call is the choke point that "
                "the route guard, the JSON API and every CLI command reach."
            ),
            para(
                "That single choke point is the design. Two front doors — a dashboard and a CLI — "
                "is exactly the shape in which authorisation drifts, because it is so easy for "
                "one of them to grow its own idea of who may do what."
            ),

            heading("Permissions are read and write, mostly"),
            para(
                "The catalogue uses `resource.read` and `resource.write` rather than a permission "
                "per operation. A finer split was tried and discarded: `routes.create`, "
                "`routes.update`, `routes.delete` and `routes.enable` are four permissions nobody "
                "has ever wanted to hold separately, and each additional one is another thing a "
                "role can be accidentally missing."
            ),
            para(
                "Where a distinction genuinely matters it gets its own name. "
                "`configuration.rollback` is separate from `configuration.write` because "
                "deploying the current desired state and reverting to last week's configuration "
                "are different authorities — and the second usually happens under pressure."
            ),

            heading("The default roles"),
            table(
                ["Role", "Holds"],
                [
                    ["Owner", "Everything, including roles. The recovery path — it cannot be narrowed."],
                    ["Administrator", "Full operational management, including team and keys. Not roles."],
                    ["Operator", "Gateway, route, upstream and security management, plus read access to team and audit. Not rollback."],
                    ["Analyst", "Analytics and read-only gateway visibility. No configuration access at all."],
                    ["Viewer", "Read-only across the control plane."],
                ],
            ),
            note(
                "Analyst is deliberately **not** on the ladder between Viewer and Operator. It is "
                "a different axis: how much traffic data you can see, rather than how much you "
                "can change. Someone investigating a performance problem needs every analytics "
                "screen and no ability to deploy.",
                tone="info",
            ),
            para(
                "Operator holds `users.read` and `roles.read` but neither write. An operator who "
                "can read the audit log and sees `role.permissions_changed` needs to be able to "
                "look at the role it names; withholding that made the model incoherent rather "
                "than tighter."
            ),

            heading("The Owner role cannot be narrowed"),
            para(
                "Editing Owner's permissions is refused. It is the recovery path for an "
                "installation whose roles have been misconfigured, and a control plane that lets "
                "you remove your own last way back in is one bad afternoon away from needing "
                "database access to fix."
            ),
            para(
                "Separately, an account with the superuser flag holds every permission "
                "regardless of its role. That is the second escape hatch, and it is why "
                "`janus admin create` sets it on the bootstrap account."
            ),

            heading("Custom roles"),
            para(
                "You can create roles and tick permissions. The permission catalogue on the "
                "Roles screen groups them the way this documentation does, with a description "
                "for each."
            ),
            para(
                "A role starts with nothing and is edited after creation, which is deliberate: a "
                "creation form with thirty checkboxes is one people fill in carelessly, and the "
                "two-step version makes granting a permission a thing you did rather than a "
                "default you left."
            ),

            heading("The CLI is not a bypass"),
            para(
                "Every administrative command performs its work through the same JSON API the "
                "dashboard's authorisation guards. Running a command you lack permission for "
                "fails for exactly the reason clicking the button would:"
            ),
            code("""
$ janus routes delete 4 --yes
✗ You do not have permission to do that (routes.write).
""", lang="text"),
            para(
                "That is structural rather than a check inside each command. The base class for "
                "administrative commands has no database connection at all — it holds an HTTP "
                "client and a bearer token. There is no code path from `janus routes delete` to "
                "a `DELETE FROM` statement."
            ),
            para(
                "The test suite asserts it: every command outside a named list of local ones must "
                "be built on the API base class. Adding a command that opens the database is a "
                "test failure rather than a review comment somebody might miss."
            ),

            heading("The local commands, and why they are safe"),
            table(
                ["Command", "Why it is local", "Why that is not a hole"],
                [
                    ["`migrate`", "Creates schema before an API exists.", "Does nothing a signed-in user could do."],
                    ["`seed`", "Bulk-writes demo data.", "Development only; writes data that is labelled as simulated."],
                    ["`serve` / `worker` / `scheduler`", "They *are* the processes.", "No user operation involved."],
                    ["`collect`", "Reads a file and writes analytics rows.", "No user operation; the scheduler does the same on a timer."],
                    ["`admin create`", "Bootstraps the first account.", "Refuses once any account exists."],
                ],
            ),
            para(
                "`admin create` is the one that needs the argument made. Shell access already "
                "implies database access, so this is not a security boundary in the strict "
                "sense. What the refusal does is remove the *documented* path — the difference "
                "between someone determined enough to write SQL and a command in the help text."
            ),

            heading("Sessions"),
            para(
                "Both a browser session and a CLI token are `UserSession` rows, and both can be "
                "revoked from the Team screen. Revocation is checked on every request, so it "
                "takes effect on the session's next request rather than whenever its cookie "
                "would have expired."
            ),
            para(
                "Disabling a user revokes all of their sessions immediately. Leaving them live "
                "would mean a disabled account keeps working until its cookie happens to expire, "
                "which is not what \"disable\" means to the person clicking it."
            ),

            heading("Server-side, always"),
            note(
                "The interface hides controls a role cannot use. That is **cosmetic**. The gate "
                "that refuses an action is on the route, server-side, and a page that only hid "
                "the button would be a page anyone could POST to.\n\n"
                "The test suite asserts that every dashboard route carries a guard, and that no "
                "mutation is guarded only by a `.read` permission.",
                tone="caution",
            ),

            heading("Every change is audited"),
            para(
                "Role assignments, permission changes, account creation and session revocation "
                "all write audit entries with before and after values. The audit log records who "
                "acted, from which address, and through which front door — a person acting "
                "through the CLI and through the dashboard are different facts during a review."
            ),
            code("""
janus roles list
janus roles permissions Operator
janus roles create --name "Release manager" --permissions configuration.read,configuration.write,configuration.rollback
janus users role alice@example.com Operator
"""),
        ],
    },
    {
        "slug": "modules",
        "section": "operations",
        "title": "Caddy modules",
        "summary": "Which plugins your build has, what each unlocks, and how to add one.",
        "blocks": [
            para(
                "Caddy is a single static binary. Plugins are **compiled in**, not loaded at "
                "runtime — there is no plugin directory and no module path. Adding one means "
                "building a new binary."
            ),
            note(
                "This is why the Modules screen has no install button. One could be made to "
                "exist, and it would be a lie: whatever it did, the running gateway would not "
                "have the plugin until its binary was replaced and the process restarted.\n\n"
                "So Janus does the four things that are honestly possible instead.",
                tone="info",
                title="Why there is no install button",
            ),

            heading("What Janus does about modules"),
            steps([
                "**Detects** what a build provides, from `caddy list-modules --versions` and the "
                "Go module graph in `caddy build-info`.",
                "**Explains** what each missing plugin would unlock, so *should I rebuild* has an "
                "answer rather than a shrug.",
                "**Generates** the exact `xcaddy build` command, including everything already "
                "installed.",
                "**Records** what a remote build has, for the case it cannot introspect.",
            ], ordered=True),

            heading("Detection has a limit worth knowing"),
            para(
                "`caddy list-modules` runs whatever binary is on the machine Janus is on. For a "
                "single-host install, or the Docker image — which ships `caddy` alongside Janus "
                "precisely so this holds — that binary *is* the gateway's, and detection is an "
                "observation."
            ),
            para(
                "For a Caddy on another host it is not. That binary might have plugins this one "
                "lacks, or lack plugins this one has. Janus cannot tell, and Caddy's admin API "
                "does not expose a module list."
            ),
            para(
                "So a remote gateway's modules can be **declared**: run `caddy list-modules` on "
                "the gateway host and record the result. The interface labels detected and "
                "declared differently, because an assumption presented as an observation is how "
                "a dashboard starts lying."
            ),
            code("""
# on the gateway host
caddy list-modules | tr '\\n' ','

# in Janus
janus modules declare "http.handlers.rate_limit,http.authentication.providers.jwt"
janus modules declare none          # clear, and detect locally again
"""),
            note(
                "A declared build changes what Janus generates *and* how it validates. The local "
                "`caddy validate` is not a valid judge of a build you have told Janus is "
                "different — it would reject handlers the real gateway supports — so Janus falls "
                "back to structural checks and says why.",
                tone="caution",
            ),

            heading("What each plugin unlocks"),
            table(
                ["Plugin", "Module", "Without it"],
                [
                    ["Rate limiting", "`http.handlers.rate_limit`", "Limits are stored and shown but not enforced."],
                    ["JWT authentication", "`http.authentication.providers.jwt`", "A `jwt` policy checks only that a header is present."],
                    ["Coraza WAF", "`http.handlers.waf`", "No request inspection stage."],
                    ["GeoIP matching", "`http.matchers.maxmind_geolocation`", "Policies cannot match on country."],
                ],
            ),
            para(
                "The catalogue also carries plugins Janus does not use directly — caching, "
                "response rewriting, CrowdSec, DNS providers for ACME, layer-4 proxying — because "
                "they are useful at an edge even where no Janus screen is waiting on them."
            ),

            heading("The rebuild command"),
            para(
                "Select the plugins you want and Janus generates the command, in two forms — "
                "toggle between them on the Modules screen. Both include **everything already "
                "installed** as well as what you are adding."
            ),
            para(
                "**One-shot script.** `scripts/setup-caddy.sh` (in this checkout) runs on the "
                "host Caddy lives on and does the whole job: build with `xcaddy` if a Go "
                "toolchain is present, otherwise download a custom build from caddyserver.com; "
                "back the current binary up with a timestamp; swap the new one in; restart Caddy "
                "(brew services, systemd, or a bare `caddy run` — auto-detected); and redeploy. "
                "`--rollback --restart` restores the last backup."
            ),
            code("""
./scripts/setup-caddy.sh \\
    --version v2.11.4 \\
    --with github.com/caddyserver/cache-handler \\
    --with github.com/ggicci/caddy-jwt \\
    --with github.com/mholt/caddy-ratelimit \\
    --restart --redeploy
"""),
            para(
                "**Manual `xcaddy`.** The build line on its own, if you would rather drive the "
                "swap and restart yourself."
            ),
            note(
                "This is the part people get wrong. `xcaddy build` produces a fresh binary from "
                "exactly the packages named on the command line. A command listing only the new "
                "plugin silently drops every plugin the current binary has — a build that "
                "succeeds, deploys cleanly, and behaves like a downgrade.",
                tone="critical",
                title="Why the command lists plugins you already have",
            ),
            code("""
xcaddy build v2.11.4 \\
    --with github.com/caddyserver/cache-handler \\
    --with github.com/ggicci/caddy-jwt \\
    --with github.com/mholt/caddy-ratelimit
"""),
            para(
                "The Caddy version is pinned. Rebuilding is a good moment to upgrade "
                "deliberately, and a bad one to upgrade by accident."
            ),
            para(
                "If your build contains a non-standard module Janus does not recognise, it says "
                "so and warns that the generated command is incomplete for your binary. That is "
                "better than emitting a command that quietly loses it."
            ),

            heading("What a rebuild needs"),
            para(
                "`scripts/setup-caddy.sh` picks the first of these that the host can do, so in "
                "most cases you install nothing:"
            ),
            table(
                ["Route", "Needs", "When it is used"],
                [
                    ["`xcaddy` build", "`xcaddy` on `PATH`", "You already have it."],
                    ["`go install` then build", "Go 1.21+ on `PATH`", "Go is present but `xcaddy` is not — the script installs `xcaddy` itself."],
                    ["Download a custom build", "`curl`, outbound HTTPS to `caddyserver.com`", "No Go toolchain at all. caddyserver.com compiles it server-side."],
                ],
            ),
            para(
                "Plus, for every route: `bash`, write access to the target path (the script uses "
                "`sudo` automatically when `/usr/local/bin/caddy` is not writable), and — for "
                "`--redeploy` — this checkout, so it can run `janus config apply`."
            ),
            note(
                "The download route builds the same binary `xcaddy` would, without a toolchain, "
                "but it is a network call to a third-party service at build time. On an isolated "
                "host, install Go and use `xcaddy`.",
                tone="info",
            ),

            heading("Installing xcaddy and Go"),
            para("Only needed if you want to build locally rather than use the download route."),
            code("""
# macOS (Homebrew) — installs Go as a dependency
brew install xcaddy

# Any platform with Go already installed
go install github.com/caddyserver/xcaddy/cmd/xcaddy@latest
# xcaddy lands in $(go env GOPATH)/bin — add that to PATH

# Go itself: https://go.dev/dl/  (or `brew install go`, `apt install golang`)
"""),
            para(
                "`xcaddy` is a thin wrapper: it writes a tiny `main.go` that imports Caddy plus "
                "the plugins you named, and runs `go build`. Everything it produces, the "
                "download route produces too — the difference is only *where* the compile runs."
            ),

            heading("No toolchain: the download build"),
            para(
                "caddyserver.com will compile a custom binary and hand it back. This is exactly "
                "what the script's third route does; you can also call it directly:"
            ),
            code("""
curl -fSL -o caddy \\
  "https://caddyserver.com/api/download?os=darwin&arch=amd64&p=github.com/mholt/caddy-ratelimit&p=github.com/ggicci/caddy-jwt&version=v2.11.4"
chmod +x caddy
./caddy list-modules | grep -E 'rate_limit|jwt'
"""),
            para(
                "`os` is `darwin`/`linux`/`windows`, `arch` is `amd64`/`arm64`/`armv7`, each "
                "`p=` is one plugin's Go package, and `version` pins Caddy. Omit every `p=` for "
                "a plain build — which is how you *remove* the last plugin."
            ),

            heading("Installing a rebuilt binary"),
            para(
                "The one-shot script does all of this. If you are placing a binary you built or "
                "downloaded yourself:"
            ),
            steps([
                "Verify it first — never swap in a binary you have not checked: "
                "`./caddy version` and `./caddy list-modules | grep rate_limit`.",
                "Back up the current one: `sudo cp \"$(command -v caddy)\" \"$(command -v caddy).bak\"`.",
                "Replace it: `sudo install -m 0755 ./caddy \"$(command -v caddy)\"`.",
                "Restart Caddy. A restart drops in-flight connections, so do it the way you would "
                "any Caddy restart — behind a load balancer, or during a window. "
                "`brew services restart caddy`, `systemctl restart caddy`, or kill and re-run "
                "your `caddy run` process.",
                "Redeploy: `janus config apply` (or wait for the next deployment). Janus "
                "re-detects the build on the next visit to the Modules screen.",
            ], ordered=True),
            note(
                "The last step matters. Installing the rate-limit plugin does not enable your "
                "rate limits — Janus has to generate a configuration that uses it, which happens "
                "on the next deployment. Until then the limits are still recorded and not "
                "enforced. Removing a plugin is the mirror image: redeploy so Janus stops "
                "emitting a handler the new binary no longer has.",
                tone="caution",
            ),

            heading("setup-caddy.sh flags"),
            terms([
                ("`--with <pkg>`", "A plugin's Go package. Repeatable. Pass every plugin the "
                 "binary should end up with — the build replaces the binary, it does not add to it."),
                ("`--plain`", "Build or download a Caddy with no plugins. This is how you "
                 "uninstall the last one. Mutually exclusive with `--with`."),
                ("`--version <v>`", "Caddy version to build, e.g. `v2.11.4`. Defaults to the "
                 "current binary's version so a rebuild does not upgrade Caddy by accident."),
                ("`--caddy <path>`", "Target binary. Defaults to `command -v caddy`, else "
                 "`/usr/local/bin/caddy`."),
                ("`--restart`", "Restart Caddy after installing — brew services, systemd, or a "
                 "bare `caddy run` process (it finds the pid and working directory), auto-detected."),
                ("`--redeploy`", "Run `janus config apply` afterwards, falling back to a direct "
                 "deploy if the CLI is not logged in. A fresh Caddy starts with an empty config, "
                 "so this is what makes the gateway serve again."),
                ("`--rollback`", "Restore the newest `caddy.backup-*` the script wrote next to "
                 "the target. Pair with `--restart`."),
                ("`--dry-run`", "Print the build or download command and exit, touching nothing."),
                ("`--yes`", "Skip the confirmation prompt, for non-interactive use. Combine with "
                 "`sudo -v` first so the password prompt does not block it."),
            ]),
            code("""
# add a plugin, keeping everything already there, and go live
./scripts/setup-caddy.sh --with github.com/mholt/caddy-ratelimit --restart --redeploy

# remove every plugin — a stock Caddy
./scripts/setup-caddy.sh --plain --restart --redeploy

# undo the last rebuild
./scripts/setup-caddy.sh --rollback --restart
"""),
            note(
                "The script runs on the host Caddy lives on. For a co-located install that is "
                "the Janus host; for a remote gateway, run it there (it needs a checkout), or "
                "use the manual steps and `janus modules declare`.",
                tone="info",
            ),

            heading("Removing a plugin"),
            para(
                "There is no uninstall for a compiled-in plugin — you rebuild without it. On the "
                "Modules screen, every installed plugin has a **Remove** button; it stages the "
                "removal and regenerates the command with that plugin left out (or `--plain` if "
                "it was the last one). Then run the command, restart, and redeploy."
            ),
            para(
                "If you have just rebuilt and want to go back, `--rollback --restart` is faster "
                "and exact — it restores the binary the script backed up rather than building a "
                "third one."
            ),

            heading("Plugins Janus will not configure for you"),
            para(
                "Several catalogue entries carry a caveat, and it is the same shape each time: "
                "the plugin needs configuration Janus does not generate."
            ),
            terms([
                ("JWT", "Needs your signing key or a JWKS endpoint. Janus holds no signing keys."),
                ("Coraza WAF", "Needs the OWASP Core Rule Set downloaded and tuned. An untuned "
                 "WAF in blocking mode refuses legitimate traffic on its first day."),
                ("GeoIP", "Needs a MaxMind database file on the gateway host."),
                ("CrowdSec", "Needs a running CrowdSec local API to pull decisions from."),
                ("DNS providers", "Need an API token with permission to edit the zone."),
                ("Log formats", "Janus ingests Caddy's JSON access log. Changing the Janus "
                 "logger's encoder stops ingestion — use a second, separate logger."),
            ]),
            para(
                "These are shown on the screen next to each plugin rather than buried here, "
                "because installing one and finding out afterwards that it needs a database file "
                "is a bad afternoon."
            ),

            heading("Verifying a rebuild"),
            para(
                "Before replacing anything, check that the binary you built has what you expected:"
            ),
            code("""
./caddy list-modules --versions | grep -i rate_limit
./caddy version
./caddy build-info | head -5
"""),
            para(
                "`list-modules --versions` separates standard modules from non-standard ones and "
                "prints a count of each, so a plugin that failed to compile in is visible rather "
                "than something you discover when a deployment is rejected."
            ),
            para(
                "`build-info` prints the Go module graph. Janus reads it too, which is how it "
                "recognises a plugin that registers module ids the catalogue does not know about."
            ),

            heading("Keeping a build reproducible"),
            para(
                "`xcaddy build` with no version argument builds from the latest Caddy and the "
                "latest of every plugin, which means two builds a week apart are two different "
                "binaries. Janus pins the Caddy version in the command it generates for that "
                "reason."
            ),
            para(
                "Plugins can be pinned too, with `@` and a version or a commit:"
            ),
            code("""
xcaddy build v2.11.4 \\
    --with github.com/mholt/caddy-ratelimit@v0.1.0 \\
    --with github.com/ggicci/caddy-jwt@v3.0.1
"""),
            para(
                "For anything that matters, put the build in a Dockerfile or a CI job rather than "
                "running it by hand. A binary somebody built on their laptop is one nobody can "
                "reproduce when it needs a security patch."
            ),

            heading("Building the Janus image with plugins"),
            para(
                "The shipped Dockerfile installs stock Caddy for validation. To build an image "
                "whose Caddy has plugins, add an `xcaddy` stage:"
            ),
            code("""
FROM caddy:2-builder AS caddy-build
RUN xcaddy build \\
    --with github.com/mholt/caddy-ratelimit \\
    --with github.com/ggicci/caddy-jwt

FROM caddy:2-alpine
COPY --from=caddy-build /usr/bin/caddy /usr/bin/caddy
""", lang="dockerfile"),
            note(
                "If Janus and Caddy are separate images — which the Compose setup makes them — "
                "the *Janus* image also needs the plugin-enabled binary for validation and "
                "detection to be accurate. Otherwise Janus will detect a core-only build and "
                "decline to emit handlers your gateway actually supports.\n\n"
                "The alternative is `janus modules declare`, which tells Janus what the gateway "
                "has without needing the binary locally.",
                tone="caution",
            ),

            heading("How detection actually works"),
            table(
                ["Source", "Command", "Gives"],
                [
                    ["Module list", "`caddy list-modules --versions`", "Module ids, versions, and the standard/non-standard split"],
                    ["Package graph", "`caddy build-info`", "Go module paths compiled in"],
                ],
            ),
            para(
                "The two are combined because either alone has a gap. A plugin might register no "
                "module id the catalogue recognises, in which case the package path identifies "
                "it; and a module id might be present from a plugin whose package name has "
                "changed."
            ),
            para(
                "Where neither is available — no binary, nothing declared — Janus assumes core "
                "modules only. That is the conservative direction: omitting a handler produces a "
                "clearly reported *not enforced* warning, while emitting one the build lacks "
                "produces a rejected deployment that blocks every other change in it."
            ),

            heading("Adding a plugin Janus does not know about"),
            para(
                "The catalogue is curated rather than exhaustive — a directory of every Caddy "
                "plugin would be a directory, and a directory is not a decision aid. If you build "
                "with something outside it, Janus notices."
            ),
            para(
                "Non-standard modules that the catalogue does not describe are listed as "
                "unrecognised, with a warning that the generated `xcaddy` command is incomplete "
                "for your binary. Copying that command as-is would drop them."
            ),
            para(
                "Janus will not generate configuration that uses an unrecognised plugin, because "
                "it has no idea what that plugin's handler expects. Configuring it is a matter of "
                "a second server object in Caddy that Janus does not own — which the co-tenancy "
                "model supports precisely so that this is possible."
            ),
        ],
    },
    {
        "slug": "cli",
        "section": "operations",
        "title": "CLI reference",
        "summary": "Every command, the safety rules they share, and how to script them.",
        "blocks": [
            para(
                "The CLI covers everything the dashboard does, plus the local operations a "
                "dashboard cannot perform. It is built on the framework's console — the command "
                "class, the parameter declaration, the table renderer, the prompts and the exit "
                "codes are all `sillo.console`'s. Janus supplies the commands and nothing else."
            ),
            para(
                "The design constraint that shaped it is stated in "
                "[Roles and permissions](/docs/rbac) and worth repeating here: **the CLI is not "
                "a second way into the database.** Every administrative command is an HTTP "
                "client with a bearer token, calling the same API the dashboard calls. There is "
                "no code path from `janus routes delete` to a `DELETE FROM` statement."
            ),

            heading("Signing in"),
            code("""
janus login                 # prompts for email and password
janus whoami
janus logout
"""),
            para(
                "`login` stores a bearer token at `~/.config/janus/credentials.json`, mode 0600, "
                "in a directory that is 0700. A token granting full authority over a gateway "
                "estate should not be world-readable in a shared home directory, and the default "
                "umask does not guarantee that."
            ),
            para(
                "`logout` revokes the token server-side *and then* deletes it locally. Deleting "
                "the file alone would leave a working token in the database until it expired, "
                "which on a shared machine is the difference between logging out and appearing "
                "to."
            ),
            para("An unauthenticated administrative command answers:"),
            code("""
Authentication required.
Run: janus login
""", lang="text"),

            heading("Pointing at an installation"),
            table(
                ["Mechanism", "Scope", "Use for"],
                [
                    ["`--url` on `login`", "Stored with the token", "The installation you usually work with."],
                    ["`JANUS_URL`", "One shell, or one command", "A one-off against somewhere else."],
                    ["`JANUS_TOKEN`", "One shell, or one command", "CI, where there is no interactive login."],
                    ["`JANUS_HOME`", "Which credentials file is used", "Keeping separate tokens for staging and production."],
                ],
            ),
            para(
                "`JANUS_HOME` is the one worth knowing about. Two shells with different values "
                "hold different sessions, so you can have staging in one terminal and production "
                "in another without either forgetting who it is."
            ),
            code("""
JANUS_HOME=~/.janus/staging janus login --url https://janus.staging.example.com
JANUS_HOME=~/.janus/prod    janus login --url https://janus.example.com

JANUS_HOME=~/.janus/staging janus config apply --yes
"""),
            note(
                "`janus whoami` prints which installation the stored token points at. It is the "
                "first thing to run when a command fails with an authentication error that makes "
                "no sense — a staging token against production fails as *unauthenticated* rather "
                "than as *wrong installation*, which is confusing until you know to look.",
                tone="info",
            ),

            heading("Gateway"),
            code("""
janus gateways list
janus gateways show edge
janus gateways status edge
janus gateways create --name "Edge" --listen :443 --admin-url http://10.0.0.5:2019
janus gateways delete edge --yes
"""),
            para(
                "Most commands take `--gateway <slug|id>`. Without it they act on the first "
                "enabled gateway, which is the single-gateway case and the common one. With "
                "several gateways, pass it explicitly — a script that relies on which gateway "
                "happens to be first is a script waiting to act on the wrong edge."
            ),

            heading("Routes"),
            code("""
janus routes list --gateway edge
janus routes show 4

janus routes create --name Orders --path '/api/orders*' \\
    --methods GET,POST --upstream orders-service \\
    --priority 100 --auth api_key --strip-prefix /api

janus routes update 4 --priority 120 --timeout 45
janus routes enable 4
janus routes disable 4
janus routes delete 4 --yes
"""),
            note(
                "Quote path patterns. A shell expands `/api/orders*` against the filesystem "
                "before Janus ever sees it, and on a machine where that matches nothing, zsh "
                "fails the whole command with `no matches found` — which looks like a Janus "
                "error and is not.",
                tone="caution",
            ),

            heading("Upstreams"),
            code("""
janus upstreams list
janus upstreams show orders-service

janus upstreams create --name orders-service \\
    --targets 10.0.1.11:8000:3,10.0.1.12:8000:2 \\
    --health-path /healthz --policy weighted_round_robin

janus upstreams health
janus upstreams delete 4 --yes
"""),
            para(
                "Targets are `host:port:weight`, comma-separated. The weight is optional and "
                "defaults to 1. The parser splits from the right, so an IPv6 address with colons "
                "in it is not mangled into a weight."
            ),
            para(
                "`upstreams health` refreshes from Caddy's own health checkers and prints the "
                "result. It is the fastest answer to *is this backend actually out of rotation, "
                "or does it only look that way*."
            ),

            heading("Configuration"),
            code("""
janus config show           # the full generated payload
janus config validate       # without deploying
janus config diff           # against what is live
janus config apply --note "add reports route"
janus config apply --yes --force
janus config history
janus config rollback 14 --yes
"""),
            para(
                "`config diff` prints a unified diff with additions and removals coloured. It is "
                "the command to run before `apply`, and the one to run first when a gateway is "
                "DRIFTED."
            ),
            para(
                "`--force` deploys when nothing has changed. You need it in exactly one "
                "situation: a drifted gateway, where the desired state is correct and the gateway "
                "is not."
            ),

            heading("Security"),
            code("""
janus security block 203.0.113.42 --reason "probing /.env" --minutes 360
janus security block 203.0.113.0/24 --reason "scanning range"
janus security unblock 203.0.113.42
janus security allow 10.0.0.0/8 --reason "internal"
janus security blocklist
janus security allowlist

janus ratelimit list
janus ratelimit create --name "Per key" --key api_key --limit 1000 --window 60 --burst 100
janus ratelimit update 4 --limit 2000
janus ratelimit delete 4 --yes

janus keys list
janus keys create --name "Acme production" --scopes products.read --expires-in-days 365
janus keys rotate 4
janus keys revoke 4 --reason "leaked" --yes
"""),
            note(
                "Security commands write desired state. `janus security block` records the rule; "
                "`janus config apply` is what makes the gateway enforce it. During an active "
                "incident that is two commands, and forgetting the second is the most likely way "
                "to believe you have blocked something you have not.",
                tone="critical",
            ),

            heading("Team"),
            code("""
janus users list
janus users create --email alice@example.com --role Operator
janus users role alice@example.com Administrator
janus users disable alice@example.com --reason "on leave"
janus users enable alice@example.com
janus users delete alice@example.com --yes

janus roles list
janus roles permissions Operator
janus roles create --name "Release manager" \\
    --permissions configuration.read,configuration.write,configuration.rollback
janus roles assign "Release manager" alice@example.com

janus admin create          # bootstrap only; refuses once an account exists
"""),

            heading("Analytics"),
            code("""
janus analytics overview --range 24h
janus analytics routes --range 1h
janus analytics problematic --range 24h
janus analytics performance
janus analytics errors
janus analytics clients
janus analytics traffic
"""),
            para(
                "Ranges are `15m`, `1h`, `6h`, `24h`, `7d` and `30d`. `analytics problematic` is "
                "the one to reach for first — it is the same ranking the dashboard leads with, "
                "and it prints the component scores so you can see why something is at the top."
            ),

            heading("Caddy modules"),
            code("""
janus modules list
janus modules build ratelimit,jwt                 # setup-caddy.sh line (default)
janus modules build ratelimit,jwt --mode xcaddy   # plain xcaddy line
janus modules build - --remove ratelimit          # rebuild without a plugin
janus modules declare "http.handlers.rate_limit,http.authentication.providers.jwt"
janus modules declare none
"""),
            para(
                "`modules build` prints the command; it does not run it. The one-shot rebuild "
                "itself is `scripts/setup-caddy.sh` — see [Caddy modules](/docs/modules)."
            ),

            heading("Local commands"),
            code("""
janus migrate               # schema, columns, permission catalogue
janus seed --hours 48       # demo estate
janus work                  # every scheduled job, once
janus collect               # ingest access logs now
janus serve --host 0.0.0.0 --port 8000
janus worker --queues collector,health,gateway,analytics
janus scheduler
"""),
            para(
                "These are the only commands that open the database directly. They do nothing a "
                "signed-in user could do instead, which is the test for whether a command belongs "
                "in this list."
            ),

            heading("Rules every command shares"),
            terms([
                ("`--json` everywhere",
                 "Including on errors. A script that set `--json` and then has to parse a human "
                 "sentence out of stderr is a script that will not."),
                ("Destructive commands confirm",
                 "And name what they are about to destroy: *Delete route POST /api/orders?* "
                 "rather than *Delete route 4192?*, because the second is not a question anyone "
                 "can answer. That means an extra fetch before the prompt, which is worth it."),
                ("`--yes` for automation",
                 "Skips the prompt deliberately."),
                ("Refuses rather than assumes",
                 "In a non-interactive shell without `--yes`, a destructive command refuses "
                 "rather than proceeding. A pipeline that meant to delete a route can say so; "
                 "one that did not should not find out in production."),
                ("Passwords are prompted, never flags",
                 "`--password hunter2` ends up in shell history and in the process list, where "
                 "any other user on the machine can read it with `ps`."),
            ]),

            heading("Exit codes"),
            table(
                ["Code", "Means"],
                [
                    ["0", "Success."],
                    ["1", "The command failed — permission denied, not found, validation failed, or cancelled."],
                    ["2", "Usage error: an unknown command or a bad argument."],
                    ["130", "A prompt was cancelled with Ctrl-C."],
                ],
            ),

            heading("Scripting"),
            para(
                "`config validate` and `config apply` exit non-zero when the configuration is "
                "invalid or the deployment failed, which makes them usable as a CI gate:"
            ),
            code("""
export JANUS_URL=https://janus.example.com
export JANUS_TOKEN="$JANUS_DEPLOY_TOKEN"

janus config validate --json > validation.json || {
  echo "Configuration rejected:"
  jq -r .error validation.json
  exit 1
}

janus config apply --yes --note "deploy $GIT_SHA" --json > deploy.json || {
  echo "Deployment failed at stage: $(jq -r .stage deploy.json)"
  jq -r .error deploy.json
  exit 1
}
"""),
            para(
                "The token for a pipeline should belong to a service account with the narrowest "
                "role that works. A deploy pipeline needs `configuration.read` and "
                "`configuration.write`; it almost certainly does not need `configuration.rollback`, "
                "and it definitely does not need `users.write`."
            ),
            para("Reading analytics from a script:"),
            code("""
# Fail a nightly check if any route is CRITICAL
critical=$(janus analytics problematic --range 24h --json \\
  | jq '[.routes[] | select(.status == "CRITICAL")] | length')

[ "$critical" -gt 0 ] && echo "$critical route(s) critical" && exit 1
"""),

            heading("Command names are space-separated"),
            para(
                "`janus routes list`, not `janus routes:list`. The framework's console resolves "
                "the first token as the command name; Janus extends resolution to join the "
                "leading tokens, longest match first, and hands everything else back to the "
                "framework. A name that matches nothing falls through to the framework's own "
                "'unknown command' handling, suggestion included."
            ),
        ],
    },
    {
        "slug": "deployment",
        "section": "operations",
        "title": "Deploying Janus",
        "summary": "Processes, settings, and what the production guard refuses.",
        "blocks": [
            heading("The processes"),
            table(
                ["Process", "Does", "How many"],
                [
                    ["`janus serve`", "Dashboard and API", "As many as you like"],
                    ["`janus scheduler`", "Ingestion, health, drift, alerts, pruning", "Exactly one"],
                    ["`janus worker`", "Drains the job queues", "As many as you like"],
                ],
            ),
            note(
                "The scheduler is singular. Its jobs are not concurrency-safe in the way that "
                "matters: two schedulers ingesting the same access log would each advance their "
                "own byte offset and double-count everything between them.",
                tone="caution",
            ),
            note(
                "Only the web process should own the schema. Creating tables concurrently races "
                "on the implicit row type behind `CREATE TABLE IF NOT EXISTS`, and the error "
                "names nothing about the race that caused it. Workers and the scheduler run with "
                "`DB_GENERATE_SCHEMAS=false`.",
                tone="caution",
            ),

            heading("With Docker"),
            code("docker compose up --build"),
            para(
                "Brings up Caddy, Postgres, Redis, Janus, a worker and a scheduler. No "
                "Kubernetes, no Kafka, no Elasticsearch — a control plane and an analytics store "
                "need neither, and every component you add is one that can fail."
            ),
            para(
                "The one thing worth understanding is the shared log volume. Caddy writes its "
                "access log to it and the Janus containers read from it. That is the entire "
                "analytics pipeline: no broker, no agent, no sidecar. Across hosts, mount the "
                "same network volume or run a collector beside each gateway."
            ),

            heading("Settings that matter"),
            table(
                ["Variable", "Why"],
                [
                    ["`SECRET_KEY`", "Signs sessions and CSRF tokens. Janus refuses to boot with the development default."],
                    ["`DATABASE_URL`", "Postgres in production. SQLite is a laptop convenience and will not survive concurrent writers."],
                    ["`CADDY_ADMIN_URL`", "Where the gateway's admin API is. Never expose it publicly."],
                    ["`CADDY_ACCESS_LOG`", "Where Caddy writes and Janus reads. Must be the same path from both sides."],
                    ["`COOKIE_SECURE`", "On behind HTTPS. A `Secure` cookie on plain HTTP is accepted by the browser and then never sent back, which reads as \"login silently does nothing\"."],
                    ["`CADDY_SIMULATE`", "Off by default. On only where there is genuinely no Caddy."],
                    ["`QUEUE_BACKEND`", "`redis` for more than one process."],
                ],
            ),
            para("The full list is in `.env.example` and `app/config.py`."),

            heading("The production guard"),
            para(
                "With `APP_ENV=production`, Janus refuses to start if the secret key is the "
                "development default, if debug is on, if cookies are not secure, or if the Caddy "
                "simulator is enabled."
            ),
            para(
                "It refuses rather than warning. Every one of those is a way for a production "
                "control plane to be quietly insecure, and a log line at startup is not where "
                "anyone will see it — it scrolls past during a deploy and is never read again."
            ),

            heading("Upgrading"),
            steps([
                "Stop the scheduler and workers. The web process can keep serving.",
                "`janus migrate` — creates missing tables *and* reconciles missing columns.",
                "Deploy the new web process.",
                "Start the scheduler and workers again.",
            ], ordered=True),
            note(
                "Step two matters more than it looks. Creating a schema only creates missing "
                "*tables*; a column added to an existing model would otherwise be silently "
                "absent until a query failed at runtime with `no such column`.",
                tone="caution",
            ),
            para(
                "Gateway configuration is unaffected by a Janus upgrade. Caddy keeps serving the "
                "last deployed version throughout, which is the property that makes upgrading "
                "Janus a low-stakes operation."
            ),

            heading("Backups"),
            para(
                "The database holds everything: gateways, routes, rules, keys, the audit log and "
                "the analytics. Back it up the way you back up any Postgres."
            ),
            para(
                "Analytics are the bulk of the volume and the least valuable per row. If backup "
                "size is a problem, `request_logs` and `route_rollups` are the tables to exclude "
                "— losing them costs you history, not the ability to run the gateway."
            ),
            para(
                "What is genuinely irreplaceable is the configuration history. Those rows are the "
                "record of what ran and when, and they are small."
            ),

            heading("Sizing"),
            table(
                ["Gateway traffic", "Request rows/day", "Rollup rows/day"],
                [
                    ["10 req/s", "~864,000", "~14,000"],
                    ["100 req/s", "~8.6M", "~14,000"],
                    ["1,000 req/s", "~86M", "~14,000"],
                ],
            ),
            para(
                "Rollups are flat because they are per route per minute regardless of volume — "
                "which is the whole reason they exist. If request rows are too many, lower "
                "`REQUEST_RETENTION_HOURS`; the charts are unaffected because they read rollups."
            ),

            heading("A minimal single-host deployment"),
            para(
                "The smallest thing that is not a toy: one machine, Caddy and Janus as system "
                "services, Postgres alongside, and no container runtime."
            ),
            code("""
# /etc/systemd/system/janus.service
[Service]
ExecStart=/opt/janus/janus serve --host 127.0.0.1 --port 8000
EnvironmentFile=/etc/janus/env
Restart=always

# /etc/systemd/system/janus-scheduler.service
[Service]
ExecStart=/opt/janus/janus scheduler
EnvironmentFile=/etc/janus/env
Restart=always
""", lang="ini"),
            para(
                "Janus binds to loopback and Caddy proxies to it — which means the control plane "
                "is behind the gateway it controls. That is a pleasing arrangement and has one "
                "sharp edge: a configuration that breaks the route to Janus locks you out of the "
                "interface. The CLI over SSH is the way back in, which is a reason to keep it "
                "working."
            ),
            note(
                "If you do put Janus behind its own gateway, keep a route to it that no ordinary "
                "deployment touches — a dedicated port, or a server object outside the "
                "Janus-owned one. Co-tenancy exists partly for this.",
                tone="caution",
            ),

            heading("Where the access log has to be visible"),
            para(
                "The collector reads a file. Caddy writes it. Both need to agree on the path, and "
                "the Janus process needs read access."
            ),
            table(
                ["Arrangement", "How"],
                [
                    ["Same host", "Any path both can reach. The default under `storage/` is fine."],
                    ["Separate containers", "A shared volume, mounted read-only into Janus."],
                    ["Separate hosts", "A network volume, or run a collector process beside each gateway."],
                ],
            ),
            para(
                "There is no push mechanism and no agent. If the file is not reachable, ingestion "
                "reports that it cannot find it rather than failing silently — but nothing else "
                "will tell you, because the gateway carries on serving perfectly."
            ),

            heading("Scaling the web process"),
            para(
                "The web process is stateless apart from sessions, which live in signed cookies. "
                "Run as many as you like behind a load balancer."
            ),
            para(
                "Every replica needs the same `SECRET_KEY`, or a session issued by one will not "
                "validate against another and users will be logged out at random as they are "
                "balanced between them."
            ),
            para(
                "With more than one process, use Redis for the queue. The in-memory backend is "
                "per process, so a job dispatched by one web replica would never be seen by a "
                "worker in another."
            ),

            heading("Health checks and readiness"),
            para(
                "`/health` touches the database rather than only confirming the event loop is "
                "running. A container answering HTTP with a dead database should not be receiving "
                "traffic, and a check that only proves the process is alive would happily let it."
            ),
            code("""
$ curl -s localhost:8000/health
{"app":"Janus","env":"production","database":"ok","queue":"redis","caddy":"http://caddy:2019"}
""", lang="text"),
            para(
                "It answers 503 when the database is unreachable, and includes any production "
                "configuration warnings — though in production those prevent startup rather than "
                "appearing here."
            ),

            heading("Logging"),
            para(
                "Janus logs to stdout in the usual way for a Python service. It does not log "
                "request bodies, and the one response containing a secret — API key creation — is "
                "not logged."
            ),
            para(
                "Caddy's own logs are separate and are where gateway problems appear: "
                "certificate provisioning, upstream dial failures, configuration load errors. "
                "When a deployment fails at `applying`, Caddy's log has the full context and "
                "Janus has the summary."
            ),

            heading("Disaster recovery"),
            para(
                "The property worth understanding is that **Caddy holds its own configuration**. "
                "Losing Janus entirely does not stop traffic, and does not require restoring "
                "Janus before the gateway works."
            ),
            steps([
                "Restore the database from backup.",
                "Run `janus migrate`.",
                "Compare: `janus config diff`. If the restore predates the last deployment, the "
                "gateway is running something newer than the database knows about, and Janus will "
                "report DRIFTED.",
                "Decide deliberately — redeploy the restored state over the gateway, or adopt "
                "what the gateway is running by recreating it in Janus.",
            ], ordered=True),
            note(
                "Step three is why drift detection reports rather than corrects. A control plane "
                "that automatically pushed a restored-from-backup configuration over a running "
                "gateway would turn a Janus outage into a traffic incident.",
                tone="critical",
            ),

            heading("Security checklist"),
            steps([
                "`SECRET_KEY` set, unique, and not in version control.",
                "`COOKIE_SECURE=true` and Janus reached over HTTPS.",
                "Caddy's admin API on loopback or a private network, never public.",
                "Postgres not reachable from outside its network.",
                "The first account created with `janus admin create`, then that command is inert.",
                "Service accounts for CI hold the narrowest role that works.",
                "`CADDY_SIMULATE` off — the production guard enforces this.",
            ]),
        ],
    },
    {
        "slug": "troubleshooting",
        "section": "operations",
        "title": "Troubleshooting",
        "summary": "The failure modes that look like something else.",
        "blocks": [
            para(
                "Ordered by how often they happen and how misleading they are, rather than by "
                "severity."
            ),

            heading("The gateway answers 403 to everything"),
            para(
                "You almost certainly have a **global allow rule**. Any allow rule at a scope "
                "makes that scope deny-by-default, so one global allow for your office network "
                "refuses every other caller."
            ),
            code("janus security allowlist"),
            para(
                "Scope the rule to a route or a domain, or remove it. See "
                "[Allowlists and blocklists](/docs/access-control)."
            ),

            heading("Plain HTTP returns 400 about HTTPS"),
            para(
                "`Client sent an HTTP request to an HTTPS server`. Caddy enabled automatic HTTPS "
                "because your routes carry host matchers."
            ),
            para(
                "Set the domains' TLS mode to `off` if the gateway should speak HTTP, or reach it "
                "over https. See [Domains and TLS](/docs/domains)."
            ),

            heading("A local request is refused but a remote one is not"),
            para(
                "Your allowlist has `127.0.0.0/8` and your browser is arriving as `::1`. Add the "
                "IPv6 loopback."
            ),

            heading("A rate limit is defined but nothing is limited"),
            para(
                "Your Caddy build has no rate-limit module. The Rate Limits screen says so, and "
                "so does every deployment. See [Caddy modules](/docs/modules)."
            ),
            code("""
janus modules list
# add it, in one command, on the host Caddy runs on:
./scripts/setup-caddy.sh --with github.com/mholt/caddy-ratelimit --restart --redeploy
"""),

            heading("Analytics show no traffic"),
            steps([
                "Is the gateway serving at all? Check Health, and try a request directly.",
                "Is Caddy writing where Janus reads? Compare the Settings screen with the "
                "deployed configuration — `janus config show` includes the logger path.",
                "Has the collector run? `janus collect` runs it immediately; under the scheduler "
                "it runs every fifteen seconds.",
                "Is the scheduler running at all? Without it nothing ingests.",
            ], ordered=True),

            heading("A gateway reports traffic it never served"),
            para(
                "Two gateways are reading the same access log. The collector keeps a byte offset "
                "per gateway, so a shared file is ingested once per gateway and everything is "
                "counted twice."
            ),
            para("Give each gateway its own path — the default includes the gateway's slug."),

            heading("A deployment failed"),
            para(
                "The stage tells you where you stand, and it is the first thing to read:"
            ),
            table(
                ["Stage", "What happened", "Gateway state"],
                [
                    ["`validating`", "The configuration was rejected before being sent.", "Untouched, serving the previous version."],
                    ["`applying`", "Caddy refused it.", "Serving the previous version — Caddy loads atomically."],
                    ["`verifying`", "Caddy accepted it but is not running it.", "Uncertain. Marked DRIFTED; check the diff."],
                ],
            ),
            para("The Deployments screen carries the error verbatim from Caddy."),

            heading("The gateway is DRIFTED"),
            para(
                "Caddy is not running what Janus last deployed. Someone edited it, reloaded it "
                "from a file, or restarted it against a different configuration."
            ),
            code("janus config diff"),
            para(
                "Then either redeploy over it — with `--force`, because the desired state has not "
                "changed — or change the desired state to match and deploy that."
            ),

            heading("Routes return 502"),
            para(
                "The upstream targets are not reachable from the Caddy host. Note *from the Caddy "
                "host* — an address that resolves on your laptop may not resolve inside a "
                "container."
            ),
            code("janus upstreams health"),

            heading("A route returns 503 with no upstream error"),
            para(
                "The upstream has no enabled targets, or is marked as draining. Janus emits a "
                "static 503 in that case and warns at generation time."
            ),

            heading("`no such column` after upgrading"),
            para(
                "Run `janus migrate`. Creating a schema only creates missing *tables*; a column "
                "added to an existing model needs reconciling."
            ),

            heading("Login redirects straight back to the login page"),
            para(
                "Usually `COOKIE_SECURE=true` on a plain-HTTP deployment. The browser accepts the "
                "cookie and never sends it back, so the next request is unauthenticated and the "
                "guard redirects."
            ),

            heading("The CLI cannot reach Janus"),
            para(
                "Check `JANUS_URL`, or `janus whoami` to see which installation the stored token "
                "points at. A token from staging against production fails with an "
                "authentication error rather than a connection one, which is confusing until you "
                "know to look."
            ),

            heading("Everything is slow"),
            para(
                "Analytics queries scale with the number of rollup rows in the range. A 30-day "
                "window across many routes is a lot of rows."
            ),
            para(
                "If the dashboard is slow and the gateway is not, the problem is Janus and not "
                "your edge — which is the useful thing about keeping the control plane out of "
                "the request path. Narrow the range, and check that pruning is running."
            ),

            heading("A deployment succeeded but nothing changed"),
            para(
                "Check you deployed the gateway you meant to. Most commands act on the first "
                "enabled gateway when `--gateway` is not given, and on an estate with several "
                "that is not always the one on screen."
            ),
            code("janus gateways list"),

            heading("Rate limits stopped working after a Caddy upgrade"),
            para(
                "A rebuilt binary from an `xcaddy` command that named only the new plugin drops "
                "every plugin the previous build had. `janus modules list` will show them missing."
            ),
            para(
                "Rebuild using the command Janus generates, which includes everything already "
                "present — that is precisely what it is for. `scripts/setup-caddy.sh` also "
                "defaults `--version` to the current binary's, so a plugin rebuild does not "
                "upgrade Caddy as a side effect."
            ),

            heading("Analytics stop the moment Caddy rotates its log"),
            para(
                "The collector tracks the file's inode alongside its byte offset and resets when "
                "the inode changes. If ingestion stops at a rotation, the likely cause is that "
                "rotation is happening by *copy and truncate* rather than by rename — the inode "
                "stays the same while the content is replaced."
            ),
            para(
                "Caddy's own `roll` option renames, which is handled. An external logrotate with "
                "`copytruncate` is not."
            ),

            heading("The interface is empty but the API works"),
            para(
                "The frontend did not mount. Open the browser console; a missing asset or a "
                "failed module import will be there."
            ),
            para(
                "In development this usually means the Vite dev server is not running. In "
                "production it means the assets were not built, or `VITE_DEV` is still true and "
                "the page is pointing at a dev server that is not there."
            ),
            code("""
npm run build
VITE_DEV=false janus serve
"""),

            heading("Everything works locally and nothing works in Docker"),
            para(
                "Usually an address that resolves differently inside a container. `localhost` "
                "inside the Janus container is the Janus container, not your machine and not "
                "Caddy."
            ),
            table(
                ["Setting", "Wrong in Compose", "Right"],
                [
                    ["`CADDY_ADMIN_URL`", "`http://localhost:2019`", "`http://caddy:2019`"],
                    ["`DATABASE_URL`", "`...@localhost:5432/...`", "`...@postgres:5432/...`"],
                    ["Upstream targets", "`127.0.0.1:9000`", "The service name, or a routable address"],
                ],
            ),
            para(
                "Upstream targets are dialled by **Caddy**, not by Janus, so they have to resolve "
                "from the Caddy container's network position."
            ),

            heading("An upstream shows healthy in Janus but requests fail"),
            para(
                "Health checks and real requests can disagree. An active check on `/healthz` "
                "against a handler that returns 200 unconditionally proves the process is "
                "listening and nothing else."
            ),
            para(
                "Look at the route's status distribution rather than the health indicator. A wall "
                "of 500s from a backend whose health check passes means the check is not "
                "exercising what the traffic exercises."
            ),

            heading("A CLI command works for one person and not another"),
            para("Almost always permissions. Compare:"),
            code("janus whoami"),
            para(
                "The refusal names the missing permission, which is what to grant — or what the "
                "role deliberately does not include. `configuration.rollback` is the usual one: "
                "Operator does not hold it."
            ),

            heading("Getting more detail"),
            code("""
janus config validate --json
janus gateways status --json
janus modules list --json
janus analytics overview --range 1h --json
"""),
            para(
                "`--json` on any command gives the full structure the dashboard receives, which "
                "usually contains more than the table prints — including error text passed "
                "through verbatim from Caddy."
            ),
            para(
                "For gateway-side problems, Caddy's own log is the authority. Janus reports what "
                "Caddy told it; Caddy's log has the rest."
            ),
        ],
    },
)
