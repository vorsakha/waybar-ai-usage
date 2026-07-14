# Waybar AI Usage

A native Waybar indicator and GTK4 layer-shell popup for live Claude and Codex quota.

- Calm single-icon Waybar module
- Click-to-toggle popup centered below the bar
- Claude and Codex 5-hour/weekly usage and reset countdowns
- Theme colors loaded from the active Omarchy theme
- Five-minute cache with background refresh
- Explicit stale and provider-error states
- Right-click forced refresh with a notification

## Requirements

- Python 3.11+
- PyGObject with GTK4
- `gtk4-layer-shell`
- `codex` authenticated with ChatGPT
- Claude Code authenticated through `~/.claude/.credentials.json`
- Waybar and `notify-send`

The collector reads existing local OAuth credentials but never writes or prints them. Cached usage is stored with mode `0600` under `~/.cache/waybar-ai-usage/`.

## Install

```bash
./install.sh
```

Add `custom/ai-usage` to the desired Waybar module list, then copy the module definition from [`waybar/module.jsonc`](waybar/module.jsonc) into `~/.config/waybar/config.jsonc`.

Append [`waybar/style.css`](waybar/style.css) to `~/.config/waybar/style.css`, adjusting semantic colors if your theme uses different warning/critical values.

Restart Waybar:

```bash
omarchy restart waybar
```

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

## Data sources

Claude is read from Anthropic's OAuth usage endpoint. Codex is read from the official `codex app-server` JSON-RPC method `account/rateLimits/read`, the same live data source used for Codex status displays.
