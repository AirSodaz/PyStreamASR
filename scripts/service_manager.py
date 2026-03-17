"""Terminal UI for managing the local PyStreamASR FastAPI service."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.config import Settings, get_settings


DEFAULT_ENV_FILE = ROOT_DIR / ".env"
DEFAULT_STATE_FILE = ROOT_DIR / "logs" / "service_state.json"
DEFAULT_LOG_FILE = ROOT_DIR / "logs" / "service_manager.log"
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
class ServiceState:
    """Persisted metadata for the managed Uvicorn process."""

    pid: int
    host: str
    port: int
    workers: int
    started_at: str
    launch_command: list[str]
    process_creation_date: str | None = None
    log_file: str | None = None


@dataclass(slots=True)
class ServiceStatus:
    """Current status information for the managed service."""

    status: str
    configured_host: str
    configured_port: int
    configured_workers: int
    pid: int | None
    pid_alive: bool
    health_ok: bool
    health_url: str | None
    active_state: ServiceState | None
    detail: str


class ServiceController:
    """Manage the local Uvicorn process and persisted runtime configuration."""

    def __init__(
        self,
        root_dir: Path = ROOT_DIR,
        env_file: Path = DEFAULT_ENV_FILE,
        state_file: Path = DEFAULT_STATE_FILE,
        log_file: Path = DEFAULT_LOG_FILE,
        python_executable: str | None = None,
    ) -> None:
        """Initialize the controller.

        Args:
            root_dir: Project root used as the subprocess working directory.
            env_file: Environment file used for runtime settings persistence.
            state_file: JSON file storing the managed process metadata.
            log_file: Log file capturing the managed service stdout/stderr.
            python_executable: Interpreter used to launch Uvicorn.
        """
        self.root_dir = root_dir
        self.env_file = env_file
        self.state_file = state_file
        self.log_file = log_file
        self.python_executable = python_executable or sys.executable

    def load_settings(self) -> Settings:
        """Load the latest runtime settings from the environment file."""
        return get_settings(self.env_file)

    def get_service_status(self) -> ServiceStatus:
        """Inspect current runtime state and health information."""
        settings = self.load_settings()
        state = self.load_state()

        if state is None:
            return ServiceStatus(
                status="stopped",
                configured_host=settings.APP_HOST,
                configured_port=settings.APP_PORT,
                configured_workers=settings.APP_WORKERS,
                pid=None,
                pid_alive=False,
                health_ok=False,
                health_url=None,
                active_state=None,
                detail="Service is not running.",
            )

        if not self.is_pid_running(state.pid):
            self.clear_state()
            return ServiceStatus(
                status="stopped",
                configured_host=settings.APP_HOST,
                configured_port=settings.APP_PORT,
                configured_workers=settings.APP_WORKERS,
                pid=None,
                pid_alive=False,
                health_ok=False,
                health_url=None,
                active_state=None,
                detail="Saved service state was stale and has been cleared.",
            )

        if not self.is_managed_process(state):
            self.clear_state()
            return ServiceStatus(
                status="stopped",
                configured_host=settings.APP_HOST,
                configured_port=settings.APP_PORT,
                configured_workers=settings.APP_WORKERS,
                pid=None,
                pid_alive=False,
                health_ok=False,
                health_url=None,
                active_state=None,
                detail="Saved PID no longer belongs to the managed Uvicorn process.",
            )

        health_url = self.build_health_url(state.host, state.port)
        health_ok = self.check_health(state.host, state.port)
        status = "running" if health_ok else "degraded"
        detail = "Service is healthy." if health_ok else "Process is running but /health did not respond successfully."

        return ServiceStatus(
            status=status,
            configured_host=settings.APP_HOST,
            configured_port=settings.APP_PORT,
            configured_workers=settings.APP_WORKERS,
            pid=state.pid,
            pid_alive=True,
            health_ok=health_ok,
            health_url=health_url,
            active_state=state,
            detail=detail,
        )

    def start_service(self) -> str:
        """Start the managed Uvicorn service if it is not already running."""
        status = self.get_service_status()
        if status.pid_alive:
            return f"Service is already running with PID {status.pid}."

        settings = self.load_settings()
        command = self.build_command(settings)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.log_file.parent.mkdir(parents=True, exist_ok=True)

        with self.log_file.open("a", encoding="utf-8") as log_handle:
            process = subprocess.Popen(
                command,
                cwd=self.root_dir,
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                creationflags=self.get_creation_flags(),
            )

        process_info = self.get_process_info(process.pid)
        state = ServiceState(
            pid=process.pid,
            host=settings.APP_HOST,
            port=settings.APP_PORT,
            workers=settings.APP_WORKERS,
            started_at=datetime.now(UTC).isoformat(),
            launch_command=command,
            process_creation_date=process_info.get("CreationDate") if process_info else None,
            log_file=str(self.log_file),
        )
        self.save_state(state)
        return (
            f"Service start requested. PID {process.pid}, host {settings.APP_HOST}, "
            f"port {settings.APP_PORT}, workers {settings.APP_WORKERS}."
        )

    def stop_service(self) -> str:
        """Stop the managed Uvicorn service if it is running."""
        state = self.load_state()
        if state is None:
            return "Service is already stopped."

        if not self.is_pid_running(state.pid):
            self.clear_state()
            return "Service is already stopped. Cleared stale state."

        if not self.is_managed_process(state):
            self.clear_state()
            return "Saved PID did not match the managed Uvicorn process. Cleared state without stopping another process."

        if self.send_graceful_stop(state.pid):
            if self.wait_for_exit(state.pid, timeout_seconds=5.0):
                self.clear_state()
                return f"Service stopped gracefully for PID {state.pid}."

        self.taskkill(state.pid, force=False)
        if self.wait_for_exit(state.pid, timeout_seconds=5.0):
            self.clear_state()
            return f"Service stopped for PID {state.pid}."

        self.taskkill(state.pid, force=True)
        if self.wait_for_exit(state.pid, timeout_seconds=5.0):
            self.clear_state()
            return f"Service force-stopped for PID {state.pid}."

        return f"Failed to stop service PID {state.pid}. Check {self.log_file} for details."

    def restart_service(self) -> str:
        """Restart the managed service using the latest persisted configuration."""
        stop_message = self.stop_service()
        if self.get_service_status().pid_alive:
            return stop_message

        start_message = self.start_service()
        return f"{stop_message}\n{start_message}"

    def update_host(self, raw_value: str) -> str:
        """Validate and persist a new host value."""
        normalized_host = self.validate_host(raw_value)
        self.persist_env_value("APP_HOST", normalized_host)
        return f"Host updated to {normalized_host}."

    def update_port(self, raw_value: str) -> str:
        """Validate and persist a new port value."""
        normalized_port = self.validate_port(raw_value)
        self.persist_env_value("APP_PORT", str(normalized_port))
        return f"Port updated to {normalized_port}."

    def update_workers(self, raw_value: str) -> str:
        """Validate and persist a new worker count."""
        normalized_workers = self.validate_workers(raw_value)
        self.persist_env_value("APP_WORKERS", str(normalized_workers))
        return f"Workers updated to {normalized_workers}."

    def load_state(self) -> ServiceState | None:
        """Load persisted process metadata from disk."""
        if not self.state_file.exists():
            return None

        try:
            payload = json.loads(self.state_file.read_text(encoding="utf-8"))
            return ServiceState(**payload)
        except (OSError, ValueError, TypeError):
            return None

    def save_state(self, state: ServiceState) -> None:
        """Persist process metadata to disk."""
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(
            json.dumps(asdict(state), indent=2),
            encoding="utf-8",
        )

    def clear_state(self) -> None:
        """Remove persisted process metadata if it exists."""
        if self.state_file.exists():
            self.state_file.unlink()

    def build_command(self, settings: Settings) -> list[str]:
        """Build the Uvicorn command line for the configured runtime."""
        return [
            self.python_executable,
            "-m",
            "uvicorn",
            "main:app",
            "--host",
            settings.APP_HOST,
            "--port",
            str(settings.APP_PORT),
            "--workers",
            str(settings.APP_WORKERS),
        ]

    def get_creation_flags(self) -> int:
        """Return Windows subprocess flags for a dedicated process group."""
        create_new_process_group = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        return create_new_process_group

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

    def is_pid_running(self, pid: int) -> bool:
        """Check whether a PID currently exists."""
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            check=False,
        )
        output = result.stdout.strip()
        return bool(output) and "No tasks are running" not in output and "INFO:" not in output

    def get_process_info(self, pid: int) -> dict[str, str] | None:
        """Return command-line metadata for the target PID."""
        command = (
            f"$process = Get-CimInstance Win32_Process -Filter \"ProcessId = {pid}\"; "
            "if ($null -eq $process) { return }; "
            "$process | Select-Object CommandLine, CreationDate | ConvertTo-Json -Compress"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            check=False,
        )
        output = result.stdout.strip()
        if result.returncode != 0 or not output:
            return None

        try:
            payload = json.loads(output)
        except json.JSONDecodeError:
            return None

        if not isinstance(payload, dict):
            return None

        command_line = payload.get("CommandLine")
        creation_date = payload.get("CreationDate")
        normalized_payload: dict[str, str] = {}
        if isinstance(command_line, str):
            normalized_payload["CommandLine"] = command_line
        if isinstance(creation_date, str):
            normalized_payload["CreationDate"] = creation_date
        return normalized_payload or None

    def is_managed_process(self, state: ServiceState) -> bool:
        """Verify that the saved PID still points to the managed Uvicorn process."""
        process_info = self.get_process_info(state.pid)
        if process_info is None:
            return False

        command_line = process_info.get("CommandLine", "")
        creation_date = process_info.get("CreationDate")
        if state.process_creation_date and creation_date != state.process_creation_date:
            return False

        return "-m uvicorn" in command_line and "main:app" in command_line

    def send_graceful_stop(self, pid: int) -> bool:
        """Attempt a graceful stop using a Windows console control signal."""
        ctrl_break_event = getattr(signal, "CTRL_BREAK_EVENT", None)
        if ctrl_break_event is None:
            return False

        try:
            os.kill(pid, ctrl_break_event)
            return True
        except OSError:
            return False

    def taskkill(self, pid: int, force: bool) -> None:
        """Stop a process tree using Windows taskkill."""
        command = ["taskkill", "/PID", str(pid), "/T"]
        if force:
            command.insert(1, "/F")
        subprocess.run(command, capture_output=True, text=True, check=False)

    def wait_for_exit(self, pid: int, timeout_seconds: float) -> bool:
        """Wait for a process to exit within the given timeout."""
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            if not self.is_pid_running(pid):
                return True
            time.sleep(0.25)
        return not self.is_pid_running(pid)

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
    os.system("cls")


def prompt_for_value(prompt: str) -> str:
    """Read a trimmed value from stdin."""
    return input(prompt).strip()


def format_status(status: ServiceStatus) -> list[str]:
    """Format status information for display in the menu."""
    lines = [
        f"Status: {status.status}",
        f"Configured host: {status.configured_host}",
        f"Configured port: {status.configured_port}",
        f"Configured workers: {status.configured_workers}",
    ]

    if status.active_state is not None:
        lines.extend(
            [
                f"Managed PID: {status.active_state.pid}",
                f"Active host: {status.active_state.host}",
                f"Active port: {status.active_state.port}",
                f"Active workers: {status.active_state.workers}",
                f"Health URL: {status.health_url}",
                f"Health check: {'ok' if status.health_ok else 'failed'}",
            ]
        )
    else:
        lines.append("Managed PID: none")

    lines.append(f"Detail: {status.detail}")
    return lines


def run_menu(controller: ServiceController) -> None:
    """Run the interactive terminal menu."""
    message = "Service manager ready."

    while True:
        clear_screen()
        print("PyStreamASR Service Manager")
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
