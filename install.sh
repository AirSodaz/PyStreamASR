#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="pystreamasr"
SYSTEMD_UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
REQUIRED_ENV_KEYS=(
  "MYSQL_DATABASE_URL"
  "MODEL_PATH"
  "APP_HOST"
  "APP_PORT"
  "APP_WORKERS"
)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="${SCRIPT_DIR}"
ENV_FILE="${ROOT_DIR}/.env"
VENV_DIR="${ROOT_DIR}/venv"
LOGS_DIR="${ROOT_DIR}/logs"
UNIT_TEMPLATE_PATH="${ROOT_DIR}/scripts/${SERVICE_NAME}.service"
INSTALL_METADATA_PATH="${LOGS_DIR}/service_install.json"

log() {
  printf '[install] %s\n' "$1"
}

fail() {
  printf '[install] ERROR: %s\n' "$1" >&2
  exit 1
}

require_command() {
  local command_name="$1"
  command -v "${command_name}" >/dev/null 2>&1 || fail "Required command not found: ${command_name}"
}

escape_sed_replacement() {
  printf '%s' "$1" | sed -e 's/[&|]/\\&/g'
}

require_linux_systemd() {
  [[ "$(uname -s)" == "Linux" ]] || fail "install.sh only supports Linux."
  [[ -d /run/systemd/system ]] || fail "systemd does not appear to be the active init system."
  require_command systemctl
}

resolve_service_user() {
  if [[ ${EUID} -ne 0 ]]; then
    fail "Run this installer with sudo so it can write ${SYSTEMD_UNIT_PATH}."
  fi

  if [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
    SERVICE_USER="${SUDO_USER}"
    return
  fi

  fail "Unable to determine a non-root service account. Run this script via sudo from the target user."
}

validate_repo_files() {
  [[ -f "${UNIT_TEMPLATE_PATH}" ]] || fail "Missing unit template: ${UNIT_TEMPLATE_PATH}"
  [[ -f "${ROOT_DIR}/requirements.txt" ]] || fail "Missing requirements.txt in ${ROOT_DIR}"
  [[ -f "${ROOT_DIR}/pyproject.toml" ]] || fail "Missing pyproject.toml in ${ROOT_DIR}"
  [[ -f "${ROOT_DIR}/gunicorn.conf.py" ]] || fail "Missing gunicorn.conf.py in ${ROOT_DIR}"
  [[ -f "${ROOT_DIR}/main.py" ]] || fail "Missing main.py in ${ROOT_DIR}"
}

validate_env_file() {
  [[ -f "${ENV_FILE}" ]] || fail "Missing .env file at ${ENV_FILE}"

  local key
  local missing_keys=()
  for key in "${REQUIRED_ENV_KEYS[@]}"; do
    if ! grep -Eq "^[[:space:]]*${key}=" "${ENV_FILE}"; then
      missing_keys+=("${key}")
    fi
  done

  if (( ${#missing_keys[@]} > 0 )); then
    fail "Missing required .env keys: ${missing_keys[*]}"
  fi
}

prepare_runtime_directories() {
  mkdir -p "${LOGS_DIR}"
  chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${LOGS_DIR}"
}

write_install_metadata() {
  cat > "${INSTALL_METADATA_PATH}" <<EOF
{
  "backend": "systemd",
  "service_name": "${SERVICE_NAME}.service",
  "runtime": "gunicorn",
  "install_mode": "service"
}
EOF

  chown "${SERVICE_USER}:${SERVICE_GROUP}" "${INSTALL_METADATA_PATH}"
}

prepare_virtualenv() {
  require_command python3.12

  if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    log "Creating Linux virtual environment with python3.12"
    python3.12 -m venv --clear "${VENV_DIR}"
  else
    log "Reusing existing virtual environment at ${VENV_DIR}"
  fi

  log "Upgrading pip"
  "${VENV_DIR}/bin/python" -m pip install --upgrade pip

  log "Installing Python dependencies"
  "${VENV_DIR}/bin/pip" install -r "${ROOT_DIR}/requirements.txt"

  log "Installing PyStreamASR console entry point"
  "${VENV_DIR}/bin/python" -m pip install --no-deps -e "${ROOT_DIR}"

  chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${VENV_DIR}"
}

install_global_launcher() {
  local entry_point="${VENV_DIR}/bin/pystreamasr"
  [[ -x "${entry_point}" ]] || fail "pystreamasr console entry point not found at ${entry_point}"

  log "Installing global pystreamasr launcher"
  ln -sfn "${entry_point}" "/usr/local/bin/pystreamasr"
}

render_systemd_unit() {
  local gunicorn_path="${VENV_DIR}/bin/gunicorn"
  [[ -x "${gunicorn_path}" ]] || fail "gunicorn executable not found at ${gunicorn_path}"

  local exec_start
  exec_start="${gunicorn_path} main:app -c ${ROOT_DIR}/gunicorn.conf.py --bind \${APP_HOST}:\${APP_PORT} --workers \${APP_WORKERS}"

  local escaped_service_user
  local escaped_service_group
  local escaped_root_dir
  local escaped_env_file
  local escaped_exec_start
  escaped_service_user="$(escape_sed_replacement "${SERVICE_USER}")"
  escaped_service_group="$(escape_sed_replacement "${SERVICE_GROUP}")"
  escaped_root_dir="$(escape_sed_replacement "${ROOT_DIR}")"
  escaped_env_file="$(escape_sed_replacement "${ENV_FILE}")"
  escaped_exec_start="$(escape_sed_replacement "${exec_start}")"

  sed \
    -e "s|__SERVICE_USER__|${escaped_service_user}|g" \
    -e "s|__SERVICE_GROUP__|${escaped_service_group}|g" \
    -e "s|__WORKING_DIRECTORY__|${escaped_root_dir}|g" \
    -e "s|__ENV_FILE__|${escaped_env_file}|g" \
    -e "s|__EXEC_START__|${escaped_exec_start}|g" \
    "${UNIT_TEMPLATE_PATH}" > "${SYSTEMD_UNIT_PATH}"

  chmod 0644 "${SYSTEMD_UNIT_PATH}"
}

enable_and_start_service() {
  log "Reloading systemd units"
  systemctl daemon-reload

  log "Enabling ${SERVICE_NAME}.service"
  systemctl enable "${SERVICE_NAME}"

  log "Restarting ${SERVICE_NAME}.service"
  systemctl restart --now "${SERVICE_NAME}"
}

main() {
  require_linux_systemd
  validate_repo_files
  resolve_service_user

  SERVICE_GROUP="$(id -gn "${SERVICE_USER}")"

  validate_env_file
  prepare_virtualenv
  prepare_runtime_directories
  write_install_metadata
  install_global_launcher
  render_systemd_unit
  enable_and_start_service

  cat <<EOF
[install] Installed unit: ${SYSTEMD_UNIT_PATH}
[install] Service name: ${SERVICE_NAME}.service
[install] Console command: /usr/local/bin/pystreamasr
[install] Install metadata: ${INSTALL_METADATA_PATH}
[install] Status: systemctl status ${SERVICE_NAME} --no-pager
[install] Logs: journalctl -u ${SERVICE_NAME} -n 100 --no-pager
EOF
}

main "$@"
