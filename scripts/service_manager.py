"""Terminal UI for managing the installed PyStreamASR service."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
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
MENU_OPTIONS = (
    "1. View Status",
    "2. Start",
    "3. Stop",
    "4. Restart",
    "5. Modify Host",
    "6. Modify Port",
    "7. Modify Workers",
    "0. Exit",
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


@dataclass(slots=True)
class ServiceState:
    """Information about the configured managed service target."""

    backend: str
    service_name: str
    runtime: str
    manager_state: str
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
            "[pscustomobject]@{ Installed = $false; State = 'NotInstalled'; LastTaskResult = 0 } "
            "| ConvertTo-Json -Compress; exit 0 }; "
            f"$info = Get-ScheduledTaskInfo -TaskName '{safe_name}'; "
            "[pscustomobject]@{ "
            "Installed = $true; "
            "State = [string]$task.State; "
            "LastTaskResult = [int]$info.LastTaskResult "
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
        if not installed:
            return BackendStatus(
                installed=False,
                active=False,
                manager_state="not_installed",
                detail=f"Scheduled task '{self.metadata.service_name}' is not installed.",
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

    def _invoke(self, action: str, task_command: str) -> str:
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

    def get_install_metadata(self) -> InstallMetadata:
        """Load installer metadata, or fall back to platform defaults."""
        if self.install_metadata_file.exists():
            try:
                payload = json.loads(self.install_metadata_file.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                payload = None

            if isinstance(payload, dict):
                backend = str(payload.get("backend", "")).strip()
                service_name = str(payload.get("service_name", "")).strip()
                runtime = str(payload.get("runtime", "")).strip()
                install_mode = str(payload.get("install_mode", "service")).strip() or "service"
                if backend and service_name and runtime:
                    return InstallMetadata(
                        backend=backend,
                        service_name=service_name,
                        runtime=runtime,
                        install_mode=install_mode,
                    )

        return self.default_install_metadata()

    def save_install_metadata(self, metadata: InstallMetadata) -> None:
        """Persist installer metadata for later TUI sessions."""
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


def clear_screen() -> None:
    """Clear the active terminal window."""
    os.system("cls" if os.name == "nt" else "clear")


def prompt_for_value(prompt: str) -> str:
    """Read a trimmed value from stdin."""
    return input(prompt).strip()


def format_status(status: ServiceStatus) -> list[str]:
    """Format status information for display in the menu."""
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


def run_menu(controller: ServiceController) -> None:
    """Run the interactive terminal menu."""
    message = (
        f"Service manager ready for {controller.get_backend_display_name()} "
        f"({controller.get_runtime_display_name()})."
    )

    while True:
        clear_screen()
        print(
            "PyStreamASR Service Manager "
            f"({controller.get_backend_display_name()} / {controller.get_runtime_display_name()})"
        )
        print("=" * 28)
        print()
        for line in format_status(controller.get_service_status()):
            print(line)
        print()
        for option in MENU_OPTIONS:
            print(option)
        print()
        print(message)
        print()

        choice = prompt_for_value("Select an option: ")

        if choice == "0":
            return
        if choice == "1":
            message = "Status refreshed."
        elif choice == "2":
            message = controller.start_service()
        elif choice == "3":
            message = controller.stop_service()
        elif choice == "4":
            message = controller.restart_service()
        elif choice == "5":
            try:
                message = controller.update_host(prompt_for_value("Enter new host: "))
            except ValueError as exc:
                message = str(exc)
        elif choice == "6":
            try:
                message = controller.update_port(prompt_for_value("Enter new port: "))
            except ValueError as exc:
                message = str(exc)
        elif choice == "7":
            try:
                message = controller.update_workers(prompt_for_value("Enter new workers: "))
            except ValueError as exc:
                message = str(exc)
        else:
            message = "Invalid selection. Choose a menu number."


def main() -> None:
    """Entrypoint for the terminal service manager."""
    controller = ServiceController()
    run_menu(controller)


if __name__ == "__main__":
    main()
