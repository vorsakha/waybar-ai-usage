# Waybar AI Usage

A native Waybar indicator and GTK4 layer-shell popup for live Claude and Codex quota.

- Calm single-icon Waybar module
- Click-to-toggle popup centered below the clicked monitor’s bar
- Claude and Codex 5-hour/weekly usage and reset countdowns
- Theme colors loaded from the active Omarchy theme
- Five-minute cache with background refresh
- Explicit stale and provider-error states
- Right-click forced refresh with a notification

## Requirements

- Python 3.11+
- PyGObject with GTK4
- `gtk4-layer-shell`
- Waybar and `notify-send`
- `codex` authenticated with ChatGPT
- Claude Code authenticated through `~/.claude/.credentials.json`

On Omarchy, the desktop dependencies are normally already installed. The installer checks runtime dependencies before changing anything and exits with a clear error if one is missing. Claude Code and Codex must already be authenticated.

The collector reads existing local OAuth credentials but never writes or prints them. Cached usage is stored with mode `0600` under `~/.cache/waybar-ai-usage/`.

## One-command install

From an existing clone:

```bash
cd ~/www/personal/waybar-ai-usage && ./install.sh
```

Or clone the repository and install it in one command:

```bash
gh repo clone vorsakha/waybar-ai-usage ~/www/personal/waybar-ai-usage && cd ~/www/personal/waybar-ai-usage && ./install.sh
```

The default target is `modules-center`. To put the icon elsewhere:

```bash
./install.sh --module-list modules-right
```

The installer is idempotent, so running it again updates installer-owned assets without duplicating the icon or CSS. Re-running with a different `--module-list` moves an installer-owned icon instead of creating a second one. Pre-existing user-managed definitions and styles are preserved.

### What the installer changes

1. Validates the current Waybar JSONC before editing.
2. Creates a timestamped backup under `~/.config/waybar/.waybar-ai-usage-backups/`.
3. Adds `custom/ai-usage` to the selected Waybar module list.
4. Installs the module definition and popup stylesheet.
5. Creates `~/.local/bin/waybar-ai-usage` as a symlink to this clone.
6. Validates the patched JSONC.
7. Restarts Waybar through Omarchy, or reloads it with `SIGUSR2` elsewhere.
8. Automatically restores the previous files if installation or restart fails.

Preview all checks without changing files:

```bash
./install.sh --dry-run
```

## Uninstall

```bash
./install.sh --uninstall
```

Uninstall also creates a backup and reads the ownership record created during installation. It removes only entries, imports, assets, and executable links that the installer created. Without that ownership record, it refuses to alter user-managed Waybar configuration.

## Commands

```bash
waybar-ai-usage waybar            # Print Waybar JSON, refresh stale cache in background
waybar-ai-usage popup             # Toggle the native popup
waybar-ai-usage refresh           # Refresh cache now
waybar-ai-usage refresh --notify  # Refresh and show values in a notification
```

## Interaction

- **Left click:** toggle popup
- **Right click:** refresh and notify
- **Escape / close button:** dismiss popup

## Manual integration reference

The exact module and styles installed by the script are available in [`waybar/module.jsonc`](waybar/module.jsonc) and [`waybar/style.css`](waybar/style.css).

## Data sources

Claude is read from Anthropic's OAuth usage endpoint. Codex is read from the official `codex app-server` JSON-RPC method `account/rateLimits/read`, the same live data source used for Codex status displays.

## Contributing

Pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for the short workflow.

## License

[MIT](LICENSE)
