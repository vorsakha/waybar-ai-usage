#!/bin/bash
set -euo pipefail

repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
command_path="$repo_dir/bin/waybar-ai-usage"
install_dir="${HOME}/.local/bin"

for command in python3 codex; do
  command -v "$command" >/dev/null || {
    echo "Missing required command: $command" >&2
    exit 1
  }
done

python3 - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit("Python 3.11 or newer is required")
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
PY

mkdir -p "$install_dir"
ln -sfn "$command_path" "$install_dir/waybar-ai-usage"
chmod +x "$command_path"

echo "Installed $install_dir/waybar-ai-usage"
echo "Add waybar/module.jsonc and waybar/style.css to your Waybar configuration, then restart Waybar."
