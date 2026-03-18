"""Terminal menu utilities for managing the installed PyStreamASR service."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.config import Settings, get_settings


WINDOWS_BACKEND = "scheduled_task"
LINUX_BACKEND = "systemd"
DEFAULT_WINDOWS_TASK_NAME = "PyStreamASR"
DEFAULT_LINUX_UNIT_NAME = "pystreamasr.service"
DEFAULT_ENV_FILE = ROOT_DIR / ".env"
DEFAULT_STATE_FILE = ROOT_DIR / "logs" / "service_state.json"
DEFAULT_LOG_FILE = ROOT_DIR / "logs" / "service_manager.log"
DEFAULT_INSTALL_METADATA_FILE = ROOT_DIR / "logs" / "service_install.json"
DATE_LOG_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}\.log$")
MAX_LOG_LINES = 5000
DEFAULT_LOG_LINES = 200
REQUIRED_ENV_KEYS = (
    "MODEL_PATH",
    "MYSQL_DATABASE_URL",
    "APP_HOST",
    "APP_PORT",
    "APP_WORKERS",
)
MODEL_REQUIRED_FILES = (
    "encoder.int8.onnx",
    "decoder.int8.onnx",
    "tokens.txt",
)


@dataclass(slots=True)
class InstallMetadata:
    """Installer-provided information about the managed service."""

    backend: str
    service_name: str
    runtime: str
    install_mode: str = "service"


@dataclass(slots=True)
class BackendStatus:
    """Current status returned by a service-manager backend."""

    installed: bool
    active: bool
    manager_state: str
    detail: str
    pid: int | None = None
    autostart_enabled: bool | None = None


@dataclass(slots=True)
class ServiceState:
    """Information about the configured managed service target."""

    backend: str
    service_name: str
    runtime: str
    manager_state: str
    autostart_enabled: bool | None = None
    install_mode: str = "service"
    log_file: str | None = None


@dataclass(slots=True)
class ServiceStatus:
    """Current status information for the managed service."""

    status: str
    configured_runtime: str
    configured_host: str
    configured_port: int
    configured_workers: int
    pid: int | None
    pid_alive: bool
    health_ok: bool
    health_url: str | None
    active_state: ServiceState | None
    detail: str


@dataclass(slots=True)
class LogSource:
    """Log source that can be viewed from the terminal manager."""

    source_id: str
    label: str
    available: bool
    backend: str
    kind: str
    descriptor: str


@dataclass(slots=True)
class DiagnosticResult:
    """Troubleshooting check result."""

    check_name: str
    status: str
    summary: str
    detail: str
    remediation: str


class BaseServiceBackend:
    """Common interface for platform-specific service managers."""

    def __init__(self, controller: ServiceController, metadata: InstallMetadata) -> None:
        """Store the controller and install metadata."""
        self.controller = controller
        self.metadata = metadata

    def get_status(self) -> BackendStatus:
        """Return the service manager's view of the service state."""
        raise NotImplementedError

    def start(self) -> str:
        """Start the service."""
        raise NotImplementedError

    def stop(self) -> str:
        """Stop the service."""
        raise NotImplementedError

    def restart(self) -> str:
        """Restart the service."""
        raise NotImplementedError

    def set_autostart(self, enabled: bool) -> str:
        """Enable or disable auto-start for the managed service."""
        requested_state = "enabled" if enabled else "disabled"
        return (
            f"Auto-start management is unavailable for backend '{self.metadata.backend}'. "
            f"Requested state: {requested_state}."
        )

    def _command_error(self, action: str, result: subprocess.CompletedProcess[str]) -> str:
        """Normalize command failures into a single readable message."""
        output = (result.stderr or result.stdout or "").strip()
        if not output:
            output = f"{action} failed with exit code {result.returncode}."
        return output


