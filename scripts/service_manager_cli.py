"""Hierarchical terminal menu for PyStreamASR service management."""

from __future__ import annotations

import os
from collections.abc import Callable

from scripts.service_manager import (
    DEFAULT_LOG_LINES,
    MAX_LOG_LINES,
    DiagnosticResult,
    LogSource,
    ServiceController,
    format_status,
)

InputFunc = Callable[[str], str]
OutputFunc = Callable[[str], None]
ClearScreenFunc = Callable[[], None]


class ServiceManagerCliApp:
    """Interactive layered menu for service operations and troubleshooting."""

    def __init__(
        self,
        controller: ServiceController,
        input_func: InputFunc = input,
        output_func: OutputFunc = print,
        clear_screen_func: ClearScreenFunc | None = None,
    ) -> None:
        """Initialize CLI dependencies.

        Args:
            controller: Service operations facade.
            input_func: Input callback for user prompts.
            output_func: Output callback for rendered lines.
            clear_screen_func: Optional clear-screen callback.
        """
        self.controller = controller
        self._input = input_func
        self._output = output_func
        self._clear_screen = clear_screen_func or self._default_clear_screen

    def run(self) -> None:
        """Run the layered terminal menu loop."""
        while True:
            self._show_main_menu()
            choice = self._read_choice().lower()
            if choice == "0":
                self._output("Exiting service manager.")
                return

            if choice == "1":
                self._service_operations_menu()
                continue
            if choice == "2":
                self._status_menu()
                continue
            if choice == "3":
                self._configuration_menu()
                continue
            if choice == "4":
                self._logs_menu()
                continue
            if choice == "5":
                self._diagnostics_menu()
                continue

            self._output("Invalid option. Please choose a listed menu item.")

    def _show_main_menu(self) -> None:
        """Render the top-level navigation menu."""
        self._render_menu_header("Main Menu")
        self._output("1. Service Operations")
        self._output("2. Status Viewer")
        self._output("3. Configuration Manager")
        self._output("4. Log Viewer")
        self._output("5. Diagnostics")
        self._output("0. Exit")

    def _service_operations_menu(self) -> None:
        """Render service control actions submenu."""
        while True:
            self._render_menu_header("Service Operations")
            self._output("1. Start service")
            self._output("2. Stop service")
            self._output("3. Restart service")
            self._output("0. Back to main menu")
            choice = self._read_choice()
            if choice == "0":
                return

            if choice == "1":
                self._run_action(self.controller.start_service)
                continue
            if choice == "2":
                self._run_action(self.controller.stop_service)
                continue
            if choice == "3":
                self._run_action(self.controller.restart_service)
                continue

            self._output("Invalid option. Please choose a listed menu item.")

    def _status_menu(self) -> None:
        """Render status details submenu with explicit refresh."""
        while True:
            self._render_menu_header("Status Viewer")
            for line in self._load_status_lines():
                self._output(line)
            self._output("1. Refresh status")
            self._output("0. Back to main menu")
            choice = self._read_choice()
            if choice == "0":
                return
            if choice == "1":
                continue
            self._output("Invalid option. Please choose a listed menu item.")

    def _configuration_menu(self) -> None:
        """Render runtime configuration update submenu."""
        while True:
            self._render_menu_header("Configuration Manager")
            for line in self._load_configuration_lines():
                self._output(line)
            self._output("")
            self._output("1. Update host")
            self._output("2. Update port")
            self._output("3. Update workers")
            self._output("4. Enable auto start")
            self._output("5. Disable auto start")
            self._output("0. Back to main menu")
            choice = self._read_choice()
            if choice == "0":
                return

            if choice == "1":
                raw_value = self._input("Enter host value: ").strip()
                self._run_action(lambda: self.controller.update_host(raw_value))
                continue
            if choice == "2":
                raw_value = self._input("Enter port value: ").strip()
                self._run_action(lambda: self.controller.update_port(raw_value))
                continue
            if choice == "3":
                raw_value = self._input("Enter workers value: ").strip()
                self._run_action(lambda: self.controller.update_workers(raw_value))
                continue
            if choice == "4":
                self._run_action(self.controller.enable_autostart)
                continue
            if choice == "5":
                self._run_action(self.controller.disable_autostart)
                continue

            self._output("Invalid option. Please choose a listed menu item.")

    def _logs_menu(self) -> None:
        """Render log source and tail controls submenu."""
        line_count = DEFAULT_LOG_LINES
        selected_source_id = ""

        while True:
            sources = self._load_log_sources()
            source_ids = {source.source_id for source in sources}
            if selected_source_id not in source_ids:
                selected_source_id = sources[0].source_id if sources else ""

            selected_label = self._resolve_source_label(sources, selected_source_id)
            self._render_menu_header("Log Viewer")
            self._output(f"Current source: {selected_label}")
            self._output(f"Current line count: {line_count}")
            self._output("1. Select log source")
            self._output("2. Set log line count")
            self._output("3. Show/refresh logs")
            self._output("0. Back to main menu")
            choice = self._read_choice()
            if choice == "0":
                return
            if choice == "1":
                selected_source_id = self._select_log_source(sources, selected_source_id)
                continue
            if choice == "2":
                parsed = self._parse_line_count(self._input("Enter line count: ").strip())
                if parsed is not None:
                    line_count = parsed
                continue
            if choice == "3":
                self._show_logs(selected_source_id, line_count)
                continue
            self._output("Invalid option. Please choose a listed menu item.")

    def _diagnostics_menu(self) -> None:
        """Render diagnostics submenu and display results."""
        while True:
            self._render_menu_header("Diagnostics")
            self._output("1. Run diagnostics")
            self._output("0. Back to main menu")
            choice = self._read_choice()
            if choice == "0":
                return
            if choice == "1":
                self._run_diagnostics()
                continue
            self._output("Invalid option. Please choose a listed menu item.")

    def _render_menu_header(self, title: str) -> None:
        """Clear screen and print a standard menu header."""
        self._clear_screen()
        self._output("PyStreamASR Service Manager")
        self._output("")
        self._output(title)

    def _read_choice(self) -> str:
        """Prompt user for a menu choice."""
        return self._input("Select option: ").strip()

    def _load_status_lines(self) -> list[str]:
        """Load status lines for status submenu.

        Returns:
            List of status lines or a single error line.
        """
        try:
            status = self.controller.get_service_status()
        except Exception as exc:  # pragma: no cover - defensive path
            return [f"Failed to load service status: {exc}"]
        return format_status(status)

    def _load_configuration_lines(self) -> list[str]:
        """Load configuration snapshot lines for configuration submenu."""
        try:
            status = self.controller.get_service_status()
        except Exception as exc:  # pragma: no cover - defensive path
            return [f"Failed to load current configuration: {exc}"]

        lines = [
            f"Current runtime: {status.configured_runtime}",
            f"Current host: {status.configured_host}",
            f"Current port: {status.configured_port}",
            f"Current workers: {status.configured_workers}",
        ]
        if status.active_state is not None:
            lines.extend(
                [
                    f"Service backend: {status.active_state.backend}",
                    f"Service name: {status.active_state.service_name}",
                    "Auto start: "
                    + (
                        "enabled"
                        if status.active_state.autostart_enabled is True
                        else "disabled"
                        if status.active_state.autostart_enabled is False
                        else "unknown"
                    ),
                ]
            )
        else:
            lines.append("Auto start: unknown")
        return lines

    def _load_log_sources(self) -> list[LogSource]:
        """Load available log sources safely."""
        try:
            return self.controller.list_log_sources()
        except Exception as exc:  # pragma: no cover - defensive path
            self._output(f"Failed to list log sources: {exc}")
            return []

    def _resolve_source_label(self, sources: list[LogSource], source_id: str) -> str:
        """Resolve the display label for a source id."""
        for source in sources:
            if source.source_id == source_id:
                suffix = "" if source.available else " (unavailable)"
                return f"{source.label}{suffix}"
        return "(none)"

    def _select_log_source(self, sources: list[LogSource], selected_source_id: str) -> str:
        """Prompt user to select a log source."""
        if not sources:
            self._output("No log sources detected.")
            return ""

        self._output("")
        self._output("Select Log Source")
        for idx, source in enumerate(sources, start=1):
            status = "available" if source.available else "unavailable"
            self._output(f"{idx}. {source.label} ({status})")
        self._output("0. Back to log menu")

        choice = self._read_choice()
        if choice == "0":
            return selected_source_id
        try:
            selected_idx = int(choice)
        except ValueError:
            self._output("Invalid source selection.")
            return selected_source_id
        if selected_idx < 1 or selected_idx > len(sources):
            self._output("Invalid source selection.")
            return selected_source_id
        return sources[selected_idx - 1].source_id

    def _parse_line_count(self, raw_value: str) -> int | None:
        """Validate and normalize requested line count."""
        if not raw_value:
            return DEFAULT_LOG_LINES
        try:
            line_count = int(raw_value)
        except ValueError:
            self._output("Line count must be an integer.")
            return None
        if line_count < 1 or line_count > MAX_LOG_LINES:
            self._output(f"Line count must be between 1 and {MAX_LOG_LINES}.")
            return None
        return line_count

    def _show_logs(self, source_id: str, line_count: int) -> None:
        """Read selected log source and render output."""
        if not source_id:
            self._output("No log source selected.")
            return

        try:
            content = self.controller.read_log_source(source_id, line_count)
        except Exception as exc:  # pragma: no cover - defensive path
            self._output(f"Failed to read logs: {exc}")
            return

        self._output("")
        self._output(f"Logs ({source_id}, last {line_count} lines)")
        self._output("-" * 60)
        if content.strip():
            for line in content.splitlines():
                self._output(line)
        else:
            self._output("(no output)")
        self._output("-" * 60)

    def _run_diagnostics(self) -> None:
        """Run and render diagnostics results."""
        try:
            results = self.controller.run_diagnostics()
        except Exception as exc:  # pragma: no cover - defensive path
            self._output(f"Diagnostics failed: {exc}")
            return

        pass_count = sum(1 for result in results if result.status == "pass")
        warn_count = sum(1 for result in results if result.status == "warn")
        fail_count = sum(1 for result in results if result.status == "fail")

        self._output("")
        self._output(
            f"Diagnostics summary: pass={pass_count}, warn={warn_count}, fail={fail_count}, total={len(results)}"
        )
        self._output("-" * 60)
        for result in results:
            self._render_diagnostic_result(result)
        self._output("-" * 60)

    def _render_diagnostic_result(self, result: DiagnosticResult) -> None:
        """Render a single diagnostics check result."""
        self._output(f"[{result.status.upper()}] {result.check_name}")
        self._output(f"Summary: {result.summary}")
        self._output(f"Detail: {result.detail}")
        self._output(f"Remediation: {result.remediation}")
        self._output("")

    def _run_action(self, action: Callable[[], str]) -> None:
        """Execute an action and render the resulting message."""
        try:
            message = action()
        except ValueError as exc:
            self._output(str(exc))
            return
        except Exception as exc:  # pragma: no cover - defensive path
            self._output(f"Action failed: {exc}")
            return
        self._output(message)

    @staticmethod
    def _default_clear_screen() -> None:
        """Clear the active terminal screen."""
        command = "cls" if os.name == "nt" else "clear"
        os.system(command)
