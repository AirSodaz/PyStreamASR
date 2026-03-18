"""UI smoke tests for the Textual service manager."""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT_DIR / "scripts"

for import_path in (ROOT_DIR, SCRIPTS_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

import service_manager

try:
    from scripts.service_manager_tui import ServiceManagerApp

    TEXTUAL_AVAILABLE = True
except Exception:
    ServiceManagerApp = None  # type: ignore[assignment]
    TEXTUAL_AVAILABLE = False


class FakeController:
    """Controller test double for Textual smoke tests."""

    def __init__(self) -> None:
        """Initialize fake state."""
        self.actions: list[str] = []
        self.log_reads = 0
        self.diag_runs = 0
        self.host = "0.0.0.0"
        self.port = 8000
        self.workers = 1

    def get_backend_display_name(self) -> str:
        """Return a static backend label."""
        return "Scheduled Task"

    def get_runtime_display_name(self) -> str:
        """Return a static runtime label."""
        return "Uvicorn"

    def get_service_status(self) -> service_manager.ServiceStatus:
        """Return a stable service status payload."""
        return service_manager.ServiceStatus(
            status="running",
            configured_runtime="uvicorn",
            configured_host=self.host,
            configured_port=self.port,
            configured_workers=self.workers,
            pid=1234,
            pid_alive=True,
            health_ok=True,
            health_url="http://127.0.0.1:8000/health",
            active_state=service_manager.ServiceState(
                backend=service_manager.WINDOWS_BACKEND,
                service_name="PyStreamASR",
                runtime="uvicorn",
                manager_state="running",
            ),
            detail="ok",
        )

    def start_service(self) -> str:
        """Record start action."""
        self.actions.append("start")
        return "start called"

    def stop_service(self) -> str:
        """Record stop action."""
        self.actions.append("stop")
        return "stop called"

    def restart_service(self) -> str:
        """Record restart action."""
        self.actions.append("restart")
        return "restart called"

    def update_host(self, raw_value: str) -> str:
        """Update host."""
        self.host = raw_value
        self.actions.append("update_host")
        return "host updated"

    def update_port(self, raw_value: str) -> str:
        """Update port."""
        self.port = int(raw_value)
        self.actions.append("update_port")
        return "port updated"

    def update_workers(self, raw_value: str) -> str:
        """Update workers."""
        self.workers = int(raw_value)
        self.actions.append("update_workers")
        return "workers updated"

    def list_log_sources(self) -> list[service_manager.LogSource]:
        """Return one file log source."""
        return [
            service_manager.LogSource(
                source_id="service_manager_log",
                label="Service manager log",
                available=True,
                backend="common",
                kind="file",
                descriptor="logs/service_manager.log",
            )
        ]

    def read_log_source(self, source_id: str, lines: int = 200) -> str:
        """Return synthetic log lines."""
        self.log_reads += 1
        return f"{source_id} line 1\n{source_id} line 2"

    def run_diagnostics(self) -> list[service_manager.DiagnosticResult]:
        """Return one diagnostics result."""
        self.diag_runs += 1
        return [
            service_manager.DiagnosticResult(
                check_name="Health endpoint",
                status="pass",
                summary="ok",
                detail="http://127.0.0.1:8000/health",
                remediation="none",
            )
        ]


@unittest.skipUnless(TEXTUAL_AVAILABLE, "Textual is not available")
class ServiceManagerTuiTests(unittest.TestCase):
    """Smoke-test Textual app wiring."""

    def test_app_boots_and_renders_core_widgets(self) -> None:
        """App should compose the core dashboard widgets."""
        controller = FakeController()
        app = ServiceManagerApp(controller)  # type: ignore[misc]

        async def scenario() -> None:
            async with app.run_test() as pilot:
                await pilot.pause(0.3)
                app.query_one("#main-tabs")
                app.query_one("#status-view")
                app.query_one("#log-source-select")
                app.query_one("#diag-table")

        asyncio.run(scenario())

    def test_service_action_dispatches_to_controller(self) -> None:
        """Start binding should dispatch to controller start action."""
        controller = FakeController()
        app = ServiceManagerApp(controller)  # type: ignore[misc]

        async def scenario() -> None:
            async with app.run_test() as pilot:
                app.action_start_service()
                await pilot.pause(0.3)
                self.assertIn("start", controller.actions)

        asyncio.run(scenario())

    def test_logs_and_diagnostics_workers_run(self) -> None:
        """Log refresh and diagnostics should invoke controller APIs."""
        controller = FakeController()
        app = ServiceManagerApp(controller)  # type: ignore[misc]

        async def scenario() -> None:
            async with app.run_test() as pilot:
                app._schedule_logs_refresh()
                app._schedule_diagnostics()
                await pilot.pause(0.5)
                self.assertGreater(controller.log_reads, 0)
                self.assertGreater(controller.diag_runs, 0)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
