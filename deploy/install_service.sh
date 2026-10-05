#!/usr/bin/env bash
# Install and start the bot as a systemd service (restarts on crash and on reboot).
# Run from the repository root:  sudo ./deploy/install_service.sh
#   --print   show the unit that would be installed and exit
# Paper or live is decided by the code (DRY_RUN in main.py), not by this script.
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
owner="$(ls -ld "$repo" | awk '{print $3}')"
unit="$(sed -e "s|__USER__|$owner|g" -e "s|__REPO__|$repo|g" "$repo/deploy/roostoo-bot.service")"

if [[ "${1:-}" == "--print" ]]; then
    printf '%s\n' "$unit"
    exit 0
fi
[[ $EUID -eq 0 ]] || { echo "run with sudo" >&2; exit 1; }
[[ -x "$repo/.venv/bin/python" ]] || { echo "create the virtualenv first (see DEPLOYMENT.md)" >&2; exit 1; }
[[ -f "$repo/.env" ]] || { echo "missing $repo/.env (copy .env.example and fill in the keys)" >&2; exit 1; }
"$repo/.venv/bin/python" -c "import roostoo_bot" || { echo "roostoo_bot is not installed in the virtualenv (pip install -r requirements.txt)" >&2; exit 1; }

# The service appends to logs/bot.log, so the directory must exist before it starts.
install -d -o "$owner" "$repo/logs" "$repo/data_store"
printf '%s\n' "$unit" > /etc/systemd/system/roostoo-bot.service
systemctl daemon-reload
systemctl enable --now roostoo-bot
sleep 3
systemctl --no-pager status roostoo-bot || true
echo "logs: tail -f $repo/logs/bot.log"
