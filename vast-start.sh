#!/usr/bin/env bash
set -euo pipefail
python /vast_provision_models.py
exec /start.sh