class WindowsScheduledTaskBackend(BaseServiceBackend):
    """Manage the installed service through Windows Task Scheduler."""

    def get_status(self) -> BackendStatus:
        """Inspect the scheduled task state."""
        safe_name = self.controller.quote_powershell_literal(self.metadata.service_name)
        command = (
            f"$task = Get-ScheduledTask -TaskName '{safe_name}' -ErrorAction SilentlyContinue; "
            "if ($null -eq $task) { "
            "[pscustomobject]@{ Installed = $false; State = 'NotInstalled'; LastTaskResult = 0; AutostartEnabled = $null } "
            "| ConvertTo-Json -Compress; exit 0 }; "
            f"$info = Get-ScheduledTaskInfo -TaskName '{safe_name}'; "
            "$triggers = @($task.Triggers); "
            "$autostart = $false; "
            "if ($triggers.Count -gt 0) { $autostart = @($triggers | Where-Object { $_.Enabled }).Count -gt 0 }; "
            "[pscustomobject]@{ "
            "Installed = $true; "
            "State = [string]$task.State; "
            "LastTaskResult = [int]$info.LastTaskResult; "
            "AutostartEnabled = [bool]$autostart "
            "} | ConvertTo-Json -Compress"
        )

        try:
            result = self.controller.run_command(["powershell.exe", "-NoProfile", "-Command", command])
        except OSError as exc:
            return BackendStatus(
                installed=False,
                active=False,
                manager_state="error",
                detail=f"Failed to query scheduled task '{self.metadata.service_name}': {exc}",
            )

        if result.returncode != 0:
            return BackendStatus(
                installed=False,
                active=False,
                manager_state="error",
                detail=(
                    f"Failed to query scheduled task '{self.metadata.service_name}': "
                    f"{self._command_error('status', result)}"
                ),
            )

        payload = self.controller.parse_json_payload(result.stdout)
        if payload is None:
            return BackendStatus(
                installed=False,
                active=False,
                manager_state="error",
                detail=f"Scheduled task '{self.metadata.service_name}' returned unreadable status output.",
            )

        installed = bool(payload.get("Installed"))
        raw_state = str(payload.get("State", "Unknown"))
        normalized_state = raw_state.lower()
        autostart_raw = payload.get("AutostartEnabled")
        autostart_enabled = autostart_raw if isinstance(autostart_raw, bool) else None
        if not installed:
            return BackendStatus(
                installed=False,
                active=False,
                manager_state="not_installed",
                detail=f"Scheduled task '{self.metadata.service_name}' is not installed.",
                autostart_enabled=None,
            )

        active = normalized_state in {"running", "queued"}
        detail = f"Scheduled task '{self.metadata.service_name}' state: {raw_state}."
        last_task_result = payload.get("LastTaskResult")
        if not active and last_task_result not in {None, 0, "0"}:
            detail = f"{detail} LastTaskResult={last_task_result}."

        return BackendStatus(
            installed=True,
            active=active,
            manager_state=normalized_state,
            detail=detail,
            autostart_enabled=autostart_enabled,
        )

    def start(self) -> str:
        """Start the scheduled task."""
        return self._invoke("start", f"Start-ScheduledTask -TaskName '{self.controller.quote_powershell_literal(self.metadata.service_name)}' -ErrorAction Stop")

    def stop(self) -> str:
        """Stop the scheduled task."""
        return self._invoke("stop", f"Stop-ScheduledTask -TaskName '{self.controller.quote_powershell_literal(self.metadata.service_name)}' -ErrorAction Stop")

    def restart(self) -> str:
        """Restart the scheduled task by stopping then starting it."""
        status = self.get_status()
        if not status.installed:
            return status.detail

        messages: list[str] = []
        if status.active:
            stop_message = self.stop()
            if stop_message.startswith("Failed to"):
                return stop_message
            messages.append(stop_message)

        start_message = self.start()
        if start_message.startswith("Failed to"):
            return start_message

        messages.append(start_message)
        return "\n".join(messages)

    def set_autostart(self, enabled: bool) -> str:
        """Enable or disable scheduled-task triggers for auto-start behavior."""
        status = self.get_status()
        if not status.installed:
            return status.detail

        safe_name = self.controller.quote_powershell_literal(self.metadata.service_name)
        enabled_literal = "$true" if enabled else "$false"
        state_word = "enabled" if enabled else "disabled"
        command = (
            f"$task = Get-ScheduledTask -TaskName '{safe_name}' -ErrorAction Stop; "
            "$triggers = @($task.Triggers); "
            "if ($triggers.Count -eq 0) { throw 'Scheduled task has no triggers to toggle.' }; "
            f"foreach ($trigger in $triggers) {{ $trigger.Enabled = {enabled_literal} }}; "
            f"Set-ScheduledTask -TaskName '{safe_name}' -Trigger $triggers -ErrorAction Stop | Out-Null"
        )
        return self._invoke(
            action=f"set auto-start {state_word}",
            task_command=command,
            success_message=f"Auto-start {state_word} for scheduled task '{self.metadata.service_name}'.",
        )

    def _invoke(self, action: str, task_command: str, success_message: str | None = None) -> str:
        """Run a task-scheduler action and return a user-facing message."""
        command = f"try {{ {task_command}; Write-Output 'ok' }} catch {{ Write-Error $_; exit 1 }}"
        try:
            result = self.controller.run_command(["powershell.exe", "-NoProfile", "-Command", command])
        except OSError as exc:
            return f"Failed to {action} scheduled task '{self.metadata.service_name}': {exc}"

        if result.returncode != 0:
            return (
                f"Failed to {action} scheduled task '{self.metadata.service_name}': "
                f"{self._command_error(action, result)}"
            )

        self.controller.logger.info(
            "%s requested for scheduled task '%s'.",
            action.capitalize(),
            self.metadata.service_name,
        )
        if success_message is not None:
            return success_message
        return f"{action.capitalize()} requested for scheduled task '{self.metadata.service_name}'."


