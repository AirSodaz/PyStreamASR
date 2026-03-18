"""Unit tests for the layered terminal service manager menu."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT_DIR / "scripts"

for import_path in (ROOT_DIR, SCRIPTS_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

import service_manager
from scripts.service_manager_cli import ServiceManagerCliApp


class FakeController:
    """Controller test double for CLI menu tests."""

    def __init__(self) -> None:
        """Initialize fake state."""
        self.actions: list[str] = []
        self.status_calls = 0
        self.log_reads = 0
        self.diag_runs = 0
        self.host = "0.0.0.0"
        self.port = 8000
        self.workers = 1
        self.autostart_enabled = True
        self.last_log_request: tuple[str, int] | None = None

    def get_service_status(self) -> service_manager.ServiceStatus:
        """Return a stable status payload."""
        self.status_calls += 1
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
                autostart_enabled=self.autostart_enabled,
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
        """Update host value."""
        self.host = raw_value
        self.actions.append("update_host")
        return f"host updated to {raw_value}"

    def update_port(self, raw_value: str) -> str:
        """Update port value."""
        self.port = int(raw_value)
        self.actions.append("update_port")
        return f"port updated to {raw_value}"

    def update_workers(self, raw_value: str) -> str:
        """Update worker value."""
        self.workers = int(raw_value)
        self.actions.append("update_workers")
        return f"workers updated to {raw_value}"

    def enable_autostart(self) -> str:
        """Enable startup behavior."""
        self.autostart_enabled = True
        self.actions.append("enable_autostart")
        return "auto start enabled"

    def disable_autostart(self) -> str:
        """Disable startup behavior."""
        self.autostart_enabled = False
        self.actions.append("disable_autostart")
        return "auto start disabled"

    def list_log_sources(self) -> list[service_manager.LogSource]:
        """Return a deterministic log source list."""
        return [
            service_manager.LogSource(
                source_id="app_log",
                label="Application log",
                available=True,
                backend="common",
                kind="file",
                descriptor="logs/2026-03-18.log",
            ),
            service_manager.LogSource(
                source_id="service_manager_log",
                label="Service manager log",
                available=True,
                backend="common",
                kind="file",
                descriptor="logs/service_manager.log",
            ),
        ]

    def read_log_source(self, source_id: str, lines: int = service_manager.DEFAULT_LOG_LINES) -> str:
        """Return synthetic logs."""
        self.log_reads += 1
        self.last_log_request = (source_id, lines)
        return f"{source_id} line 1\n{source_id} line 2"

    def run_diagnostics(self) -> list[service_manager.DiagnosticResult]:
        """Return deterministic diagnostics."""
        self.diag_runs += 1
        return [
            service_manager.DiagnosticResult(
                check_name="Health endpoint",
                status="pass",
                summary="healthy",
                detail="http://127.0.0.1:8000/health",
                remediation="none",
            ),
            service_manager.DiagnosticResult(
                check_name="Model files",
                status="warn",
                summary="partial",
                detail="missing optional file",
                remediation="download full model pack",
            ),
        ]


class ScriptedIo:
    """Scripted input/output harness for menu tests."""

    def __init__(self, scripted_inputs: list[str]) -> None:
        """Store scripted input and output traces."""
        self._inputs = scripted_inputs
        self.outputs: list[str] = []
        self.clear_calls = 0

    def input_func(self, prompt: str) -> str:
        """Return next scripted input."""
        self.outputs.append(prompt)
        if not self._inputs:
            raise AssertionError("Scripted input exhausted.")
        return self._inputs.pop(0)

    def output_func(self, message: str) -> None:
        """Capture output line."""
        self.outputs.append(message)

    def clear_func(self) -> None:
        """Record clear-screen calls."""
        self.clear_calls += 1
        self.outputs.append("<clear>")

    def joined_output(self) -> str:
        """Return all outputs as one string."""
        return "\n".join(self.outputs)


class ServiceManagerCliTests(unittest.TestCase):
    """Verify layered CLI navigation and action dispatch."""

    def make_app(self, io: ScriptedIo, controller: FakeController | None = None) -> tuple[ServiceManagerCliApp, FakeController]:
        """Build app with injected IO and fake controller."""
        fake_controller = controller or FakeController()
        app = ServiceManagerCliApp(
            fake_controller, input_func=io.input_func, output_func=io.output_func, clear_screen_func=io.clear_func
        )
        return app, fake_controller

    def test_main_menu_navigation_and_zero_exit(self) -> None:
        """Main menu should show navigation-only options and quit on zero."""
        io = ScriptedIo(["0"])
        app, _ = self.make_app(io)

        app.run()

        output = io.joined_output()
        self.assertIn("Main Menu", output)
        self.assertIn("1. Service Operations", output)
        self.assertIn("0. Exit", output)
        self.assertNotIn("q. Quit", output)
        self.assertNotIn("6. View Options", output)
        self.assertNotIn("Status:", output)
        self.assertGreaterEqual(io.clear_calls, 1)

    def test_main_menu_zero_exits_and_submenu_zero_returns(self) -> None:
        """Zero should exit main menu but return from submenus."""
        io = ScriptedIo(["2", "0", "0"])
        app, controller = self.make_app(io)

        app.run()

        output = io.joined_output()
        self.assertGreaterEqual(output.count("Main Menu"), 2)
        self.assertEqual(controller.status_calls, 1)
        self.assertGreaterEqual(io.clear_calls, 3)

    def test_status_refresh_and_no_c_shortcut(self) -> None:
        """Status refresh should work and c should not clear screen globally."""
        io = ScriptedIo(["2", "1", "0", "c", "0"])
        app, controller = self.make_app(io)

        app.run()

        output = io.joined_output()
        self.assertEqual(controller.status_calls, 2)
        self.assertGreaterEqual(io.clear_calls, 4)
        self.assertIn("Invalid option. Please choose a listed menu item.", output)

    def test_service_and_configuration_actions_dispatch(self) -> None:
        """Service and config submenus should call controller APIs."""
        io = ScriptedIo(
            [
                "1",
                "1",
                "2",
                "3",
                "0",
                "3",
                "1",
                "127.0.0.1",
                "2",
                "9001",
                "3",
                "4",
                "4",
                "5",
                "0",
                "0",
            ]
        )
        app, controller = self.make_app(io)

        app.run()

        self.assertEqual(
            controller.actions,
            [
                "start",
                "stop",
                "restart",
                "update_host",
                "update_port",
                "update_workers",
                "enable_autostart",
                "disable_autostart",
            ],
        )
        self.assertEqual(controller.host, "127.0.0.1")
        self.assertEqual(controller.port, 9001)
        self.assertEqual(controller.workers, 4)
        self.assertFalse(controller.autostart_enabled)

    def test_logs_and_diagnostics_submenus(self) -> None:
        """Log viewer and diagnostics submenu actions should be reachable."""
        io = ScriptedIo(
            [
                "4",
                "1",
                "2",
                "2",
                "50",
                "3",
                "0",
                "5",
                "1",
                "0",
                "0",
            ]
        )
        app, controller = self.make_app(io)

        app.run()

        output = io.joined_output()
        self.assertEqual(controller.log_reads, 1)
        self.assertEqual(controller.last_log_request, ("service_manager_log", 50))
        self.assertEqual(controller.diag_runs, 1)
        self.assertGreaterEqual(io.clear_calls, 4)
        self.assertIn("Diagnostics summary:", output)
        self.assertIn("[PASS] Health endpoint", output)


if __name__ == "__main__":
    unittest.main()
