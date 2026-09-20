#!/usr/bin/env bash
set -euo pipefail

# Set by the endpoint template. This keeps the user's key out of the image
# registry and allows it to be rotated without rebuilding the image.
if [[ -n "${SSH_AUTHORIZED_KEY:-}" ]]; then
  install -d -m 700 /root/.ssh
  printf '%s\n' "$SSH_AUTHORIZED_KEY" > /root/.ssh/authorized_keys
  chmod 600 /root/.ssh/authorized_keys
  /usr/sbin/sshd
else
  echo "[ClipWeave] SSH is disabled: SSH_AUTHORIZED_KEY is not configured."
fi

exec /start.sh
