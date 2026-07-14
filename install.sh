#!/bin/bash
set -euo pipefail

repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
uninstall=false
for argument in "$@"; do
  [[ $argument == "--uninstall" ]] && uninstall=true
done

required_commands=(python3 pgrep pkill)
if [[ $uninstall == false ]]; then
  required_commands+=(codex waybar notify-send)
fi
for command in "${required_commands[@]}"; do
  command -v "$command" >/dev/null || {
    echo "Missing required command: $command" >&2
    exit 1
  }
done

if [[ $uninstall == false ]]; then
  python3 - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit("Python 3.11 or newer is required")
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
PY
fi

exec python3 "$repo_dir/scripts/install.py" "$@"
