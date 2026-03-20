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
export BIRKA_SOUNDFONT="${PROJECT_ROOT}/data/FluidR3 GM.sf2"
PYTHONPATH="${PROJECT_ROOT}/src" python3 "${PROJECT_ROOT}/src/main.py"
