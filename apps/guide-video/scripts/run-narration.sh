#!/usr/bin/env bash
# Narration runs on a VoxCPM2 install that lives outside this repo (model weights
# and venv are never copied in). Override both for another machine.
set -euo pipefail
: "${VOXCPM_PYTHON:=/Users/rishabhjain/Documents/ChatGPT/huntik/tools/voxcpm/.venv/bin/python}"
: "${VOXCPM_MODEL:=/Users/rishabhjain/Documents/ChatGPT/huntik/tools/voxcpm/models/VoxCPM2}"
export VOXCPM_MODEL
exec "$VOXCPM_PYTHON" "$(dirname "$0")/narrate.py" "${1:-render}"
