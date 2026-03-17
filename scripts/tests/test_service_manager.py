"""Unit tests for the terminal service manager."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
import logging
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
            platform_name="nt",
        )
        self.addCleanup(self.close_controller_logger)

    def close_controller_logger(self) -> None:
        """Close file handlers so the temporary directory can be removed on Windows."""
        for handler in list(self.controller.logger.handlers):
            handler.close()
            self.controller.logger.removeHandler(handler)

    def override_method(self, obj: object, name: str, value: object) -> None:
        """Temporarily replace an instance or module attribute."""
        original_value = getattr(obj, name)
        self.addCleanup(setattr, obj, name, original_value)
        setattr(obj, name, value)

    def create_state(self, pid: int = 4321, runtime: str = "uvicorn") -> service_manager.ServiceState:
        """Build a consistent test state object."""
        return service_manager.ServiceState(
            pid=pid,
            host="0.0.0.0",
            port=8000,
            workers=1,
            started_at="2026-03-17T00:00:00+00:00",
            launch_command=["python", "-m", "uvicorn", "main:app"],
            runtime=runtime,
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

    def test_is_pid_running_windows_uses_cim_lookup(self) -> None:
        """Windows PID checks should use CIM output instead of locale-specific tasklist text."""

        class FakeCompletedProcess:
            def __init__(self, stdout: str, returncode: int = 0) -> None:
                self.stdout = stdout
                self.stderr = ""
                self.returncode = returncode

        captured_args: list[str] = []

        def fake_run(args: list[str], **kwargs: object) -> FakeCompletedProcess:
            captured_args.extend(args)
            return FakeCompletedProcess(
                json.dumps(
                    {
                        "ProcessId": 4321,
                        "CommandLine": "python -m uvicorn main:app --host 0.0.0.0 --port 8000",
                        "CreationDate": "20260317000000.000000+000",
                    }
                )
            )

        self.override_method(service_manager.subprocess, "run", fake_run)

        self.assertTrue(self.controller.is_pid_running(4321))
        self.assertEqual(captured_args[:3], ["powershell", "-NoProfile", "-Command"])
        self.assertIn("Get-CimInstance Win32_Process", captured_args[3])

    def test_is_pid_running_windows_false_when_process_is_missing(self) -> None:
        """A missing Windows PID should return False even if no localized status text is available."""

        class FakeCompletedProcess:
            def __init__(self, stdout: str, returncode: int = 0) -> None:
                self.stdout = stdout
                self.stderr = ""
                self.returncode = returncode

        def fake_run(args: list[str], **kwargs: object) -> FakeCompletedProcess:
            return FakeCompletedProcess("")

        self.override_method(service_manager.subprocess, "run", fake_run)

        self.assertFalse(self.controller.is_pid_running(4321))

    def test_duplicate_start_is_blocked(self) -> None:
        """Starting an already-running managed service should not spawn again."""
        running_status = service_manager.ServiceStatus(
            status="running",
            configured_runtime="uvicorn",
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
        self.assertIn("already running", self.log_file.read_text(encoding="utf-8"))

    def test_start_service_does_not_capture_application_logs(self) -> None:
        """Managed startup should not redirect child stdout/stderr into manager logs."""
        self.override_method(
            self.controller,
            "get_service_status",
            lambda: service_manager.ServiceStatus(
                status="stopped",
                configured_runtime="uvicorn",
                configured_host="0.0.0.0",
                configured_port=8000,
                configured_workers=1,
                pid=None,
                pid_alive=False,
                health_ok=False,
                health_url=None,
                active_state=None,
                detail="Service is not running.",
            ),
        )
        self.override_method(
            self.controller,
            "get_process_info",
            lambda pid: {"CreationDate": "20260317000000.000000+000"},
        )

        captured_kwargs: dict[str, object] = {}

        class FakeProcess:
            pid = 2468

        def fake_popen(*args: object, **kwargs: object) -> FakeProcess:
            captured_kwargs.update(kwargs)
            return FakeProcess()

        self.override_method(service_manager.subprocess, "Popen", fake_popen)

        message = self.controller.start_service()

        self.assertIn("PID 2468", message)
        self.assertEqual(captured_kwargs["stdout"], service_manager.subprocess.DEVNULL)
        self.assertEqual(captured_kwargs["stderr"], service_manager.subprocess.DEVNULL)
        self.assertTrue(self.log_file.exists())
        log_contents = self.log_file.read_text(encoding="utf-8")
        self.assertIn("Starting managed uvicorn service", log_contents)
        self.assertIn("Service start requested for PID 2468", log_contents)

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

    def test_build_command_uses_gunicorn_on_posix(self) -> None:
        """macOS/Linux should launch gunicorn with explicit bind and workers."""
        controller = service_manager.ServiceController(
            root_dir=self.root_dir,
            env_file=self.env_file,
            state_file=self.state_file,
            log_file=self.log_file,
            python_executable=str(self.root_dir / "venv" / "bin" / "python"),
            platform_name="posix",
        )
        self.addCleanup(self.close_logger, controller.logger)

        command = controller.build_command(controller.load_settings())

        self.assertEqual(command[0], "gunicorn")
        self.assertEqual(
            command[1:],
            [
                "main:app",
                "-c",
                str(self.root_dir / "gunicorn.conf.py"),
                "--bind",
                "0.0.0.0:8000",
                "--workers",
                "1",
            ],
        )

    def test_is_managed_process_accepts_gunicorn_state(self) -> None:
        """Saved gunicorn state should validate against the current process metadata."""
        controller = service_manager.ServiceController(
            root_dir=self.root_dir,
            env_file=self.env_file,
            state_file=self.state_file,
            log_file=self.log_file,
            python_executable="python",
            platform_name="posix",
        )
        self.addCleanup(self.close_logger, controller.logger)

        state = self.create_state(pid=9999, runtime="gunicorn")
        state.launch_command = ["gunicorn", "main:app", "-c", str(self.root_dir / "gunicorn.conf.py")]
        state.process_creation_date = "Thu Mar 18 01:23:45 2026"

        self.override_method(
            controller,
            "get_process_info",
            lambda pid: {
                "CommandLine": "gunicorn main:app -c gunicorn.conf.py --bind 0.0.0.0:8000 --workers 1",
                "CreationDate": "Thu Mar 18 01:23:45 2026",
            },
        )

        self.assertTrue(controller.is_managed_process(state))

    def close_logger(self, logger: logging.Logger) -> None:
        """Close handlers for an arbitrary logger instance."""
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)


if __name__ == "__main__":
    unittest.main()
