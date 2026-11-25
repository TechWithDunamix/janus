"""``janus tui`` — the terminal console for watching and operating Janus gateways.

Everything goes through the same API, login and permissions as the ``janus`` commands; the TUI arranges it
on one screen. Tabs: Overview, Requests (live), Routes, Upstreams, Domains, Security, Policies, Config,
Audit. Changes are saved as desired state and deployed with ``D`` (after a look at the diff on the Config
tab); maintenance mode deploys at once.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any, ClassVar

from sillo.console import Option
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Static,
    TabbedContent,
    TabPane,
)

from cli.base import ApiCommand, cell
from cli.client import ApiClient, CliError

#: Seconds between automatic refreshes of the visible tab.
REFRESH_SECONDS = 2.0

Columns = list[tuple[str, str]]


# --------------------------------------------------------------------------
# dialogs
# --------------------------------------------------------------------------


class Confirm(ModalScreen[bool]):
    """Ask yes or no before something that cannot be undone."""

    BINDINGS: ClassVar = [Binding("y", "answer(True)", "Yes"), Binding("n,escape", "answer(False)", "No")]

    def __init__(self, question: str) -> None:
        super().__init__()
        self.question = question

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self.question, id="question")
            with Horizontal(id="buttons"):
                yield Button("Yes  (y)", variant="error", id="yes")
                yield Button("No  (n)", variant="primary", id="no")

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def action_answer(self, value: bool) -> None:
        self.dismiss(value)


class Prompt(ModalScreen[str | None]):
    """Ask for one line of text."""

    BINDINGS: ClassVar = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, question: str, placeholder: str = "", value: str = "") -> None:
        super().__init__()
        self.question, self.placeholder, self.value = question, placeholder, value

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self.question, id="question")
            yield Input(value=self.value, placeholder=self.placeholder, id="answer")
            yield Label("Enter to confirm · Esc to cancel", classes="hint")

    @on(Input.Submitted)
    def submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())

    def action_cancel(self) -> None:
        self.dismiss(None)


class Details(ModalScreen[None]):
    """A read-only page of text."""

    BINDINGS: ClassVar = [Binding("escape,q", "app.pop_screen", "Close")]

    def __init__(self, title: str, body: str) -> None:
        super().__init__()
        self.heading, self.body = title, body

    def compose(self) -> ComposeResult:
        with Vertical(id="details"):
            yield Label(self.heading, id="details-title")
            with VerticalScroll():
                yield Static(self.body, id="details-body", markup=False)
            yield Label("Esc to close", classes="hint")


def describe(item: dict[str, Any], skip: tuple[str, ...] = ()) -> str:
    """``label  value`` lines for an object, nested values as indented JSON."""
    width = max((len(k) for k in item if k not in skip), default=0)
    lines = []
    for key, value in item.items():
        if key in skip:
            continue
        if isinstance(value, (dict, list)) and value:
            text = json.dumps(value, indent=2, default=str).replace("\n", "\n" + " " * (width + 2))
        else:
            text = cell(value, wide=True)
        lines.append(f"{key.replace('_', ' '):<{width}}  {text}")
    return "\n".join(lines)


def parse_filter(text: str, allowed: tuple[str, ...], default: str) -> dict[str, str]:
    """``status:5xx path:/api`` → ``{"status": "5xx", "path": "/api"}``; loose words go to ``default``."""
    params: dict[str, str] = {}
    loose = []
    for word in text.split():
        name, sep, value = word.partition(":")
        if sep and name in allowed and value:
            params[name] = value
        else:
            loose.append(word)
    if loose:
        params[default] = " ".join(loose)
    return params


# --------------------------------------------------------------------------
# a tab that shows one list
# --------------------------------------------------------------------------


class ListView(Vertical):
    """A table of rows fetched from the API, refreshed while visible."""

    def __init__(self, fetch: Callable[[ApiClient, str | None, str], list[dict]], columns: Columns, *,
                 key: str = "id", filter_hint: str = "", empty: str = "Nothing here yet.", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.fetch, self.columns, self.key, self.empty = fetch, columns, key, empty
        self.filter_hint = filter_hint
        self.rows: dict[str, dict] = {}

    def compose(self) -> ComposeResult:
        if self.filter_hint:
            yield Input(placeholder=self.filter_hint, classes="filter")
        yield DataTable(cursor_type="row", zebra_stripes=True)
        yield Label("", classes="status")

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns(*(heading for heading, _ in self.columns))

    @property
    def filter_text(self) -> str:
        found = self.query(Input)
        return found.first().value.strip() if found else ""

    @on(Input.Submitted, ".filter")
    def filtered(self) -> None:
        self.app.refresh_now()

    def show(self, rows: list[dict], note: str = "") -> None:
        """Replace the rows, keeping the cursor on the same row when it is still there."""
        table = self.query_one(DataTable)
        selected = self.selected_key()
        self.rows = {str(row.get(self.key)): row for row in rows}
        table.clear()
        for row in rows:
            table.add_row(*(cell(row.get(field)) for _, field in self.columns), key=str(row.get(self.key)))
        if selected in self.rows:
            table.move_cursor(row=table.get_row_index(selected))
        self.query_one(".status", Label).update(note or (f"{len(rows)} shown" if rows else self.empty))

    def selected_key(self) -> str | None:
        table = self.query_one(DataTable)
        if not table.row_count:
            return None
        try:
            return str(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value)
        except Exception:  # noqa: BLE001 - the table emptied under the cursor
            return None

    def selected(self) -> dict | None:
        key = self.selected_key()
        return self.rows.get(key) if key else None


# --------------------------------------------------------------------------
# what each tab shows
# --------------------------------------------------------------------------


def fetch_requests(api: ApiClient, gateway: str | None, text: str) -> list[dict]:
    params = parse_filter(text, ("status", "method", "client", "host"), "contains")
    return api.get("/api/requests", gateway=gateway, limit=200, **params)["requests"]


def fetch_routes(api: ApiClient, gateway: str | None, _text: str) -> list[dict]:
    return api.get("/api/routes", gateway=gateway)["routes"]


def fetch_upstreams(api: ApiClient, gateway: str | None, _text: str) -> list[dict]:
    rows = api.get("/api/upstreams", gateway=gateway)["upstreams"]
    for row in rows:
        row["target_list"] = ", ".join(
            f"{t['dial']} w{t['weight']}" + ("" if t.get("healthy", True) else " DOWN") for t in row["targets"])
    return rows


def fetch_domains(api: ApiClient, gateway: str | None, _text: str) -> list[dict]:
    return api.get("/api/domains", gateway=gateway)["domains"]


def fetch_rules(api: ApiClient, gateway: str | None, _text: str) -> list[dict]:
    return api.get("/api/security/rules", gateway=gateway)["rules"]


def fetch_clients(api: ApiClient, _gateway: str | None, text: str) -> list[dict]:
    params = parse_filter(text, ("status",), "q")
    return api.get("/api/security/clients", limit=200, **params)["clients"]


def fetch_policies(api: ApiClient, gateway: str | None, _text: str) -> list[dict]:
    return api.get("/api/policies", gateway=gateway)["policies"]


def fetch_history(api: ApiClient, gateway: str | None, _text: str) -> list[dict]:
    return api.get("/api/config/history", gateway=gateway)["versions"]


def fetch_audit(api: ApiClient, _gateway: str | None, _text: str) -> list[dict]:
    return api.get("/api/audit")["events"]


def percent(value: Any) -> str:
    return f"{value:.2%}" if isinstance(value, (int, float)) else "-"


def overview_text(data: dict) -> str:
    gw, t, h = data["gateway"], data["traffic"], data["health"]
    state = "MAINTENANCE — every request gets the maintenance page" if gw["maintenance"] else "serving"
    lines = [
        f"gateway      {gw['name']} ({gw['slug']}) · {state}",
        f"caddy        {'reachable' if h.get('reachable') else 'NOT REACHABLE'} · sync {h.get('sync_state', '-')}"
        + (f" · {h['sync_error']}" if h.get("sync_error") else ""),
        f"upstreams    {h.get('targets_total', 0)} targets · {h.get('targets_unhealthy', 0)} unhealthy",
        "",
        f"traffic      {t.get('requests', 0)} requests · {str(data['range'].get('label', '24h')).lower()} · "
        f"{t.get('rps', 0)} rps"
        + (" (includes demo data)" if t.get("simulated") else ""),
        f"errors       {percent(t.get('error_rate'))} · {t.get('errors_4xx', 0)} 4xx · {t.get('errors_5xx', 0)} 5xx",
        f"latency      avg {t.get('avg_latency_ms', 0)} ms · p95 {t.get('p95', '-')} ms · max {t.get('max_latency_ms', 0)} ms",
        f"edge         {t.get('blocked', 0)} blocked · {t.get('rate_limited', 0)} rate limited · "
        f"{t.get('timeouts', 0)} timeouts",
        f"routes       {t.get('active_routes', 0)}/{t.get('total_routes', 0)} enabled · "
        f"{t.get('active_upstreams', 0)} upstreams",
    ]
    return "\n".join(lines)


REQUEST_COLUMNS: Columns = [("TIME", "at"), ("METHOD", "method"), ("STATUS", "status"), ("MS", "latency_ms"),
                            ("HOST", "host"), ("PATH", "path"), ("CLIENT", "client_ip"), ("EDGE", "edge_action")]


# --------------------------------------------------------------------------
# the app
# --------------------------------------------------------------------------


class JanusTui(App[None]):
    """Tabs over the Janus API, refreshed every few seconds."""

    TITLE = "Janus"
    CSS = """
    Screen { background: $surface; }
    #overview-body { padding: 1 2; height: auto; }
    #diff-body { padding: 0 1; }
    .split { height: 1fr; }
    .split > Vertical, .split > ListView { width: 1fr; }
    .section { padding: 0 1; color: $text-muted; text-style: bold; }
    .filter { margin: 0 0 1 0; }
    .status, .hint { color: $text-muted; padding: 0 1; }
    DataTable { height: 1fr; }
    #diff { height: 1fr; border: round $primary-darken-2; }
    Confirm, Prompt, Details { align: center middle; }
    #dialog { width: 76; height: auto; padding: 1 2; border: thick $primary; background: $panel; }
    #question { width: 100%; margin-bottom: 1; }
    #buttons { height: auto; }
    #buttons Button { margin-right: 2; }
    #details { width: 90%; height: 85%; padding: 1 2; border: thick $primary; background: $panel; }
    #details-title { text-style: bold; margin-bottom: 1; }
    """
    BINDINGS: ClassVar = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh_now", "Refresh"),
        Binding("g", "next_gateway", "Gateway"),
        Binding("slash", "focus_filter", "Filter"),
        Binding("enter", "open", "Open", show=False),
        Binding("t", "toggle", "On/off"),
        Binding("x", "remove", "Delete/remove"),
        Binding("b", "block", "Block"),
        Binding("D", "deploy", "Deploy"),
        Binding("M", "maintenance", "Maintenance"),
        Binding("l", "toggle_live", "Live"),
    ]

    def __init__(self, api: ApiClient | None = None, gateway: str | None = None) -> None:
        super().__init__()
        self.api = api or ApiClient()
        self.gateway = gateway
        self.gateways: list[dict] = []
        self.live = True
        self.views: dict[str, ListView] = {}

    # ---- layout ----------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(id="tabs"):
            with TabPane("Overview", id="overview"):
                yield Static("Loading…", id="overview-body")
                with Horizontal(classes="split"):
                    yield ListView(lambda *_: [], [("OPENED", "opened_at"), ("SEVERITY", "severity"),
                                                   ("ALERT", "title")], id="alerts-view", empty="No open alerts.")
                    yield ListView(lambda *_: [], [("STARTED", "started_at"), ("V", "version"),
                                                   ("STATUS", "status"), ("BY", "actor"), ("SUMMARY", "summary")],
                                   id="deploys-view", empty="No deployments yet.")
            with TabPane("Requests", id="requests"):
                yield ListView(fetch_requests, REQUEST_COLUMNS, id="requests-view", empty="No requests match.",
                               filter_hint="Filter: /api  status:5xx  status:404  method:POST  client:1.2.3.4  (Enter)")
            with TabPane("Routes", id="routes"):
                yield ListView(fetch_routes,
                               [("ID", "id"), ("NAME", "name"), ("METHOD", "method"), ("PATH", "path"),
                                ("DOMAIN", "domain"), ("UPSTREAM", "upstream"), ("AUTH", "auth_policy"),
                                ("ENABLED", "enabled"), ("PRIORITY", "priority")],
                               id="routes-view", empty="No routes. Create one: janus routes create")
            with TabPane("Upstreams", id="upstreams"):
                yield ListView(fetch_upstreams,
                               [("ID", "id"), ("NAME", "name"), ("POLICY", "policy"), ("TARGETS", "target_list"),
                                ("HEALTH PATH", "health_path"), ("ENABLED", "enabled"),
                                ("MAINTENANCE", "maintenance")],
                               id="upstreams-view", empty="No upstreams. Create one: janus upstreams create")
            with TabPane("Domains", id="domains"):
                yield ListView(fetch_domains,
                               [("ID", "id"), ("HOSTNAME", "hostname"), ("TLS", "tls_mode"),
                                ("TLS STATUS", "tls_status"), ("EXPIRES", "tls_expires_at"),
                                ("ENABLED", "enabled"), ("ROUTES", "routes")],
                               id="domains-view", empty="No domains. Add one: janus domains create HOSTNAME")
            with TabPane("Security", id="security"):
                with Horizontal(classes="split"):
                    with Vertical():
                        yield Label("ALLOW / BLOCK RULES   b block an address · x remove", classes="section")
                        yield ListView(fetch_rules,
                                       [("ACTION", "action"), ("CIDR", "cidr"), ("REASON", "reason"),
                                        ("EXPIRES", "expires_at")],
                                       id="rules-view", empty="No rules.")
                    with Vertical():
                        yield Label("CLIENTS   Enter open · b block this client", classes="section")
                        yield ListView(fetch_clients,
                                       [("CLIENT", "identifier"), ("STATUS", "status"),
                                        ("REQUESTS", "request_count"), ("ERRORS", "error_count"),
                                        ("BLOCKED", "blocked_count"), ("LAST SEEN", "last_seen_at")],
                                       id="clients-view", empty="No clients seen yet.",
                                       filter_hint="Search an address · status:blocked  (Enter)")
            with TabPane("Policies", id="policies"):
                yield ListView(fetch_policies,
                               [("ID", "id"), ("NAME", "name"), ("RULE", "sentence"), ("PRIORITY", "priority"),
                                ("SCOPE", "scope"), ("ENABLED", "enabled"), ("MATCHES", "match_count")],
                               id="policies-view", empty="No policies. Add one: janus policies create")
            with TabPane("Config", id="config"):
                yield Label("PENDING CHANGES   D deploy", classes="section")
                with VerticalScroll(id="diff"):
                    yield Static("Loading…", id="diff-body", markup=False)
                yield Label("HISTORY", classes="section")
                yield ListView(fetch_history,
                               [("VERSION", "version"), ("STATUS", "status"), ("CREATED", "created_at"),
                                ("BY", "author"), ("ORIGIN", "origin"), ("ROUTES", "routes"),
                                ("SUMMARY", "summary"), ("ERROR", "error")],
                               key="version", id="history-view", empty="Nothing deployed yet.")
            with TabPane("Audit", id="audit"):
                yield ListView(fetch_audit,
                               [("AT", "at"), ("ACTOR", "actor"), ("ACTION", "action"), ("RESOURCE", "resource"),
                                ("LABEL", "label"), ("ORIGIN", "origin")],
                               key="at", id="audit-view", empty="No audit events.")
        yield Footer()

    def on_mount(self) -> None:
        self.views = {view.id: view for view in self.query(ListView) if view.id}
        self.load_gateways()
        self.refresh_now()
        self.set_interval(REFRESH_SECONDS, self._tick)

    def _subtitle(self) -> str:
        name = next((g["name"] for g in self.gateways if str(g.get("slug")) == str(self.gateway)), None)
        live = "live" if self.live else "paused"
        return f"{self.api.url} · {name or self.gateway or 'default gateway'} · {live}"

    @work(thread=True, group="gateways")
    def load_gateways(self) -> None:
        try:
            gateways = self.api.get("/api/gateways")["gateways"]
        except CliError as exc:
            self.call_from_thread(self.notify, str(exc).splitlines()[0], severity="error", timeout=8)
            return

        def apply() -> None:
            self.gateways = gateways
            if self.gateway is None and gateways:
                first = next((g for g in gateways if g.get("enabled", True)), gateways[0])
                self.gateway = str(first["slug"])
            self.sub_title = self._subtitle()

        self.call_from_thread(apply)

    # ---- refreshing ------------------------------------------------------

    @property
    def tab(self) -> str:
        return self.query_one(TabbedContent).active or "overview"

    def _tick(self) -> None:
        if self.live and not isinstance(self.screen, ModalScreen):
            self.refresh_now()

    def refresh_now(self) -> None:
        self.load(self.tab, self.gateway, {vid: v.filter_text for vid, v in self.views.items()})

    @on(TabbedContent.TabActivated)
    def tab_changed(self) -> None:
        self.refresh_now()

    @work(thread=True, exclusive=True, group="load")
    def load(self, tab: str, gateway: str | None, filters: dict[str, str]) -> None:
        """Fetch what the visible tab shows (in a thread: the client is synchronous)."""
        try:
            if tab == "overview":
                data = self.api.get("/api/overview", gateway=gateway)
                self.call_from_thread(self._show_overview, data)
                return
            if tab == "config":
                diff = self.api.get("/api/config/diff", gateway=gateway)
                self.call_from_thread(self._show_diff, diff)
            names = {"security": ("rules-view", "clients-view"), "config": ("history-view",)}.get(
                tab, (f"{tab}-view",))
            for name in names:
                view = self.views[name]
                rows = view.fetch(self.api, gateway, filters.get(name, ""))
                self.call_from_thread(view.show, rows)
        except CliError as exc:
            self.call_from_thread(self.notify, str(exc).splitlines()[0], severity="error", timeout=6)

    def _show_overview(self, data: dict) -> None:
        self.query_one("#overview-body", Static).update(overview_text(data))
        self.views["alerts-view"].show(data["alerts"])
        self.views["deploys-view"].show(data["deployments"])

    def _show_diff(self, data: dict) -> None:
        if data["has_changes"]:
            text = "\n".join(data["diff"])
        else:
            text = f"Nothing to deploy: the gateway runs version {data.get('active_version') or '-'}."
        self.query_one("#diff-body", Static).update(text)

    # ---- actions ---------------------------------------------------------

    def current(self) -> ListView | None:
        """The list the cursor is in, or the tab's main list."""
        focused = self.focused
        while focused is not None and not isinstance(focused, ListView):
            focused = focused.parent
        if isinstance(focused, ListView):
            return focused
        default = {"security": "rules-view", "config": "history-view"}.get(self.tab, f"{self.tab}-view")
        return self.views.get(default)

    def action_refresh_now(self) -> None:
        self.refresh_now()

    def action_toggle_live(self) -> None:
        self.live = not self.live
        self.sub_title = self._subtitle()
        self.notify("Live refresh on." if self.live else "Paused. Press l to resume, r to refresh once.")

    def action_next_gateway(self) -> None:
        if not self.gateways:
            self.notify("No gateways yet.")
            return
        slugs = [str(g["slug"]) for g in self.gateways]
        index = slugs.index(self.gateway) if self.gateway in slugs else -1
        self.gateway = slugs[(index + 1) % len(slugs)]
        self.sub_title = self._subtitle()
        self.refresh_now()

    def action_focus_filter(self) -> None:
        view = self.current()
        if view is not None and view.query(Input):
            view.query_one(Input).focus()

    @on(DataTable.RowSelected)
    def row_chosen(self) -> None:
        self.action_open()

    def action_open(self) -> None:
        view = self.current()
        row = view.selected() if view else None
        if row is None or view is None:
            return
        if view.id == "clients-view":
            self.open_client(row["id"])
        else:
            title = row.get("name") or row.get("hostname") or row.get("path") or str(row.get(view.key))
            self.push_screen(Details(str(title), describe(row)))

    @work(thread=True, group="open")
    def open_client(self, client_id: int) -> None:
        try:
            data = self.api.get(f"/api/security/clients/{client_id}", gateway=self.gateway)
        except CliError as exc:
            self.call_from_thread(self.notify, str(exc).splitlines()[0], severity="error")
            return
        body = describe(data["client"])
        body += "\n\nrules\n" + ("\n".join(f"  {r['action']:<6} {r['cidr']}  {cell(r['reason'])}"
                                           for r in data["rules"]) or "  none")
        body += "\n\nstatus codes\n" + ("\n".join(f"  {s['status']}  {s['count']}" for s in data["by_status"])
                                        or "  none in this window")
        body += "\n\nlatest requests\n" + "\n".join(
            f"  {cell(r['at'])}  {r['method']:<6} {r['status']}  {r['latency_ms']} ms  {r['path']}"
            for r in data["recent"])
        self.call_from_thread(self.push_screen, Details(data["client"]["identifier"], body))

    def _ask(self, question: str, method: str, path: str, body: Any, done: str) -> None:
        self.push_screen(Confirm(question),
                         lambda yes: yes and self.call_api(method, path, body, done))

    def action_toggle(self) -> None:
        view = self.current()
        row = view.selected() if view else None
        if view is None or row is None:
            return
        on = not row.get("enabled")
        word = "Enable" if on else "Disable"
        targets = {
            "routes-view": ("post", f"/api/routes/{row['id']}/toggle", {"enabled": on}, row.get("name")),
            "domains-view": ("patch", f"/api/domains/{row['id']}", {"enabled": on}, row.get("hostname")),
            "policies-view": ("patch", f"/api/policies/{row['id']}", {"enabled": on}, row.get("name")),
            "upstreams-view": ("patch", f"/api/upstreams/{row['id']}", {"enabled": on}, row.get("name")),
        }
        if view.id not in targets:
            self.notify("t switches routes, upstreams, domains and policies on or off.")
            return
        method, path, body, label = targets[view.id]
        self._ask(f"{word} {label}? It takes effect at the next deploy (D).", method, path, body,
                  f"{label} {'enabled' if on else 'disabled'}. Press D to deploy.")

    def action_remove(self) -> None:
        view = self.current()
        row = view.selected() if view else None
        if view is None or row is None:
            return
        if view.id == "routes-view":
            self._ask(f"Delete route {row['name']}?", "delete", f"/api/routes/{row['id']}", None,
                      "Route deleted. Press D to deploy.")
        elif view.id == "domains-view":
            self._ask(f"Delete domain {row['hostname']}?", "delete", f"/api/domains/{row['id']}", None,
                      "Domain deleted. Press D to deploy.")
        elif view.id == "policies-view":
            self._ask(f"Delete policy {row['name']}?", "delete", f"/api/policies/{row['id']}", None,
                      "Policy deleted. Press D to deploy.")
        elif view.id == "rules-view":
            self._ask(f"Remove the {row['action']} rule for {row['cidr']}?", "post", "/api/security/unblock",
                      {"cidr": row["cidr"]}, "Rule removed. Press D to deploy.")
        else:
            self.notify("x deletes routes, domains, policies and allow/block rules.")

    def action_block(self) -> None:
        view = self.current()
        row = view.selected() if view else None
        preset = row["identifier"] if view is not None and view.id == "clients-view" and row else ""

        def typed(text: str | None) -> None:
            if not text:
                return
            address, _, reason = text.partition(" ")
            self.call_api("post", "/api/security/block", {"cidr": address, "reason": reason or "blocked from janus tui"},
                          f"Blocked {address}. Press D to deploy.")

        self.push_screen(Prompt("Block an address or range (and a reason)", "203.0.113.7 scraping", preset), typed)

    def action_deploy(self) -> None:
        self.push_screen(Confirm("Deploy the pending configuration to the gateway now?"),
                         lambda yes: yes and self.deploy())

    @work(thread=True, group="act")
    def deploy(self) -> None:
        try:
            result = self.api.post("/api/config/apply", {"note": "deployed from janus tui"}, gateway=self.gateway)
        except CliError as exc:
            self.call_from_thread(self.push_screen, Details("Deploy failed", str(exc)))
            return
        if result.get("unchanged"):
            text = "Nothing to deploy: the gateway already runs this configuration."
        else:
            version = (result.get("version") or {}).get("version") if isinstance(result.get("version"), dict) else \
                result.get("version")
            text = f"Deployed version {version}." if result.get("ok") else f"Deploy stopped: {result.get('error')}"
        self.call_from_thread(self.notify, text, severity="information" if result.get("ok") else "error", timeout=8)
        self.call_from_thread(self.refresh_now)

    def action_maintenance(self) -> None:
        gateway = next((g for g in self.gateways if str(g.get("slug")) == str(self.gateway)), None)
        on = not (gateway or {}).get("maintenance", False)
        question = ("Put the whole gateway into maintenance? Every request gets the maintenance page, deployed now."
                    if on else "Take the gateway out of maintenance and deploy now?")
        self.push_screen(Confirm(question), lambda yes: yes and self.maintenance(on))

    @work(thread=True, group="act")
    def maintenance(self, on: bool) -> None:
        try:
            self.api.post("/api/gateways/maintenance", {"on": on}, gateway=self.gateway)
        except CliError as exc:
            self.call_from_thread(self.notify, str(exc).splitlines()[0], severity="error", timeout=8)
            return
        self.call_from_thread(self.notify, "Maintenance is on." if on else "Maintenance is off.")
        self.load_gateways()
        self.call_from_thread(self.refresh_now)

    @work(thread=True, group="act")
    def call_api(self, method: str, path: str, body: Any, done: str) -> None:
        try:
            if method in ("post", "patch"):
                getattr(self.api, method)(path, body, gateway=self.gateway)
            else:
                getattr(self.api, method)(path, gateway=self.gateway)
        except CliError as exc:
            self.call_from_thread(self.notify, str(exc).splitlines()[0], severity="error", timeout=6)
            return
        self.call_from_thread(self.notify, done)
        self.call_from_thread(self.refresh_now)


class Tui(ApiCommand):
    """``janus tui [--gateway G]`` — the interactive console (sign in with ``janus login`` first).

    An :class:`ApiCommand` like the rest: everything it shows or changes goes through the API and its
    permissions.
    """

    name = "tui"
    help = "Open the interactive terminal console: live requests, routes, upstreams, security, deploys."
    arguments = [Option("gateway", help="Gateway slug or id to start on (g switches).")]

    def run(self, api: ApiClient) -> int:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            print("janus tui needs an interactive terminal; use the janus commands in scripts.", file=sys.stderr)
            return 2
        api.get("/api/auth/whoami")  # fail here, with the usual message, when not signed in
        JanusTui(api, self.option("gateway") or None).run()
        return 0