class LinuxSystemdBackend(BaseServiceBackend):
    """Manage the installed service through systemd."""

    def get_status(self) -> BackendStatus:
        """Inspect the systemd unit state."""
        command = [
            "systemctl",
            "show",
            self.metadata.service_name,
            "--no-pager",
            "--property",
            "LoadState",
            "--property",
            "ActiveState",
            "--property",
            "SubState",
            "--property",
            "UnitFileState",
            "--property",
            "Id",
        ]

        try:
            result = self.controller.run_command(command)
        except OSError as exc:
            return BackendStatus(
                installed=False,
                active=False,
                manager_state="error",
                detail=f"Failed to query systemd unit '{self.metadata.service_name}': {exc}",
            )

        payload = self.controller.parse_key_value_payload(result.stdout)
        load_state = payload.get("LoadState", "")
        active_state = payload.get("ActiveState", "")
        sub_state = payload.get("SubState", "")
        unit_file_state = payload.get("UnitFileState", "")
        autostart_enabled = self._parse_autostart_enabled(unit_file_state)

        if result.returncode != 0 and not payload:
            return BackendStatus(
                installed=False,
                active=False,
                manager_state="error",
                detail=(
                    f"Failed to query systemd unit '{self.metadata.service_name}': "
                    f"{self._command_error('status', result)}"
                ),
            )

        if load_state == "not-found" or not load_state:
            return BackendStatus(
                installed=False,
                active=False,
                manager_state="not_installed",
                detail=f"systemd unit '{self.metadata.service_name}' is not installed.",
                autostart_enabled=None,
            )

        active = active_state in {"active", "activating", "reloading"}
        detail = f"systemd unit '{self.metadata.service_name}' state: {active_state or 'unknown'}"
        if sub_state:
            detail = f"{detail}/{sub_state}."
        else:
            detail = f"{detail}."

        return BackendStatus(
            installed=True,
            active=active,
            manager_state=active_state or "unknown",
            detail=detail,
            autostart_enabled=autostart_enabled,
        )

    def start(self) -> str:
        """Start the systemd unit."""
        return self._invoke("start")

    def stop(self) -> str:
        """Stop the systemd unit."""
        return self._invoke("stop")

    def restart(self) -> str:
        """Restart the systemd unit."""
        return self._invoke("restart")

    def set_autostart(self, enabled: bool) -> str:
        """Enable or disable boot-time startup for the systemd unit."""
        action = "enable" if enabled else "disable"
        command = ["systemctl", action, self.metadata.service_name]
        try:
            result = self.controller.run_command(command)
        except OSError as exc:
            return f"Failed to {action} auto-start for systemd unit '{self.metadata.service_name}': {exc}"

        if result.returncode != 0:
            return (
                f"Failed to {action} auto-start for systemd unit '{self.metadata.service_name}': "
                f"{self._command_error(action, result)}"
            )

        state_word = "enabled" if enabled else "disabled"
        self.controller.logger.info(
            "Auto-start %s for systemd unit '%s'.",
            state_word,
            self.metadata.service_name,
        )
        return f"Auto-start {state_word} for systemd unit '{self.metadata.service_name}'."

    @staticmethod
    def _parse_autostart_enabled(unit_file_state: str) -> bool | None:
        """Interpret systemd UnitFileState as auto-start enabled/disabled."""
        normalized = unit_file_state.strip().lower()
        if normalized.startswith("enabled"):
            return True
        if normalized.startswith("disabled"):
            return False
        return None

    def _invoke(self, action: str) -> str:
        """Run a systemd action and return a user-facing message."""
        command = ["systemctl", action, self.metadata.service_name]
        try:
            result = self.controller.run_command(command)
        except OSError as exc:
            return f"Failed to {action} systemd unit '{self.metadata.service_name}': {exc}"

        if result.returncode != 0:
            return (
                f"Failed to {action} systemd unit '{self.metadata.service_name}': "
                f"{self._command_error(action, result)}"
            )

        self.controller.logger.info(
            "%s requested for systemd unit '%s'.",
            action.capitalize(),
            self.metadata.service_name,
        )
        return f"{action.capitalize()} requested for systemd unit '{self.metadata.service_name}'."


