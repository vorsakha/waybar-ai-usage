# Design System

## Theme

A compact native desktop utility derived from the active Omarchy theme. Dark or light behavior follows `~/.config/omarchy/current/theme/colors.toml`; the app does not own a separate brand palette.

## Color

- Background: active theme `background`
- Raised surface: active theme `color0`
- Primary text: active theme `foreground`
- Muted text: active theme `color8`
- Accent and progress: active theme `accent`
- Warning: active theme `color2`
- Critical and error: active theme `color1`

Status is always communicated with text as well as color.

## Typography

Use JetBrainsMono Nerd Font throughout to match Waybar. Keep a compact product scale: 11px metadata, 12px labels, 14px provider names, 16px panel title. Use weight rather than extra font families for hierarchy.

## Layout

The popup is a fixed-width layer-shell panel centered below Waybar. One header is followed by two provider sections separated by a divider. Each provider contains two quota rows: label and percentage, progress track, then reset timing. Avoid nested cards.

## Components

- Waybar indicator: one AI glyph; warning and critical classes alter color.
- Header: title, freshness label, refresh button, close button.
- Provider section: provider name, plan badge, quota rows, inline error state.
- Progress bar: restrained track, theme accent fill, semantic warning/critical fill.
- Empty/loading state: explicit copy with cached data retained where possible.

## Motion

Use only a short panel reveal and progress-state transitions when supported. No staged entrances or decorative movement. Reduced-motion environments receive immediate state changes.

## Interaction

- Left click: toggle popup.
- Right click: force refresh and notify when complete.
- Escape or close button: dismiss popup.
- Popup refresh button: refresh without blocking the interface.
