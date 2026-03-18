#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${ROOT_DIR}"

if [[ -f "venv/bin/activate" ]]; then
  # Linux/macOS virtualenv layout
  source "venv/bin/activate"
elif [[ -f "venv/Scripts/activate" ]]; then
  # Windows virtualenv layout when running under Git Bash/MSYS
  source "venv/Scripts/activate"
else
  echo "Virtual environment activation script not found under ${ROOT_DIR}/venv" >&2
  exit 1
fi

python -m scripts.service_manager
