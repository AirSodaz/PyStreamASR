"""Unit tests for the terminal service manager."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT_DIR / "scripts"

for import_path in (ROOT_DIR, SCRIPTS_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from core.config import get_settings
import service_manager


class ServiceManagerTests(unittest.TestCase):
    """Test runtime configuration and process-state behavior."""

    def setUp(self) -> None:
        """Create an isolated workspace for each test."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

        self.root_dir = Path(self.temp_dir.name)
        self.logs_dir = self.root_dir / "logs"
        self.logs_dir.mkdir(parents=True, exist_ok=True)

        self.env_file = self.root_dir / ".env"
        self.state_file = self.logs_dir / "service_state.json"
        self.log_file = self.logs_dir / "service_manager.log"
        self.env_file.write_text(
            "\n".join(
                [
                    "PROJECT_NAME=PyStreamASR",
                    "MYSQL_DATABASE_URL=mysql+aiomysql://root:password@localhost/pystreamasr",
                    "MODEL_PATH=models/test-model",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        self.controller = service_manager.ServiceController(
            root_dir=self.root_dir,
            env_file=self.env_file,
            state_file=self.state_file,
            log_file=self.log_file,
            python_executable="python",
        )

    def override_method(self, obj: object, name: str, value: object) -> None:
        """Temporarily replace an instance or module attribute."""
        original_value = getattr(obj, name)
        self.addCleanup(setattr, obj, name, original_value)
        setattr(obj, name, value)

    def create_state(self, pid: int = 4321) -> service_manager.ServiceState:
        """Build a consistent test state object."""
        return service_manager.ServiceState(
            pid=pid,
            host="0.0.0.0",
            port=8000,
            workers=1,
            started_at="2026-03-17T00:00:00+00:00",
            launch_command=["python", "-m", "uvicorn", "main:app"],
            process_creation_date="20260317000000.000000+000",
            log_file=str(self.log_file),
        )

    def test_settings_default_runtime_values(self) -> None:
        """Missing APP_* keys should fall back to defaults."""
        settings = get_settings(self.env_file)

        self.assertEqual(settings.APP_HOST, "0.0.0.0")
        self.assertEqual(settings.APP_PORT, 8000)
        self.assertEqual(settings.APP_WORKERS, 1)

    def test_update_runtime_values_persists_to_env(self) -> None:
        """Valid edits should be written back to the environment file."""
        self.controller.update_host("127.0.0.1")
        self.controller.update_port("9001")
        self.controller.update_workers("3")

        settings = self.controller.load_settings()
        env_contents = self.env_file.read_text(encoding="utf-8")

        self.assertEqual(settings.APP_HOST, "127.0.0.1")
        self.assertEqual(settings.APP_PORT, 9001)
        self.assertEqual(settings.APP_WORKERS, 3)
        self.assertIn("APP_HOST=127.0.0.1", env_contents)
        self.assertIn("APP_PORT=9001", env_contents)
        self.assertIn("APP_WORKERS=3", env_contents)

    def test_invalid_runtime_value_does_not_persist(self) -> None:
        """Invalid port or worker edits should leave the file unchanged."""
        original_contents = self.env_file.read_text(encoding="utf-8")

        with self.assertRaises(ValueError):
            self.controller.update_port("70000")

        self.assertEqual(self.env_file.read_text(encoding="utf-8"), original_contents)

        with self.assertRaises(ValueError):
            self.controller.update_workers("0")

        self.assertEqual(self.env_file.read_text(encoding="utf-8"), original_contents)

    def test_stale_state_is_treated_as_stopped(self) -> None:
        """A dead PID should be cleared and reported as stopped."""
        self.controller.save_state(self.create_state())
        self.override_method(self.controller, "is_pid_running", lambda pid: False)
        status = self.controller.get_service_status()

        self.assertEqual(status.status, "stopped")
        self.assertFalse(self.state_file.exists())

    def test_status_running_when_pid_alive_and_health_ok(self) -> None:
        """A live PID with a healthy endpoint should report running."""
        self.controller.save_state(self.create_state())
        self.override_method(self.controller, "is_pid_running", lambda pid: True)
        self.override_method(self.controller, "is_managed_process", lambda state: True)
        self.override_method(self.controller, "check_health", lambda host, port: True)
        status = self.controller.get_service_status()

        self.assertEqual(status.status, "running")
        self.assertTrue(status.health_ok)
        self.assertEqual(status.health_url, "http://127.0.0.1:8000/health")

    def test_status_degraded_when_health_fails(self) -> None:
        """A live PID with a failing endpoint should report degraded."""
        self.controller.save_state(self.create_state())
        self.override_method(self.controller, "is_pid_running", lambda pid: True)
        self.override_method(self.controller, "is_managed_process", lambda state: True)
        self.override_method(self.controller, "check_health", lambda host, port: False)
        status = self.controller.get_service_status()

        self.assertEqual(status.status, "degraded")
        self.assertFalse(status.health_ok)

    def test_duplicate_start_is_blocked(self) -> None:
        """Starting an already-running managed service should not spawn again."""
        running_status = service_manager.ServiceStatus(
            status="running",
            configured_host="0.0.0.0",
            configured_port=8000,
            configured_workers=1,
            pid=4321,
            pid_alive=True,
            health_ok=True,
            health_url="http://127.0.0.1:8000/health",
            active_state=self.create_state(),
            detail="Service is healthy.",
        )

        self.override_method(self.controller, "get_service_status", lambda: running_status)

        def unexpected_popen(*args: object, **kwargs: object) -> None:
            raise AssertionError("subprocess.Popen should not be called for duplicate start.")

        self.override_method(service_manager.subprocess, "Popen", unexpected_popen)
        message = self.controller.start_service()
        self.assertIn("already running", message)

    def test_restart_replaces_saved_state(self) -> None:
        """Restart should replace old process metadata with the new state."""
        self.controller.save_state(self.create_state(pid=1111))

        def fake_stop() -> str:
            self.controller.clear_state()
            return "stopped"

        def fake_start() -> str:
            self.controller.save_state(self.create_state(pid=2222))
            return "started"

        self.override_method(self.controller, "stop_service", fake_stop)
        self.override_method(self.controller, "start_service", fake_start)
        message = self.controller.restart_service()

        current_state = self.controller.load_state()
        self.assertIsNotNone(current_state)
        self.assertEqual(current_state.pid, 2222)
        self.assertIn("stopped", message)
        self.assertIn("started", message)


if __name__ == "__main__":
    unittest.main()
