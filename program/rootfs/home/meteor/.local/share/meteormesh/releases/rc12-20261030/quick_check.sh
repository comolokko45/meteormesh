#!/usr/bin/env bash
set -euo pipefail
if [[ "$(id -u)" == 0 ]]; then echo 'REFUSING_ROOT'; exit 2; fi
cd "$(dirname "${BASH_SOURCE[0]}")/MeteorHub"
python3 -B mm01_readonly_preflight.py
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest -q test_lab_http_adapter test_mm03_merge test_rc11_fd_handoff
printf '%s\n' 'RC11_FD_HANDOFF_SMOKE_OK; NO_PRODUCTION_ACTIVATION'
