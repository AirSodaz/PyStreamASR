"""Unit tests for the terminal service manager."""

from __future__ import annotations

import json
import logging
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


class FakeCompletedProcess:
    """Minimal completed-process stub for backend command tests."""

    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0) -> None:
        """Store command results."""
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class ServiceManagerTests(unittest.TestCase):
    """Test runtime configuration and installed-service behavior."""

    def setUp(self) -> None:
        """Create an isolated workspace for each test."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

        self.root_dir = Path(self.temp_dir.name)
        self.logs_dir = self.root_dir / "logs"
        self.logs_dir.mkdir(parents=True, exist_ok=True)

        self.env_file = self.root_dir / ".env"
        self.state_file = self.logs_dir / "service_state.json"
        self.metadata_file = self.logs_dir / "service_install.json"
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

        self.controller = self.build_controller(platform_name="nt")
        self.addCleanup(self.close_logger, self.controller.logger)

    def build_controller(self, platform_name: str) -> service_manager.ServiceController:
        """Create a controller bound to the temporary test workspace."""
        return service_manager.ServiceController(
            root_dir=self.root_dir,
            env_file=self.env_file,
            state_file=self.state_file,
            log_file=self.log_file,
            python_executable="python",
            platform_name=platform_name,
            install_metadata_file=self.metadata_file,
        )

    def close_logger(self, logger: logging.Logger) -> None:
        """Close handlers so the temporary directory can be removed on Windows."""
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)

    def override_method(self, obj: object, name: str, value: object) -> None:
        """Temporarily replace an instance or module attribute."""
        original_value = getattr(obj, name)
        self.addCleanup(setattr, obj, name, original_value)
        setattr(obj, name, value)

    def write_metadata(self, backend: str, service_name: str, runtime: str) -> None:
        """Persist install metadata for the active controller."""
        self.controller.save_install_metadata(
            service_manager.InstallMetadata(
                backend=backend,
                service_name=service_name,
                runtime=runtime,
            )
        )

    def make_status(
        self,
        *,
        status: str,
        backend: str,
        service_name: str,
        runtime: str,
    ) -> service_manager.ServiceStatus:
        """Build a status object for action-dispatch tests."""
        return service_manager.ServiceStatus(
            status=status,
            configured_runtime=runtime,
            configured_host="0.0.0.0",
            configured_port=8000,
            configured_workers=1,
            pid=None,
            pid_alive=status in {"running", "degraded"},
            health_ok=status == "running",
            health_url="http://127.0.0.1:8000/health" if status in {"running", "degraded"} else None,
            active_state=service_manager.ServiceState(
                backend=backend,
                service_name=service_name,
                runtime=runtime,
                manager_state=status,
            ),
            detail="status detail",
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

    def test_install_metadata_round_trip(self) -> None:
        """Installer metadata should persist and reload cleanly."""
        metadata = service_manager.InstallMetadata(
            backend=service_manager.WINDOWS_BACKEND,
            service_name="CustomTask",
            runtime="uvicorn",
        )

        self.controller.save_install_metadata(metadata)

        loaded_metadata = self.controller.get_install_metadata()
        self.assertEqual(loaded_metadata.backend, metadata.backend)
        self.assertEqual(loaded_metadata.service_name, metadata.service_name)
        self.assertEqual(loaded_metadata.runtime, metadata.runtime)

    def test_create_backend_uses_windows_metadata(self) -> None:
        """Windows metadata should create the scheduled-task backend."""
        self.write_metadata(service_manager.WINDOWS_BACKEND, "CustomTask", "uvicorn")

        backend = self.controller.create_backend()

        self.assertIsInstance(backend, service_manager.WindowsScheduledTaskBackend)

    def test_create_backend_uses_linux_metadata(self) -> None:
        """Linux metadata should create the systemd backend."""
        controller = self.build_controller(platform_name="posix")
        self.addCleanup(self.close_logger, controller.logger)
        controller.save_install_metadata(
            service_manager.InstallMetadata(
                backend=service_manager.LINUX_BACKEND,
                service_name="pystreamasr.service",
                runtime="gunicorn",
            )
        )

        backend = controller.create_backend()

        self.assertIsInstance(backend, service_manager.LinuxSystemdBackend)

    def test_windows_status_not_installed(self) -> None:
        """A missing scheduled task should report not installed."""
        self.write_metadata(service_manager.WINDOWS_BACKEND, "PyStreamASR", "uvicorn")
        self.override_method(
            self.controller,
            "run_command",
            lambda args: FakeCompletedProcess(
                stdout=json.dumps({"Installed": False, "State": "NotInstalled", "LastTaskResult": 0})
            ),
        )

        status = self.controller.get_service_status()

        self.assertEqual(status.status, "not installed")
        self.assertEqual(status.active_state.service_name, "PyStreamASR")

    def test_windows_status_running_when_health_ok(self) -> None:
        """A running scheduled task with healthy HTTP endpoint should report running."""
        self.write_metadata(service_manager.WINDOWS_BACKEND, "PyStreamASR", "uvicorn")
        self.override_method(
            self.controller,
            "run_command",
            lambda args: FakeCompletedProcess(
                stdout=json.dumps({"Installed": True, "State": "Running", "LastTaskResult": 0})
            ),
        )
        self.override_method(self.controller, "check_health", lambda host, port: True)

        status = self.controller.get_service_status()

        self.assertEqual(status.status, "running")
        self.assertTrue(status.health_ok)
        self.assertEqual(status.health_url, "http://127.0.0.1:8000/health")

    def test_windows_status_degraded_when_health_fails(self) -> None:
        """A running scheduled task with an unhealthy endpoint should report degraded."""
        self.write_metadata(service_manager.WINDOWS_BACKEND, "PyStreamASR", "uvicorn")
        self.override_method(
            self.controller,
            "run_command",
            lambda args: FakeCompletedProcess(
                stdout=json.dumps({"Installed": True, "State": "Running", "LastTaskResult": 0})
            ),
        )
        self.override_method(self.controller, "check_health", lambda host, port: False)

        status = self.controller.get_service_status()

        self.assertEqual(status.status, "degraded")
        self.assertFalse(status.health_ok)

    def test_windows_status_stopped_when_task_not_running(self) -> None:
        """A non-running scheduled task should report stopped."""
        self.write_metadata(service_manager.WINDOWS_BACKEND, "PyStreamASR", "uvicorn")
        self.override_method(
            self.controller,
            "run_command",
            lambda args: FakeCompletedProcess(
                stdout=json.dumps({"Installed": True, "State": "Ready", "LastTaskResult": 0})
            ),
        )

        status = self.controller.get_service_status()

        self.assertEqual(status.status, "stopped")
        self.assertFalse(status.pid_alive)

    def test_linux_status_not_installed(self) -> None:
        """A missing systemd unit should report not installed."""
        controller = self.build_controller(platform_name="posix")
        self.addCleanup(self.close_logger, controller.logger)
        controller.save_install_metadata(
            service_manager.InstallMetadata(
                backend=service_manager.LINUX_BACKEND,
                service_name="pystreamasr.service",
                runtime="gunicorn",
            )
        )
        self.override_method(
            controller,
            "run_command",
            lambda args: FakeCompletedProcess(
                stdout="LoadState=not-found\nActiveState=inactive\nSubState=dead\n",
            ),
        )

        status = controller.get_service_status()

        self.assertEqual(status.status, "not installed")
        self.assertEqual(status.active_state.backend, service_manager.LINUX_BACKEND)

    def test_linux_status_running_when_health_ok(self) -> None:
        """An active systemd unit with healthy HTTP endpoint should report running."""
        controller = self.build_controller(platform_name="posix")
        self.addCleanup(self.close_logger, controller.logger)
        controller.save_install_metadata(
            service_manager.InstallMetadata(
                backend=service_manager.LINUX_BACKEND,
                service_name="pystreamasr.service",
                runtime="gunicorn",
            )
        )
        self.override_method(
            controller,
            "run_command",
            lambda args: FakeCompletedProcess(
                stdout="LoadState=loaded\nActiveState=active\nSubState=running\nId=pystreamasr.service\n",
            ),
        )
        self.override_method(controller, "check_health", lambda host, port: True)

        status = controller.get_service_status()

        self.assertEqual(status.status, "running")
        self.assertTrue(status.health_ok)
        self.assertEqual(status.health_url, "http://127.0.0.1:8000/health")

    def test_linux_status_degraded_when_health_fails(self) -> None:
        """An active systemd unit with an unhealthy endpoint should report degraded."""
        controller = self.build_controller(platform_name="posix")
        self.addCleanup(self.close_logger, controller.logger)
        controller.save_install_metadata(
            service_manager.InstallMetadata(
                backend=service_manager.LINUX_BACKEND,
                service_name="pystreamasr.service",
                runtime="gunicorn",
            )
        )
        self.override_method(
            controller,
            "run_command",
            lambda args: FakeCompletedProcess(
                stdout="LoadState=loaded\nActiveState=active\nSubState=running\nId=pystreamasr.service\n",
            ),
        )
        self.override_method(controller, "check_health", lambda host, port: False)

        status = controller.get_service_status()

        self.assertEqual(status.status, "degraded")
        self.assertFalse(status.health_ok)

    def test_linux_status_stopped_when_unit_inactive(self) -> None:
        """An inactive systemd unit should report stopped."""
        controller = self.build_controller(platform_name="posix")
        self.addCleanup(self.close_logger, controller.logger)
        controller.save_install_metadata(
            service_manager.InstallMetadata(
                backend=service_manager.LINUX_BACKEND,
                service_name="pystreamasr.service",
                runtime="gunicorn",
            )
        )
        self.override_method(
            controller,
            "run_command",
            lambda args: FakeCompletedProcess(
                stdout="LoadState=loaded\nActiveState=inactive\nSubState=dead\nId=pystreamasr.service\n",
            ),
        )

        status = controller.get_service_status()

        self.assertEqual(status.status, "stopped")
        self.assertFalse(status.pid_alive)

    def test_windows_start_dispatches_to_start_scheduled_task(self) -> None:
        """Starting should invoke Start-ScheduledTask for the configured task."""
        self.write_metadata(service_manager.WINDOWS_BACKEND, "CustomTask", "uvicorn")
        self.override_method(
            self.controller,
            "get_service_status",
            lambda: self.make_status(
                status="stopped",
                backend=service_manager.WINDOWS_BACKEND,
                service_name="CustomTask",
                runtime="uvicorn",
            ),
        )

        captured_args: list[list[str]] = []

        def fake_run_command(args: list[str]) -> FakeCompletedProcess:
            captured_args.append(args)
            return FakeCompletedProcess(stdout="ok\n")

        self.override_method(self.controller, "run_command", fake_run_command)

        message = self.controller.start_service()

        self.assertIn("Start requested", message)
        self.assertEqual(captured_args[0][:3], ["powershell.exe", "-NoProfile", "-Command"])
        self.assertIn("Start-ScheduledTask", captured_args[0][3])

    def test_windows_stop_dispatches_to_stop_scheduled_task(self) -> None:
        """Stopping should invoke Stop-ScheduledTask for the configured task."""
        self.write_metadata(service_manager.WINDOWS_BACKEND, "CustomTask", "uvicorn")
        self.override_method(
            self.controller,
            "get_service_status",
            lambda: self.make_status(
                status="running",
                backend=service_manager.WINDOWS_BACKEND,
                service_name="CustomTask",
                runtime="uvicorn",
            ),
        )

        captured_args: list[list[str]] = []

        def fake_run_command(args: list[str]) -> FakeCompletedProcess:
            captured_args.append(args)
            return FakeCompletedProcess(stdout="ok\n")

        self.override_method(self.controller, "run_command", fake_run_command)

        message = self.controller.stop_service()

        self.assertIn("Stop requested", message)
        self.assertEqual(captured_args[0][:3], ["powershell.exe", "-NoProfile", "-Command"])
        self.assertIn("Stop-ScheduledTask", captured_args[0][3])

    def test_linux_restart_dispatches_to_systemctl_restart(self) -> None:
        """Restart should invoke `systemctl restart` for the configured unit."""
        controller = self.build_controller(platform_name="posix")
        self.addCleanup(self.close_logger, controller.logger)
        controller.save_install_metadata(
            service_manager.InstallMetadata(
                backend=service_manager.LINUX_BACKEND,
                service_name="pystreamasr.service",
                runtime="gunicorn",
            )
        )
        self.override_method(
            controller,
            "get_service_status",
            lambda: self.make_status(
                status="running",
                backend=service_manager.LINUX_BACKEND,
                service_name="pystreamasr.service",
                runtime="gunicorn",
            ),
        )

        captured_args: list[list[str]] = []

        def fake_run_command(args: list[str]) -> FakeCompletedProcess:
            captured_args.append(args)
            return FakeCompletedProcess(stdout="")

        self.override_method(controller, "run_command", fake_run_command)

        message = controller.restart_service()

        self.assertIn("Restart requested", message)
        self.assertEqual(captured_args[0], ["systemctl", "restart", "pystreamasr.service"])


if __name__ == "__main__":
    unittest.main()
