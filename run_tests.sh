#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="/Volumes/External/Code/Birka"
VENV_PATH="${PROJECT_ROOT}/.venv"

if [[ ! -d "${VENV_PATH}" ]]; then
  echo "Virtualenv not found at ${VENV_PATH}. Create it first."
  exit 1
fi

cd "${PROJECT_ROOT}"
source "${VENV_PATH}/bin/activate"
rm -rf "${PROJECT_ROOT}/tests/__pycache__"
PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen PYTHONPATH="${PROJECT_ROOT}/src" python3 -m unittest discover -s "${PROJECT_ROOT}/tests"
