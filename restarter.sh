#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="/Volumes/External/Code/Birka"
VENV_PATH="${PROJECT_ROOT}/.venv"

# --- defaults ---------------------------------------------------------------
# Backend: tsf (default, SF2), sfizz (SFZ), fluidsynth, or auto.
# Override with BIRKA_BACKEND env var or the first CLI arg (sfizz / tsf / ...).
BACKEND="${BIRKA_BACKEND:-tsf}"
if [[ $# -ge 1 ]]; then
  BACKEND="$1"
fi

if [[ ! -d "${VENV_PATH}" ]]; then
  echo "Virtualenv not found at ${VENV_PATH}. Create it first."
  exit 1
fi

cd "${PROJECT_ROOT}"
source "${VENV_PATH}/bin/activate"

# Instrument files: SF2 soundfont for tsf/fluidsynth, SFZ bank for sfizz.
# BIRKA_SFZ, BIRKA_SOUNDFONT, BIRKA_BACKEND are honoured only if not already
# set in the environment, so ad-hoc overrides always win.
export BIRKA_BACKEND="${BACKEND}"
: "${BIRKA_SOUNDFONT:=${PROJECT_ROOT}/data/FluidR3 GM.sf2}"
export BIRKA_SOUNDFONT

case "${BACKEND}" in
  sfizz)
    # Use the bundled Discord GM bank by default; BIRKA_SFZ overrides it.
    : "${BIRKA_SFZ:=${PROJECT_ROOT}/data/Discord-SFZ-GM-Bank/Discord GM/GM_combined.sfz}"
    # PITCH_CENTER_IGNORE=1 swaps to the no-detected-keycenter variant so each
    # sample plays at its *requested* note (no Surge preset-transpose compensation).
    # Useful for A/B testing the detected pitch_keycenter vs the raw one.
    if [[ "${PITCH_CENTER_IGNORE:-0}" == "1" ]]; then
      nokey="${BIRKA_SFZ%.sfz}_nokeycentered.sfz"
      if [[ -f "${nokey}" ]]; then
        BIRKA_SFZ="${nokey}"
        echo "[birka] PITCH_CENTER_IGNORE=1 -> using ${nokey}"
      else
        echo "[birka] PITCH_CENTER_IGNORE=1 but ${nokey} not found; falling back to detected SFZ"
      fi
    fi
    export BIRKA_SFZ
    echo "[birka] backend: sfizz | SFZ: ${BIRKA_SFZ}"
    ;;
  tsf|fluidsynth|auto)
    echo "[birka] backend: ${BACKEND} | soundfont: ${BIRKA_SOUNDFONT}"
    ;;
  *)
    echo "[birka] unknown backend '${BACKEND}' (expected: tsf|sfizz|fluidsynth|auto)"
    exit 1
    ;;
esac

PYTHONPATH="${PROJECT_ROOT}/src" python3 "${PROJECT_ROOT}/src/main.py"