class ServiceController:
    """Manage the installed service and runtime configuration."""

    def __init__(
        self,
        root_dir: Path = ROOT_DIR,
        env_file: Path = DEFAULT_ENV_FILE,
        state_file: Path = DEFAULT_STATE_FILE,
        log_file: Path = DEFAULT_LOG_FILE,
        python_executable: str | None = None,
        platform_name: str | None = None,
        install_metadata_file: Path = DEFAULT_INSTALL_METADATA_FILE,
    ) -> None:
        """Initialize the controller.

        Args:
            root_dir: Project root used for relative paths and defaults.
            env_file: Environment file used for runtime settings persistence.
            state_file: Legacy state path kept for backward-compatible construction.
            log_file: Log file used for service-manager control events.
            python_executable: Interpreter associated with the active install.
            platform_name: Optional platform override for testing.
            install_metadata_file: Installer metadata file describing the service target.
        """
        self.root_dir = root_dir
        self.env_file = env_file
        self.state_file = state_file
        self.log_file = log_file
        self.python_executable = python_executable or sys.executable
        self.platform_name = platform_name or os.name
        self.install_metadata_file = install_metadata_file
        self.logger = self._build_logger()

    def _build_logger(self) -> logging.Logger:
        """Create a dedicated file logger for service-manager events."""
        logger_name = f"service_manager.{self.log_file.resolve()}"
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.INFO)
        logger.propagate = False

        if not logger.handlers:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            handler = logging.FileHandler(self.log_file, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
            logger.addHandler(handler)

        return logger

    def load_settings(self) -> Settings:
        """Load the latest runtime settings from the environment file."""
        return get_settings(self.env_file)

    def load_settings_safe(self) -> tuple[Settings | None, str | None]:
        """Load settings and return `(settings, error)` for resilient diagnostics."""
        try:
            return self.load_settings(), None
        except Exception as exc:  # pragma: no cover - defensive path
            return None, str(exc)

    def get_service_status(self) -> ServiceStatus:
        """Inspect current runtime state and health information."""
        settings = self.load_settings()
        metadata = self.get_install_metadata()
        backend = self.create_backend(metadata)
        backend_status = backend.get_status()

        state = ServiceState(
            backend=metadata.backend,
            service_name=metadata.service_name,
            runtime=metadata.runtime,
            manager_state=backend_status.manager_state,
            autostart_enabled=backend_status.autostart_enabled,
            install_mode=metadata.install_mode,
            log_file=str(self.log_file),
        )

        if not backend_status.installed:
            return ServiceStatus(
                status="not installed",
                configured_runtime=metadata.runtime,
                configured_host=settings.APP_HOST,
                configured_port=settings.APP_PORT,
                configured_workers=settings.APP_WORKERS,
                pid=None,
                pid_alive=False,
                health_ok=False,
                health_url=None,
                active_state=state,
                detail=backend_status.detail,
            )

        if not backend_status.active:
            return ServiceStatus(
                status="stopped",
                configured_runtime=metadata.runtime,
                configured_host=settings.APP_HOST,
                configured_port=settings.APP_PORT,
                configured_workers=settings.APP_WORKERS,
                pid=backend_status.pid,
                pid_alive=False,
                health_ok=False,
                health_url=None,
                active_state=state,
                detail=backend_status.detail,
            )

        health_url = self.build_health_url(settings.APP_HOST, settings.APP_PORT)
        health_ok = self.check_health(settings.APP_HOST, settings.APP_PORT)
        status = "running" if health_ok else "degraded"
        detail = backend_status.detail if health_ok else (
            f"{backend_status.detail} Health endpoint {health_url} did not respond successfully."
        )

        return ServiceStatus(
            status=status,
            configured_runtime=metadata.runtime,
            configured_host=settings.APP_HOST,
            configured_port=settings.APP_PORT,
            configured_workers=settings.APP_WORKERS,
            pid=backend_status.pid,
            pid_alive=True,
            health_ok=health_ok,
            health_url=health_url,
            active_state=state,
            detail=detail,
        )

    def start_service(self) -> str:
        """Start the managed service if it is not already running."""
        status = self.get_service_status()
        if status.status == "not installed":
            self.logger.warning("Start requested but the managed service is not installed.")
            return status.detail
        if status.pid_alive:
            self.logger.info("Start skipped because service is already active.")
            return "Service is already running."

        return self.create_backend(self.get_install_metadata()).start()

    def stop_service(self) -> str:
        """Stop the managed service if it is running."""
        status = self.get_service_status()
        if status.status == "not installed":
            self.logger.warning("Stop requested but the managed service is not installed.")
            return status.detail
        if not status.pid_alive:
            self.logger.info("Stop skipped because service is already stopped.")
            return "Service is already stopped."

        return self.create_backend(self.get_install_metadata()).stop()

    def restart_service(self) -> str:
        """Restart the managed service using the latest persisted configuration."""
        status = self.get_service_status()
        if status.status == "not installed":
            self.logger.warning("Restart requested but the managed service is not installed.")
            return status.detail

        self.logger.info("Restart requested.")
        return self.create_backend(self.get_install_metadata()).restart()

    def set_autostart(self, enabled: bool) -> str:
        """Enable or disable managed-service auto-start."""
        status = self.get_service_status()
        if status.status == "not installed":
            self.logger.warning("Auto-start update requested but the managed service is not installed.")
            return status.detail

        self.logger.info("Auto-start update requested: %s.", "enabled" if enabled else "disabled")
        return self.create_backend(self.get_install_metadata()).set_autostart(enabled)

    def enable_autostart(self) -> str:
        """Enable auto-start for the managed service."""
        return self.set_autostart(True)

    def disable_autostart(self) -> str:
        """Disable auto-start for the managed service."""
        return self.set_autostart(False)

    def update_host(self, raw_value: str) -> str:
        """Validate and persist a new host value."""
        normalized_host = self.validate_host(raw_value)
        self.persist_env_value("APP_HOST", normalized_host)
        self.logger.info("Updated APP_HOST to %s.", normalized_host)
        return f"Host updated to {normalized_host}."

    def update_port(self, raw_value: str) -> str:
        """Validate and persist a new port value."""
        normalized_port = self.validate_port(raw_value)
        self.persist_env_value("APP_PORT", str(normalized_port))
        self.logger.info("Updated APP_PORT to %s.", normalized_port)
        return f"Port updated to {normalized_port}."

    def update_workers(self, raw_value: str) -> str:
        """Validate and persist a new worker count."""
        normalized_workers = self.validate_workers(raw_value)
        self.persist_env_value("APP_WORKERS", str(normalized_workers))
        self.logger.info("Updated APP_WORKERS to %s.", normalized_workers)
        return f"Workers updated to {normalized_workers}."

    def load_install_metadata(self) -> tuple[InstallMetadata, bool, str | None]:
        """Load metadata and return `(metadata, from_file, error)`."""
        if self.install_metadata_file.exists():
            try:
                payload = json.loads(self.install_metadata_file.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError) as exc:
                return self.default_install_metadata(), False, str(exc)

            if isinstance(payload, dict):
                backend = str(payload.get("backend", "")).strip()
                service_name = str(payload.get("service_name", "")).strip()
                runtime = str(payload.get("runtime", "")).strip()
                install_mode = str(payload.get("install_mode", "service")).strip() or "service"
                if backend and service_name and runtime:
                    return (
                        InstallMetadata(
                            backend=backend,
                            service_name=service_name,
                            runtime=runtime,
                            install_mode=install_mode,
                        ),
                        True,
                        None,
                    )
                return self.default_install_metadata(), False, "Missing required metadata keys."

            return self.default_install_metadata(), False, "Metadata payload is not a JSON object."

        return self.default_install_metadata(), False, None

    def get_install_metadata(self) -> InstallMetadata:
        """Load installer metadata, or fall back to platform defaults."""
        metadata, _, _ = self.load_install_metadata()
        return metadata

    def save_install_metadata(self, metadata: InstallMetadata) -> None:
        """Persist installer metadata for later terminal sessions."""
        self.install_metadata_file.parent.mkdir(parents=True, exist_ok=True)
        self.install_metadata_file.write_text(
            json.dumps(asdict(metadata), indent=2) + "\n",
            encoding="utf-8",
        )

    def default_install_metadata(self) -> InstallMetadata:
        """Return the default installed service target for the current platform."""
        if self.is_windows_platform():
            return InstallMetadata(
                backend=WINDOWS_BACKEND,
                service_name=DEFAULT_WINDOWS_TASK_NAME,
                runtime="uvicorn",
            )

        return InstallMetadata(
            backend=LINUX_BACKEND,
            service_name=DEFAULT_LINUX_UNIT_NAME,
            runtime="gunicorn",
        )

    def create_backend(self, metadata: InstallMetadata | None = None) -> BaseServiceBackend:
        """Build the service-manager backend for the active installation."""
        metadata = metadata or self.get_install_metadata()
        if metadata.backend == WINDOWS_BACKEND:
            return WindowsScheduledTaskBackend(self, metadata)
        if metadata.backend == LINUX_BACKEND:
            return LinuxSystemdBackend(self, metadata)

        raise ValueError(f"Unsupported service backend: {metadata.backend}")

    def get_runtime_name(self) -> str:
        """Return the server runtime for the current installation."""
        return self.get_install_metadata().runtime

    def get_runtime_display_name(self) -> str:
        """Return a human-friendly runtime name."""
        return self.get_runtime_name().capitalize()

    def get_backend_display_name(self) -> str:
        """Return a human-friendly backend name."""
        metadata = self.get_install_metadata()
        if metadata.backend == WINDOWS_BACKEND:
            return "Scheduled Task"
        if metadata.backend == LINUX_BACKEND:
            return "systemd"
        return metadata.backend

    def is_windows_platform(self) -> bool:
        """Return whether the controller is running on Windows."""
        return self.platform_name == "nt"

    def persist_env_value(self, key: str, value: str) -> None:
        """Update or append a key in the environment file."""
        if self.env_file.exists():
            lines = self.env_file.read_text(encoding="utf-8").splitlines()
        else:
            lines = []

        updated_lines: list[str] = []
        replaced = False
        for line in lines:
            if line.startswith(f"{key}="):
                updated_lines.append(f"{key}={value}")
                replaced = True
            else:
                updated_lines.append(line)

        if not replaced:
            if updated_lines and updated_lines[-1] != "":
                updated_lines.append("")
            updated_lines.append(f"{key}={value}")

        self.env_file.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")

    def run_command(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        """Execute a subprocess command and capture text output."""
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            check=False,
        )

    def read_env_values(self) -> dict[str, str]:
        """Parse `.env` into a dictionary without validation."""
        payload: dict[str, str] = {}
        if not self.env_file.exists():
            return payload

        for line in self.env_file.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            payload[key.strip()] = value.strip()
        return payload

    def resolve_path(self, raw_path: str) -> Path:
        """Resolve an absolute or project-relative path."""
        path = Path(raw_path)
        if path.is_absolute():
            return path
        return self.root_dir / path

    def resolve_log_dir(self, settings: Settings | None = None, env_values: dict[str, str] | None = None) -> Path:
        """Resolve configured LOG_DIR with fallback."""
        if settings is not None:
            return self.resolve_path(settings.LOG_DIR)
        values = env_values or self.read_env_values()
        return self.resolve_path(values.get("LOG_DIR", "logs"))

    def find_preferred_app_log(self, log_dir: Path) -> Path | None:
        """Return today's date log or the latest date-stamped app log."""
        today_name = f"{datetime.now().strftime('%Y-%m-%d')}.log"
        today_path = log_dir / today_name
        if today_path.exists():
            return today_path

        if not log_dir.exists():
            return None

        date_logs = sorted(
            [path for path in log_dir.iterdir() if path.is_file() and DATE_LOG_PATTERN.match(path.name)],
            reverse=True,
        )
        return date_logs[0] if date_logs else None

    def is_journalctl_available(self) -> bool:
        """Return whether `journalctl` is available."""
        try:
            result = self.run_command(["journalctl", "--version"])
        except OSError:
            return False
        return result.returncode == 0

    def tail_file_lines(self, path: Path, lines: int) -> str:
        """Read the last `lines` lines from a UTF-8 text file."""
        line_count = max(1, min(lines, MAX_LOG_LINES))
        if not path.exists():
            return f"Log file not found: {path}"

        text = path.read_text(encoding="utf-8", errors="replace")
        payload = text.splitlines()
        if not payload:
            return f"{path.name} is empty."
        return "\n".join(payload[-line_count:])

    def list_log_sources(self) -> list[LogSource]:
        """List log sources for dashboard log viewing."""
        metadata = self.get_install_metadata()
        settings, _ = self.load_settings_safe()
        env_values = self.read_env_values()
        log_dir = self.resolve_log_dir(settings=settings, env_values=env_values)
        app_log_path = self.find_preferred_app_log(log_dir)
        sources: list[LogSource] = []

        if app_log_path is None:
            expected_today = log_dir / f"{datetime.now().strftime('%Y-%m-%d')}.log"
            sources.append(
                LogSource(
                    source_id="app_log",
                    label="Application log",
                    available=False,
                    backend="common",
                    kind="file",
                    descriptor=str(expected_today),
                )
            )
        else:
            sources.append(
                LogSource(
                    source_id="app_log",
                    label="Application log",
                    available=True,
                    backend="common",
                    kind="file",
                    descriptor=str(app_log_path),
                )
            )

        manager_log_path = log_dir / "service_manager.log"
        sources.append(
            LogSource(
                source_id="service_manager_log",
                label="Service manager log",
                available=manager_log_path.exists(),
                backend="common",
                kind="file",
                descriptor=str(manager_log_path),
            )
        )

        if metadata.backend == WINDOWS_BACKEND:
            stdout_path = log_dir / "scheduled_task.stdout.log"
            stderr_path = log_dir / "scheduled_task.stderr.log"
            sources.append(
                LogSource(
                    source_id="windows_stdout",
                    label="Scheduled task stdout",
                    available=stdout_path.exists(),
                    backend=WINDOWS_BACKEND,
                    kind="file",
                    descriptor=str(stdout_path),
                )
            )
            sources.append(
                LogSource(
                    source_id="windows_stderr",
                    label="Scheduled task stderr",
                    available=stderr_path.exists(),
                    backend=WINDOWS_BACKEND,
                    kind="file",
                    descriptor=str(stderr_path),
                )
            )

        if metadata.backend == LINUX_BACKEND:
            sources.append(
                LogSource(
                    source_id="linux_journal",
                    label="systemd journal",
                    available=self.is_journalctl_available(),
                    backend=LINUX_BACKEND,
                    kind="journal",
                    descriptor=f"journalctl -u {metadata.service_name} -n <lines> --no-pager",
                )
            )

        return sources

    def read_log_source(self, source_id: str, lines: int = DEFAULT_LOG_LINES) -> str:
        """Read and return logs for a source id."""
        if not source_id:
            return "No log source selected."

        line_count = max(1, min(lines, MAX_LOG_LINES))
        source_map = {source.source_id: source for source in self.list_log_sources()}
        source = source_map.get(source_id)
        if source is None:
            return f"Unknown log source: {source_id}"

        if source.kind == "file":
            return self.tail_file_lines(Path(source.descriptor), line_count)

        if source.kind == "journal":
            metadata = self.get_install_metadata()
            if not self.is_journalctl_available():
                return (
                    "journalctl is unavailable on this system. "
                    "Install or enable systemd-journald to view service logs."
                )

            command = [
                "journalctl",
                "-u",
                metadata.service_name,
                "-n",
                str(line_count),
                "--no-pager",
                "-o",
                "short-iso",
            ]
            try:
                result = self.run_command(command)
            except OSError as exc:
                return f"Failed to run journalctl: {exc}"

            if result.returncode != 0:
                error_output = (result.stderr or result.stdout or "").strip()
                return f"journalctl failed: {error_output or 'unknown error'}"

            payload = result.stdout.strip()
            return payload if payload else "No journal entries available."

        return f"Unsupported log source kind: {source.kind}"

    @staticmethod
    def quote_powershell_literal(value: str) -> str:
        """Quote a PowerShell single-quoted literal."""
        return value.replace("'", "''")

    @staticmethod
    def parse_json_payload(output: str) -> dict[str, object] | None:
        """Parse a JSON object payload."""
        text = output.strip()
        if not text:
            return None

        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return None

        if not isinstance(payload, dict):
            return None
        return payload

    @staticmethod
    def parse_key_value_payload(output: str) -> dict[str, str]:
        """Parse `key=value` lines into a dictionary."""
        payload: dict[str, str] = {}
        for line in output.splitlines():
            stripped_line = line.strip()
            if not stripped_line or "=" not in stripped_line:
                continue
            key, value = stripped_line.split("=", 1)
            payload[key] = value
        return payload

    def build_health_url(self, host: str, port: int) -> str:
        """Build the health endpoint URL for a bound host/port pair."""
        health_host = self.resolve_health_host(host)
        return f"http://{health_host}:{port}/health"

    def resolve_health_host(self, host: str) -> str:
        """Resolve a bind host to a connectable local health-check host."""
        if host in {"0.0.0.0", "::", ""}:
            return "127.0.0.1"
        return host

    def check_health(self, host: str, port: int) -> bool:
        """Call the FastAPI health endpoint."""
        url = self.build_health_url(host, port)
        try:
            with urllib.request.urlopen(url, timeout=2.0) as response:
                return response.status == 200
        except (urllib.error.URLError, TimeoutError, ValueError):
            return False

    def run_diagnostics(self) -> list[DiagnosticResult]:
        """Run read-only troubleshooting checks."""
        results: list[DiagnosticResult] = []
        settings, settings_error = self.load_settings_safe()
        env_values = self.read_env_values()
        metadata, metadata_from_file, metadata_error = self.load_install_metadata()

        if metadata_from_file:
            results.append(
                DiagnosticResult(
                    check_name="Install metadata",
                    status="pass",
                    summary="Install metadata is valid.",
                    detail=f"Loaded {self.install_metadata_file}.",
                    remediation="No action required.",
                )
            )
        else:
            status = "warn" if metadata_error is None else "fail"
            summary = (
                "Install metadata missing. Using platform defaults."
                if metadata_error is None
                else "Install metadata is invalid."
            )
            detail = (
                f"{self.install_metadata_file} not found."
                if metadata_error is None
                else f"Could not parse {self.install_metadata_file}: {metadata_error}"
            )
            results.append(
                DiagnosticResult(
                    check_name="Install metadata",
                    status=status,
                    summary=summary,
                    detail=detail,
                    remediation=(
                        "Re-run install.ps1/install.sh to regenerate metadata."
                        if status == "fail"
                        else "Run installer to persist backend metadata explicitly."
                    ),
                )
            )

        try:
            backend = self.create_backend(metadata)
            backend_status = backend.get_status()
            if backend_status.manager_state == "error":
                results.append(
                    DiagnosticResult(
                        check_name="Service manager backend",
                        status="fail",
                        summary="Backend query failed.",
                        detail=backend_status.detail,
                        remediation="Verify service manager permissions and backend tooling.",
                    )
                )
            else:
                results.append(
                    DiagnosticResult(
                        check_name="Service manager backend",
                        status="pass",
                        summary="Backend query succeeded.",
                        detail=backend_status.detail,
                        remediation="No action required.",
                    )
                )
        except Exception as exc:
            backend_status = None
            results.append(
                DiagnosticResult(
                    check_name="Service manager backend",
                    status="fail",
                    summary="Backend initialization failed.",
                    detail=str(exc),
                    remediation="Check install metadata backend value and platform support.",
                )
            )

        if backend_status is None:
            results.append(
                DiagnosticResult(
                    check_name="Service state consistency",
                    status="fail",
                    summary="Service state could not be evaluated.",
                    detail="Backend status is unavailable.",
                    remediation="Fix backend query errors first.",
                )
            )
        elif not backend_status.installed:
            results.append(
                DiagnosticResult(
                    check_name="Service state consistency",
                    status="warn",
                    summary="Managed service is not installed.",
                    detail=backend_status.detail,
                    remediation="Run install.ps1/install.sh before managing service actions.",
                )
            )
        elif backend_status.active:
            if settings is None:
                results.append(
                    DiagnosticResult(
                        check_name="Service state consistency",
                        status="warn",
                        summary="Service appears active, but settings are invalid.",
                        detail=settings_error or "Settings load failed.",
                        remediation="Fix .env values so health checks can run.",
                    )
                )
            else:
                health_ok = self.check_health(settings.APP_HOST, settings.APP_PORT)
                if health_ok:
                    results.append(
                        DiagnosticResult(
                            check_name="Service state consistency",
                            status="pass",
                            summary="Service is active and health endpoint responds.",
                            detail=backend_status.detail,
                            remediation="No action required.",
                        )
                    )
                else:
                    results.append(
                        DiagnosticResult(
                            check_name="Service state consistency",
                            status="warn",
                            summary="Service is active but health endpoint failed.",
                            detail=(
                                f"{backend_status.detail} "
                                f"Health URL: {self.build_health_url(settings.APP_HOST, settings.APP_PORT)}"
                            ),
                            remediation="Inspect runtime logs and verify bind host/port settings.",
                        )
                    )
        else:
            results.append(
                DiagnosticResult(
                    check_name="Service state consistency",
                    status="warn",
                    summary="Managed service is installed but currently stopped.",
                    detail=backend_status.detail,
                    remediation="Start the service if runtime availability is expected.",
                )
            )

        if settings is None:
            results.append(
                DiagnosticResult(
                    check_name="Health endpoint",
                    status="fail",
                    summary="Health check skipped due invalid settings.",
                    detail=settings_error or "Settings could not be loaded.",
                    remediation="Fix required .env values and retry diagnostics.",
                )
            )
        else:
            health_ok = self.check_health(settings.APP_HOST, settings.APP_PORT)
            health_url = self.build_health_url(settings.APP_HOST, settings.APP_PORT)
            results.append(
                DiagnosticResult(
                    check_name="Health endpoint",
                    status="pass" if health_ok else "fail",
                    summary="Health endpoint reachable." if health_ok else "Health endpoint unreachable.",
                    detail=health_url,
                    remediation=(
                        "No action required."
                        if health_ok
                        else "Ensure service is running and APP_HOST/APP_PORT are correct."
                    ),
                )
            )

        missing_keys = [key for key in REQUIRED_ENV_KEYS if not env_values.get(key)]
        env_errors: list[str] = []
        if not missing_keys:
            try:
                self.validate_host(env_values["APP_HOST"])
            except ValueError as exc:
                env_errors.append(str(exc))
            try:
                self.validate_port(env_values["APP_PORT"])
            except ValueError as exc:
                env_errors.append(str(exc))
            try:
                self.validate_workers(env_values["APP_WORKERS"])
            except ValueError as exc:
                env_errors.append(str(exc))

        if missing_keys:
            results.append(
                DiagnosticResult(
                    check_name=".env required keys",
                    status="fail",
                    summary="Required keys are missing in .env.",
                    detail=", ".join(missing_keys),
                    remediation="Add missing keys to .env and rerun diagnostics.",
                )
            )
        elif env_errors:
            results.append(
                DiagnosticResult(
                    check_name=".env required keys",
                    status="fail",
                    summary="Required keys exist but contain invalid values.",
                    detail="; ".join(env_errors),
                    remediation="Correct APP_HOST/APP_PORT/APP_WORKERS values in .env.",
                )
            )
        else:
            results.append(
                DiagnosticResult(
                    check_name=".env required keys",
                    status="pass",
                    summary="Required .env keys are present and valid.",
                    detail=", ".join(REQUIRED_ENV_KEYS),
                    remediation="No action required.",
                )
            )

        model_path_value = ""
        if settings is not None:
            model_path_value = settings.MODEL_PATH
        elif env_values.get("MODEL_PATH"):
            model_path_value = env_values["MODEL_PATH"]

        if not model_path_value:
            results.append(
                DiagnosticResult(
                    check_name="Model files",
                    status="fail",
                    summary="MODEL_PATH is not configured.",
                    detail="MODEL_PATH is missing from settings/.env.",
                    remediation="Set MODEL_PATH in .env to the model directory.",
                )
            )
        else:
            model_dir = self.resolve_path(model_path_value)
            if not model_dir.exists():
                results.append(
                    DiagnosticResult(
                        check_name="Model files",
                        status="fail",
                        summary="Model directory does not exist.",
                        detail=str(model_dir),
                        remediation="Download/copy the Sherpa-onnx model to MODEL_PATH.",
                    )
                )
            else:
                missing_files = [name for name in MODEL_REQUIRED_FILES if not (model_dir / name).exists()]
                if missing_files:
                    results.append(
                        DiagnosticResult(
                            check_name="Model files",
                            status="fail",
                            summary="Model directory is missing required files.",
                            detail=", ".join(missing_files),
                            remediation="Ensure encoder.int8.onnx, decoder.int8.onnx, and tokens.txt exist.",
                        )
                    )
                else:
                    results.append(
                        DiagnosticResult(
                            check_name="Model files",
                            status="pass",
                            summary="Required model files are present.",
                            detail=str(model_dir),
                            remediation="No action required.",
                        )
                    )

        log_dir = self.resolve_log_dir(settings=settings, env_values=env_values)
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            probe_path = log_dir / ".write_probe.tmp"
            probe_path.write_text("ok", encoding="utf-8")
            probe_path.unlink(missing_ok=True)
            results.append(
                DiagnosticResult(
                    check_name="Log directory",
                    status="pass",
                    summary="Log directory is accessible and writable.",
                    detail=str(log_dir),
                    remediation="No action required.",
                )
            )
        except OSError as exc:
            results.append(
                DiagnosticResult(
                    check_name="Log directory",
                    status="fail",
                    summary="Log directory is not writable.",
                    detail=f"{log_dir}: {exc}",
                    remediation="Fix directory permissions or update LOG_DIR.",
                )
            )

        sources = self.list_log_sources()
        if metadata.backend == WINDOWS_BACKEND:
            windows_sources = {source.source_id: source for source in sources if source.backend == WINDOWS_BACKEND}
            missing_windows = [
                source_id
                for source_id in ("windows_stdout", "windows_stderr")
                if source_id not in windows_sources or not windows_sources[source_id].available
            ]
            if missing_windows:
                results.append(
                    DiagnosticResult(
                        check_name="Backend-specific logs",
                        status="warn",
                        summary="Windows scheduled-task log files are partially unavailable.",
                        detail=", ".join(missing_windows),
                        remediation="Re-run install.ps1 or ensure scheduled task logs are created.",
                    )
                )
            else:
                results.append(
                    DiagnosticResult(
                        check_name="Backend-specific logs",
                        status="pass",
                        summary="Windows backend log files are available.",
                        detail="scheduled_task.stdout.log and scheduled_task.stderr.log detected.",
                        remediation="No action required.",
                    )
                )
        elif metadata.backend == LINUX_BACKEND:
            journal_source = next((source for source in sources if source.source_id == "linux_journal"), None)
            if journal_source is None or not journal_source.available:
                results.append(
                    DiagnosticResult(
                        check_name="Backend-specific logs",
                        status="warn",
                        summary="systemd journal logs are unavailable.",
                        detail="journalctl was not detected or cannot be queried.",
                        remediation="Install/enable journalctl or inspect systemd logs manually.",
                    )
                )
            else:
                results.append(
                    DiagnosticResult(
                        check_name="Backend-specific logs",
                        status="pass",
                        summary="systemd journal logs are available.",
                        detail=journal_source.descriptor,
                        remediation="No action required.",
                    )
                )

        return results

    @staticmethod
    def validate_host(raw_value: str) -> str:
        """Validate and normalize a host string."""
        normalized_host = raw_value.strip()
        if not normalized_host:
            raise ValueError("Host cannot be empty.")
        return normalized_host

    @staticmethod
    def validate_port(raw_value: str) -> int:
        """Validate and normalize a port value."""
        try:
            normalized_port = int(raw_value.strip())
        except ValueError as exc:
            raise ValueError("Port must be an integer.") from exc

        if not 1 <= normalized_port <= 65535:
            raise ValueError("Port must be between 1 and 65535.")

        return normalized_port

    @staticmethod
    def validate_workers(raw_value: str) -> int:
        """Validate and normalize a worker count."""
        try:
            normalized_workers = int(raw_value.strip())
        except ValueError as exc:
            raise ValueError("Workers must be an integer.") from exc

        if normalized_workers < 1:
            raise ValueError("Workers must be greater than or equal to 1.")

        return normalized_workers


def format_status(status: ServiceStatus) -> list[str]:
    """Format status information for display in the terminal manager."""
    lines = [
        f"Status: {status.status}",
        f"Configured runtime: {status.configured_runtime}",
        f"Configured host: {status.configured_host}",
        f"Configured port: {status.configured_port}",
        f"Configured workers: {status.configured_workers}",
    ]

    if status.active_state is not None:
        lines.extend(
            [
                f"Backend: {status.active_state.backend}",
                f"Service name: {status.active_state.service_name}",
                f"Install mode: {status.active_state.install_mode}",
                f"Manager state: {status.active_state.manager_state}",
                "Auto start: "
                + (
                    "enabled"
                    if status.active_state.autostart_enabled is True
                    else "disabled"
                    if status.active_state.autostart_enabled is False
                    else "unknown"
                ),
                f"Runtime: {status.active_state.runtime}",
            ]
        )

        if status.health_url is not None:
            lines.extend(
                [
                    f"Health URL: {status.health_url}",
                    f"Health check: {'ok' if status.health_ok else 'failed'}",
                ]
            )
        else:
            lines.append("Health URL: unavailable while service is stopped.")
    else:
        lines.append("Managed service: unknown")

    lines.append(f"Detail: {status.detail}")
    return lines


def main() -> None:
    """Entrypoint for the terminal service manager."""
    controller = ServiceController()
    from scripts.service_manager_cli import ServiceManagerCliApp

    ServiceManagerCliApp(controller).run()


if __name__ == "__main__":
    main()
