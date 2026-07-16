# AGENTS.md

Guidance for coding agents working in this repository.

## Project purpose

Waybar AI Usage is a small Waybar module with a native GTK4 layer-shell popup for viewing Claude and Codex quota windows.

Read these files before making user-facing changes:

- `README.md` for installation and behavior
- `PRODUCT.md` for product intent and constraints
- `DESIGN.md` for visual direction and theme conventions

## Architecture

- `bin/waybar-ai-usage` contains the collector, cache handling, Waybar JSON output, settings, and GTK popup.
- `scripts/install.py` transactionally integrates the module with an existing Waybar JSONC configuration.
- `install.sh` checks runtime dependencies and invokes the installer.
- `waybar/module.jsonc` and `waybar/style.css` are the installed Waybar fragments.
- `tests/test_state.py` covers usage, freshness, settings, and output behavior.
- `tests/test_install.py` covers JSONC editing, ownership, backup, rollback, and uninstall behavior.

Claude usage comes from Anthropic's OAuth usage endpoint. Codex usage comes from the local `codex app-server` JSON-RPC method `account/rateLimits/read`.

Runtime state:

- Cache: `~/.cache/waybar-ai-usage/usage.json`
- Settings: `~/.config/waybar-ai-usage/settings.json`
- Installer ownership record: `~/.config/waybar/.waybar-ai-usage-install.json`

## Security invariants

- Never print, log, cache, expose, or commit OAuth credentials.
- Never read or publish raw private session transcripts.
- Keep cache and settings files permissioned `0600`.
- Do not add network endpoints beyond the documented Claude and Codex usage sources without explicit approval.
- Codex app-server processes must terminate on success, error, and timeout.
- Do not weaken GitHub secret scanning, push protection, branch protection, or SHA-pinned Actions.

## Data behavior

- Refresh Codex at most every ten minutes and Claude at most hourly during automatic polling.
- Manual refresh bypasses ordinary provider deadlines but never an active Claude `Retry-After` backoff.
- Retry transient Codex failures after one minute so boot-time network races recover automatically.
- Never write Claude OAuth credentials directly. Only after a user-initiated refresh receives `401`, Claude Code may be started in a short-lived background pseudo-terminal so it renews its own session; never send it a prompt, and always terminate the process tree.
- Provider collection runs concurrently.
- Failed refreshes retain prior values and clearly mark them stale or conservatively delayed.
- Never label cached values as freshly updated after a failed refresh.
- Keep percentage and reset time tied to the same selected quota window.
- The Waybar output and settings preview must use the same renderer.

## Display defaults

Keep these defaults unless a request explicitly changes them:

- Provider readout enabled
- Colored bot icon for each provider
- Percentages visible
- Reset countdowns hidden
- Compact provider spacing
- Hover details disabled
- Click popup enabled

Display changes must remain configurable in the popup settings when practical.

## Installer safety

Treat installer changes as high risk.

- Preserve valid JSONC comments, strings, and unrelated formatting.
- Never patch raw structural characters without the comment/string-aware scanner.
- Keep installation idempotent.
- Waybar commands must use `$HOME/.local/bin/waybar-ai-usage`; do not rely on `~/.local/bin` being in the boot session `PATH`.
- Refuse ambiguous duplicate module entries.
- Never overwrite unmanaged files or configuration fragments.
- Create a collision-safe backup before every real mutation.
- Validate the patched JSONC before writing.
- Roll back all touched files if writing or Waybar restart fails.
- Record exact installer ownership and hashes.
- Uninstall only unchanged fragments and files recorded as installer-owned.
- Preserve user-modified installer assets instead of deleting them.
- Add a regression test for every installer ownership or mutation change.

## Visual changes

- Follow the active Omarchy theme from `~/.config/omarchy/current/theme/colors.toml`.
- Keep the popup compact, readable, and native to the desktop.
- Avoid gradients, glass effects, oversized metrics, decorative motion, and unnecessary cards.
- Do not communicate warning or error state through color alone.
- Preserve keyboard Escape and multi-monitor behavior.
- Visually inspect both the Waybar indicator and popup after GTK or layout changes.

## Verification

Run before committing:

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile bin/waybar-ai-usage scripts/install.py
bash -n install.sh
git diff --check
```

For affected behavior, also verify:

- Live Claude and Codex collection
- Cached failure behavior
- No orphaned `codex app-server` process
- Waybar JSON validity
- Popup open, close, settings, and multi-monitor placement
- Installer dry-run, idempotence, rollback, and ownership safety

Update `README.md` and screenshots when user-visible behavior changes.

## Contribution workflow

- Keep changes focused and easy to review.
- Do not commit credentials, caches, generated runtime state, or machine-local configuration.
- External contributors use pull requests; `main` is protected.
- CI and required review must pass before merge.
