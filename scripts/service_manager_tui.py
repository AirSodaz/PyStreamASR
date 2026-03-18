"""Textual UI for PyStreamASR service management."""

from __future__ import annotations

import asyncio

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    RichLog,
    Select,
    Static,
    TabbedContent,
    TabPane,
)

from scripts.service_manager import (
    DEFAULT_LOG_LINES,
    MAX_LOG_LINES,
    DiagnosticResult,
    LogSource,
    ServiceController,
    ServiceStatus,
    format_status,
)


class ServiceManagerApp(App[None]):
    """Full-screen Textual UI for service operations, logs, and diagnostics."""

    CSS = """
    Screen {
        layout: vertical;
    }

    #status-view {
        height: 16;
        border: solid $panel;
        padding: 1 2;
        overflow: auto;
    }

    #dashboard-actions, #config-actions, #log-controls, #diag-controls {
        height: auto;
        margin: 1 0;
    }

    Input {
        width: 24;
        margin-right: 1;
    }

    Select {
        width: 52;
        margin-right: 1;
    }

    #message-banner {
        height: 3;
        border: solid $primary;
        padding: 0 1;
    }

    #logs-view, #diag-details {
        height: 1fr;
        border: solid $panel;
    }

    #diag-table {
        height: 14;
        margin-bottom: 1;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh_status", "Refresh Status"),
        Binding("s", "start_service", "Start"),
        Binding("x", "stop_service", "Stop"),
        Binding("R", "restart_service", "Restart"),
        Binding("tab", "next_tab", "Next Tab"),
        Binding("shift+tab", "previous_tab", "Previous Tab"),
    ]

    def __init__(self, controller: ServiceController) -> None:
        """Store controller and initialize UI state."""
        super().__init__()
        self.controller = controller
        self._auto_log_refresh = False
        self._log_sources: list[LogSource] = self.controller.list_log_sources()
        self._log_timer = None
        self._last_status: ServiceStatus | None = None

    def compose(self) -> ComposeResult:
        """Create the Textual layout."""
        yield Header(show_clock=True)
        with TabbedContent(id="main-tabs", initial="dashboard"):
            with TabPane("Dashboard", id="dashboard"):
                yield Static("Loading service status...", id="status-view")
                with Horizontal(id="dashboard-actions"):
                    yield Button("Refresh", id="action-refresh", variant="primary")
                    yield Button("Start", id="action-start")
                    yield Button("Stop", id="action-stop")
                    yield Button("Restart", id="action-restart")
                with Horizontal(id="config-actions"):
                    yield Input(placeholder="Host", id="input-host")
                    yield Button("Apply Host", id="apply-host")
                    yield Input(placeholder="Port", id="input-port")
                    yield Button("Apply Port", id="apply-port")
                    yield Input(placeholder="Workers", id="input-workers")
                    yield Button("Apply Workers", id="apply-workers")
            with TabPane("Logs", id="logs"):
                with Horizontal(id="log-controls"):
                    yield Select(self._build_log_select_options(), id="log-source-select")
                    yield Input(value=str(DEFAULT_LOG_LINES), id="log-lines-input")
                    yield Button("Refresh Logs", id="log-refresh", variant="primary")
                    yield Button("Auto Refresh: Off", id="log-auto")
                yield RichLog(id="logs-view", highlight=False, markup=False, wrap=False)
            with TabPane("Troubleshooting", id="troubleshooting"):
                with Horizontal(id="diag-controls"):
                    yield Button("Run Diagnostics", id="diag-run", variant="primary")
                yield DataTable(id="diag-table")
                yield RichLog(id="diag-details", highlight=False, markup=False, wrap=True)
        yield Static("Ready.", id="message-banner")
        yield Footer()

    def on_mount(self) -> None:
        """Initialize table columns and schedule first data refresh."""
        table = self.query_one("#diag-table", DataTable)
        table.add_columns("Check", "Status", "Summary")
        self._log_timer = self.set_interval(3.0, self._on_log_refresh_timer, pause=False)
        self._schedule_status_refresh()
        self._schedule_logs_refresh()
        self._set_banner(
            f"Service manager ready for {self.controller.get_backend_display_name()} "
            f"({self.controller.get_runtime_display_name()})."
        )

    def _build_log_select_options(self) -> list[tuple[str, str]]:
        """Build Select options from current log sources."""
        options: list[tuple[str, str]] = []
        for source in self._log_sources:
            suffix = "" if source.available else " (unavailable)"
            options.append((f"{source.label}{suffix}", source.source_id))
        if not options:
            options.append(("No log sources detected", ""))
        return options

    def _set_banner(self, message: str) -> None:
        """Update the bottom message banner."""
        self.query_one("#message-banner", Static).update(message)

    def _set_status_view(self, status: ServiceStatus) -> None:
        """Render status in the dashboard."""
        self.query_one("#status-view", Static).update("\n".join(format_status(status)))
        self.query_one("#input-host", Input).value = status.configured_host
        self.query_one("#input-port", Input).value = str(status.configured_port)
        self.query_one("#input-workers", Input).value = str(status.configured_workers)

    def _on_log_refresh_timer(self) -> None:
        """Refresh logs on interval when auto-refresh is enabled."""
        if self._auto_log_refresh:
            self._schedule_logs_refresh()

    def _schedule_status_refresh(self) -> None:
        """Start a background refresh for service status."""
        self.run_worker(self._refresh_status_worker(), group="status", exclusive=True)

    async def _refresh_status_worker(self) -> None:
        """Fetch status without blocking UI loop."""
        try:
            status = await asyncio.to_thread(self.controller.get_service_status)
        except Exception as exc:
            self._set_banner(f"Failed to refresh status: {exc}")
            return
        self._last_status = status
        self._set_status_view(status)

    def _selected_log_source(self) -> str:
        """Return selected log-source id."""
        selected_value = self.query_one("#log-source-select", Select).value
        if selected_value is None:
            return ""
        return str(selected_value)

    def _log_line_count(self) -> int | None:
        """Validate and normalize requested log line count."""
        raw_value = self.query_one("#log-lines-input", Input).value.strip()
        if not raw_value:
            return DEFAULT_LOG_LINES
        try:
            parsed = int(raw_value)
        except ValueError:
            self._set_banner("Log lines must be an integer.")
            return None
        if parsed < 1 or parsed > MAX_LOG_LINES:
            self._set_banner(f"Log lines must be between 1 and {MAX_LOG_LINES}.")
            return None
        return parsed

    def _schedule_logs_refresh(self) -> None:
        """Start a background refresh for selected log source."""
        self.run_worker(self._refresh_logs_worker(), group="logs", exclusive=True)

    async def _refresh_logs_worker(self) -> None:
        """Fetch logs without blocking UI loop."""
        self._log_sources = await asyncio.to_thread(self.controller.list_log_sources)
        selected_source = self._selected_log_source()
        lines = self._log_line_count()
        if lines is None:
            return

        if not selected_source and self._log_sources:
            selected_source = self._log_sources[0].source_id

        content = await asyncio.to_thread(self.controller.read_log_source, selected_source, lines)
        self._render_log_content(content)

    def _render_log_content(self, content: str) -> None:
        """Write log content into the log panel."""
        log_view = self.query_one("#logs-view", RichLog)
        log_view.clear()
        if not content.strip():
            log_view.write("(no output)")
            return
        for line in content.splitlines():
            log_view.write(line)

    def _schedule_diagnostics(self) -> None:
        """Start a background diagnostics run."""
        self.run_worker(self._run_diagnostics_worker(), group="diagnostics", exclusive=True)

    async def _run_diagnostics_worker(self) -> None:
        """Run diagnostics and render results."""
        try:
            results = await asyncio.to_thread(self.controller.run_diagnostics)
        except Exception as exc:
            self._set_banner(f"Diagnostics failed: {exc}")
            return

        self._render_diagnostics(results)

    def _render_diagnostics(self, results: list[DiagnosticResult]) -> None:
        """Render troubleshooting results in table + details view."""
        table = self.query_one("#diag-table", DataTable)
        table.clear(columns=True)
        table.add_columns("Check", "Status", "Summary")

        details = self.query_one("#diag-details", RichLog)
        details.clear()

        fail_count = 0
        warn_count = 0

        for result in results:
            status_text = result.status.upper()
            table.add_row(result.check_name, status_text, result.summary)
            details.write(f"[{status_text}] {result.check_name}")
            details.write(f"Summary: {result.summary}")
            details.write(f"Detail: {result.detail}")
            details.write(f"Remediation: {result.remediation}")
            details.write("")
            if result.status == "fail":
                fail_count += 1
            elif result.status == "warn":
                warn_count += 1

        self._set_banner(
            f"Diagnostics complete. failures={fail_count}, warnings={warn_count}, total={len(results)}."
        )

    def _schedule_service_action(self, action: str) -> None:
        """Schedule a service action without blocking UI."""
        self.run_worker(self._run_service_action_worker(action), group="service-action", exclusive=True)

    async def _run_service_action_worker(self, action: str) -> None:
        """Execute service action and refresh dashboard state."""
        actions = {
            "start": self.controller.start_service,
            "stop": self.controller.stop_service,
            "restart": self.controller.restart_service,
        }
        handler = actions.get(action)
        if handler is None:
            self._set_banner(f"Unsupported action: {action}")
            return

        message = await asyncio.to_thread(handler)
        self._set_banner(message)
        self._schedule_status_refresh()

    def _schedule_config_update(self, key: str, raw_value: str) -> None:
        """Schedule an APP_* update."""
        self.run_worker(
            self._run_config_update_worker(key, raw_value),
            group="config",
            exclusive=True,
        )

    async def _run_config_update_worker(self, key: str, raw_value: str) -> None:
        """Apply a config update in background and refresh status."""
        handlers = {
            "host": self.controller.update_host,
            "port": self.controller.update_port,
            "workers": self.controller.update_workers,
        }
        handler = handlers.get(key)
        if handler is None:
            self._set_banner(f"Unsupported config key: {key}")
            return

        try:
            message = await asyncio.to_thread(handler, raw_value)
        except ValueError as exc:
            self._set_banner(str(exc))
            return
        self._set_banner(message)
        self._schedule_status_refresh()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button commands."""
        button_id = event.button.id or ""
        if button_id == "action-refresh":
            self._schedule_status_refresh()
            return
        if button_id == "action-start":
            self._schedule_service_action("start")
            return
        if button_id == "action-stop":
            self._schedule_service_action("stop")
            return
        if button_id == "action-restart":
            self._schedule_service_action("restart")
            return
        if button_id == "apply-host":
            self._schedule_config_update("host", self.query_one("#input-host", Input).value)
            return
        if button_id == "apply-port":
            self._schedule_config_update("port", self.query_one("#input-port", Input).value)
            return
        if button_id == "apply-workers":
            self._schedule_config_update("workers", self.query_one("#input-workers", Input).value)
            return
        if button_id == "log-refresh":
            self._schedule_logs_refresh()
            return
        if button_id == "log-auto":
            self._auto_log_refresh = not self._auto_log_refresh
            event.button.label = "Auto Refresh: On" if self._auto_log_refresh else "Auto Refresh: Off"
            self._set_banner(
                "Log auto refresh enabled."
                if self._auto_log_refresh
                else "Log auto refresh disabled."
            )
            return
        if button_id == "diag-run":
            self._schedule_diagnostics()
            return

    def on_select_changed(self, event: Select.Changed) -> None:
        """Refresh logs when source selection changes."""
        if event.select.id == "log-source-select":
            self._schedule_logs_refresh()

    def action_refresh_status(self) -> None:
        """Binding: refresh dashboard status."""
        self._schedule_status_refresh()

    def action_start_service(self) -> None:
        """Binding: start service."""
        self._schedule_service_action("start")

    def action_stop_service(self) -> None:
        """Binding: stop service."""
        self._schedule_service_action("stop")

    def action_restart_service(self) -> None:
        """Binding: restart service."""
        self._schedule_service_action("restart")

    def _cycle_tab(self, direction: int) -> None:
        """Move active tab forward/backward."""
        tabs = self.query_one("#main-tabs", TabbedContent)
        pane_ids = [pane.id for pane in tabs.query(TabPane) if pane.id]
        if not pane_ids:
            return
        active = tabs.active or pane_ids[0]
        if active not in pane_ids:
            tabs.active = pane_ids[0]
            return
        current_idx = pane_ids.index(active)
        tabs.active = pane_ids[(current_idx + direction) % len(pane_ids)]

    def action_next_tab(self) -> None:
        """Binding: move to next tab."""
        self._cycle_tab(1)

    def action_previous_tab(self) -> None:
        """Binding: move to previous tab."""
        self._cycle_tab(-1)

