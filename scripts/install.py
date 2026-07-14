#!/usr/bin/env python3
"""Transactional Waybar integration for Waybar AI Usage."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import NamedTuple

MODULE_NAME = "custom/ai-usage"
MANAGED_START = "// BEGIN waybar-ai-usage (managed by install.sh)"
MANAGED_END = "// END waybar-ai-usage"
CSS_IMPORT = '@import "ai-usage.css";'
INSTALL_STATE_NAME = ".waybar-ai-usage-install.json"


def strip_jsonc(text: str) -> str:
    """Remove JSON comments and trailing commas without touching strings."""
    output: list[str] = []
    index = 0
    in_string = False
    escaped = False
    while index < len(text):
        char = text[index]
        following = text[index + 1] if index + 1 < len(text) else ""
        if in_string:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            output.append(char)
            index += 1
        elif char == "/" and following == "/":
            index += 2
            while index < len(text) and text[index] not in "\r\n":
                index += 1
        elif char == "/" and following == "*":
            index += 2
            while index + 1 < len(text) and text[index:index + 2] != "*/":
                index += 1
            index += 2
        else:
            output.append(char)
            index += 1

    uncommented = "".join(output)
    result: list[str] = []
    in_string = False
    escaped = False
    index = 0
    while index < len(uncommented):
        char = uncommented[index]
        if in_string:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            result.append(char)
            index += 1
            continue
        if char == ",":
            lookahead = index + 1
            while lookahead < len(uncommented) and uncommented[lookahead].isspace():
                lookahead += 1
            if lookahead < len(uncommented) and uncommented[lookahead] in "]}":
                index += 1
                continue
        result.append(char)
        index += 1
    return "".join(result)


def validate_jsonc(text: str, label: str) -> None:
    try:
        json.loads(strip_jsonc(text))
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Refusing to write invalid {label}: {error}") from error


class JsoncToken(NamedTuple):
    kind: str
    value: str
    start: int
    end: int


class ArraySpan(NamedTuple):
    name: str
    start: int
    end: int
    tokens: list[JsoncToken]


def jsonc_tokens(text: str) -> list[JsoncToken]:
    tokens: list[JsoncToken] = []
    index = 0
    punctuation = "{}[]:,"
    while index < len(text):
        if text[index].isspace():
            index += 1
            continue
        if text.startswith("//", index):
            newline = text.find("\n", index + 2)
            index = len(text) if newline < 0 else newline + 1
            continue
        if text.startswith("/*", index):
            closing = text.find("*/", index + 2)
            index = len(text) if closing < 0 else closing + 2
            continue
        if text[index] == '"':
            start = index
            index += 1
            escaped = False
            while index < len(text):
                char = text[index]
                index += 1
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    break
            raw = text[start:index]
            tokens.append(JsoncToken("string", json.loads(raw), start, index))
            continue
        if text[index] in punctuation:
            tokens.append(JsoncToken(text[index], text[index], index, index + 1))
            index += 1
            continue
        start = index
        while index < len(text) and not text[index].isspace() and text[index] not in punctuation:
            if text.startswith("//", index) or text.startswith("/*", index):
                break
            index += 1
        tokens.append(JsoncToken("literal", text[start:index], start, index))
    return tokens


def array_spans(config: str) -> list[ArraySpan]:
    tokens = jsonc_tokens(config)
    spans: list[ArraySpan] = []
    for index in range(len(tokens) - 2):
        key, colon, opening = tokens[index:index + 3]
        if key.kind != "string" or colon.kind != ":" or opening.kind != "[":
            continue
        depth = 0
        for closing_index in range(index + 2, len(tokens)):
            token = tokens[closing_index]
            if token.kind == "[":
                depth += 1
            elif token.kind == "]":
                depth -= 1
                if depth == 0:
                    spans.append(ArraySpan(key.value, opening.start, token.end, tokens[index + 2:closing_index + 1]))
                    break
    return spans


def find_module_list(config: str, list_name: str) -> ArraySpan:
    for span in array_spans(config):
        if span.name == list_name:
            return span
    raise RuntimeError(f'Waybar config has no "{list_name}" array; choose one with --module-list')


def array_values(config: str, span: ArraySpan) -> list[str]:
    value = json.loads(strip_jsonc(config[span.start:span.end]))
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise RuntimeError(f'Waybar "{span.name}" must be an array of module names')
    return value


def managed_module(snippet: str) -> str:
    body = snippet.strip()
    if body.endswith(","):
        body = body[:-1].rstrip()
    indented = "\n".join(f"  {line}" for line in body.splitlines())
    return f"  {MANAGED_START}\n{indented},\n  {MANAGED_END}\n"


def managed_module_span(config: str) -> tuple[int, int] | None:
    pattern = re.compile(
        rf"^[ \t]*{re.escape(MANAGED_START)}[ \t]*\r?\n.*?^[ \t]*{re.escape(MANAGED_END)}[ \t]*(?:\r?\n)?",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(config)
    return (match.start(), match.end()) if match else None


def managed_module_text(config: str) -> str | None:
    span = managed_module_span(config)
    return config[span[0]:span[1]] if span else None


def remove_managed_module(config: str) -> str:
    span = managed_module_span(config)
    return config[:span[0]] + config[span[1]:] if span else config


def add_module_definition(config: str, snippet: str) -> str:
    config = remove_managed_module(config)
    if re.search(r'"custom/ai-usage"\s*:', strip_jsonc(config)):
        return config
    opening = next((token for token in jsonc_tokens(config) if token.kind == "{"), None)
    if opening is None:
        raise RuntimeError("Waybar config has no root object")
    return config[:opening.end] + "\n" + managed_module(snippet) + config[opening.end:].lstrip("\n")


def add_module_to_list(config: str, list_name: str) -> str:
    span = find_module_list(config, list_name)
    values = array_values(config, span)
    count = values.count(MODULE_NAME)
    if count > 1:
        raise RuntimeError(f'{MODULE_NAME} appears multiple times in "{list_name}"; refusing an ambiguous edit')
    if count == 1:
        return config
    last_token = span.tokens[-2] if len(span.tokens) > 1 else span.tokens[0]
    separator = "" if not values or last_token.kind == "," else ","
    insertion = f'{separator} "{MODULE_NAME}"'
    return config[:span.end - 1] + insertion + config[span.end - 1:]


def remove_module_from_list(config: str, list_name: str) -> str:
    span = find_module_list(config, list_name)
    values = array_values(config, span)
    if values.count(MODULE_NAME) > 1:
        raise RuntimeError(f'{MODULE_NAME} appears multiple times in "{list_name}"; refusing an ambiguous removal')
    if MODULE_NAME not in values:
        return config
    module_index = next(
        index
        for index, token in enumerate(span.tokens)
        if token.kind == "string" and token.value == MODULE_NAME
    )
    module = span.tokens[module_index]
    previous = span.tokens[module_index - 1] if module_index > 0 else None
    following = span.tokens[module_index + 1] if module_index + 1 < len(span.tokens) else None
    if following and following.kind == ",":
        start, end = module.start, following.end
    elif previous and previous.kind == ",":
        start, end = previous.start, module.end
    else:
        start, end = module.start, module.end
    return config[:start] + config[end:]


def remove_module_from_lists(config: str) -> str:
    while True:
        locations = module_locations(config)
        if not locations:
            return config
        config = remove_module_from_list(config, locations[0])


def atomic_write(path: Path, content: str, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(content)
        if mode is None and path.exists():
            mode = path.stat().st_mode & 0o777
        os.chmod(temporary, mode or 0o644)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def backup_files(paths: list[Path], backup_dir: Path) -> None:
    backup_dir.mkdir(parents=True, exist_ok=False)
    manifest: dict[str, dict[str, str | bool]] = {}
    for path in paths:
        record: dict[str, str | bool] = {"exists": path.exists() or path.is_symlink()}
        if path.is_symlink():
            record["symlink"] = os.readlink(path)
        elif path.exists():
            destination = backup_dir / path.name
            shutil.copy2(path, destination)
            record["backup"] = destination.name
        manifest[str(path)] = record
    atomic_write(backup_dir / "manifest.json", json.dumps(manifest, indent=2) + "\n", 0o600)


def snapshot(path: Path) -> tuple[str, bytes | str | None, int | None]:
    if path.is_symlink():
        return ("symlink", os.readlink(path), None)
    if path.exists():
        return ("file", path.read_bytes(), path.stat().st_mode & 0o777)
    return ("missing", None, None)


def restore(path: Path, state: tuple[str, bytes | str | None, int | None]) -> None:
    kind, value, mode = state
    if path.exists() or path.is_symlink():
        path.unlink()
    if kind == "symlink" and isinstance(value, str):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(value)
    elif kind == "file" and isinstance(value, bytes):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
        os.chmod(path, mode or 0o644)


def read_install_state(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def module_locations(config: str) -> list[str]:
    return [
        span.name
        for span in array_spans(config)
        if span.name.startswith("modules-") and MODULE_NAME in array_values(config, span)
    ]


def backup_destination(root: Path, suffix: str = "") -> Path:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    nanos = time.time_ns() % 1_000_000_000
    return root / f"{stamp}-{nanos:09d}{suffix}"


def text_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def asset_ownership(source: Path, destination: Path, previously_owned: bool, previous_hash: object = None) -> bool:
    if previously_owned:
        if not (destination.exists() or destination.is_symlink()):
            return True
        if not destination.is_file() or destination.is_symlink():
            raise RuntimeError(f"Installer-owned asset path was replaced: {destination}")
        expected_hash = previous_hash if isinstance(previous_hash, str) else file_hash(source)
        if file_hash(destination) != expected_hash:
            raise RuntimeError(f"Installer-owned asset was modified; refusing to overwrite it: {destination}")
        return True
    if not (destination.exists() or destination.is_symlink()):
        return True
    if destination.is_file() and not destination.is_symlink() and destination.read_bytes() == source.read_bytes():
        return False
    raise RuntimeError(f"Refusing to overwrite unmanaged file: {destination}")


def restart_waybar(was_running: bool) -> None:
    if not was_running:
        return
    if shutil.which("omarchy"):
        result = subprocess.run(["omarchy", "restart", "waybar"], timeout=15)
    else:
        result = subprocess.run(["pkill", "-SIGUSR2", "waybar"], timeout=5)
    if result.returncode != 0:
        raise RuntimeError("Waybar restart failed")
    time.sleep(2)
    if subprocess.run(["pgrep", "-x", "waybar"], stdout=subprocess.DEVNULL).returncode != 0:
        raise RuntimeError("Waybar did not remain running after reload")


def install(args: argparse.Namespace, repo: Path) -> None:
    waybar_dir = Path(args.waybar_dir).expanduser().resolve()
    config_path = waybar_dir / "config.jsonc"
    style_path = waybar_dir / "style.css"
    module_asset = waybar_dir / "ai-usage-module.jsonc"
    css_asset = waybar_dir / "ai-usage.css"
    install_state_path = waybar_dir / INSTALL_STATE_NAME
    command_link = Path.home() / ".local" / "bin" / "waybar-ai-usage"
    source_command = repo / "bin" / "waybar-ai-usage"
    source_module = repo / "waybar" / "module.jsonc"
    source_css = repo / "waybar" / "style.css"

    if not config_path.is_file() or not style_path.is_file():
        raise RuntimeError(f"Expected {config_path} and {style_path}")
    original_config = config_path.read_text()
    original_style = style_path.read_text()
    validate_jsonc(original_config, "Waybar config")
    previous = read_install_state(install_state_path)

    definition_owned = bool(previous.get("managedDefinition")) or MANAGED_START in original_config
    definition_exists = re.search(r'"custom/ai-usage"\s*:', strip_jsonc(original_config)) is not None
    new_config = original_config
    if definition_owned:
        current_block = managed_module_text(original_config)
        expected_block_hash = previous.get("managedDefinitionHash")
        if current_block is None:
            raise RuntimeError("Installer-owned module definition was removed; refusing to recreate it automatically")
        if not isinstance(expected_block_hash, str):
            expected_block_hash = text_hash(managed_module(source_module.read_text()))
        if text_hash(current_block) != expected_block_hash:
            raise RuntimeError("Installer-owned module definition was modified; refusing to overwrite it")
        new_config = add_module_definition(new_config, source_module.read_text())
    elif not definition_exists:
        new_config = add_module_definition(new_config, source_module.read_text())
        definition_owned = True

    list_owned = bool(previous.get("addedListEntry"))
    locations = module_locations(new_config)
    for location in locations:
        if array_values(new_config, find_module_list(new_config, location)).count(MODULE_NAME) > 1:
            raise RuntimeError(f'{MODULE_NAME} appears multiple times in "{location}"; refusing an ambiguous edit')
    if len(locations) > 1:
        raise RuntimeError(f"{MODULE_NAME} already exists in multiple module lists; refusing an ambiguous edit")
    if list_owned:
        previous_list = previous.get("moduleList")
        if not isinstance(previous_list, str):
            raise RuntimeError("Installer ownership record has no module list")
        previous_span = find_module_list(new_config, previous_list)
        expected_array_hash = previous.get("moduleArrayHash")
        if isinstance(expected_array_hash, str) and text_hash(new_config[previous_span.start:previous_span.end]) != expected_array_hash:
            raise RuntimeError(f'Installer-owned "{previous_list}" array was modified; refusing to overwrite it')
        new_config = remove_module_from_list(new_config, previous_list)
        remaining_locations = module_locations(new_config)
        if remaining_locations:
            joined = ", ".join(remaining_locations)
            raise RuntimeError(f"An unmanaged {MODULE_NAME} also exists in {joined}; refusing to create a duplicate")
        new_config = add_module_to_list(new_config, args.module_list)
    elif not locations:
        new_config = add_module_to_list(new_config, args.module_list)
        list_owned = True
    elif locations != [args.module_list]:
        joined = ", ".join(locations)
        raise RuntimeError(f"{MODULE_NAME} already exists in {joined}; refusing to duplicate it in {args.module_list}")
    validate_jsonc(new_config, "patched Waybar config")

    css_import_owned = bool(previous.get("addedCssImport"))
    import_count = original_style.count(CSS_IMPORT)
    new_style = original_style
    if css_import_owned:
        if import_count != 1:
            raise RuntimeError("Installer-owned CSS import was changed or duplicated; refusing to overwrite it")
    elif "#custom-ai-usage" not in new_style and import_count == 0:
        new_style = new_style.rstrip() + f"\n\n{CSS_IMPORT}\n"
        css_import_owned = True

    module_asset_owned = asset_ownership(
        source_module, module_asset, bool(previous.get("moduleAssetOwned")), previous.get("moduleAssetHash")
    )
    css_asset_owned = asset_ownership(
        source_css, css_asset, bool(previous.get("cssAssetOwned")), previous.get("cssAssetHash")
    )
    command_owned = bool(previous.get("commandOwned"))
    if command_owned:
        if not command_link.is_symlink() or command_link.resolve() != source_command.resolve():
            raise RuntimeError(f"Installer-owned command link was removed or retargeted; refusing to replace it: {command_link}")
    elif not (command_link.exists() or command_link.is_symlink()):
        command_owned = True
    elif not (command_link.is_symlink() and command_link.resolve() == source_command.resolve()):
        raise RuntimeError(f"Refusing to replace unmanaged command: {command_link}")

    managed_text = managed_module_text(new_config) if definition_owned else None
    owned_array = find_module_list(new_config, args.module_list) if list_owned else None
    install_state = {
        "version": 1,
        "repo": str(repo),
        "moduleList": args.module_list,
        "managedDefinition": definition_owned,
        "managedDefinitionHash": text_hash(managed_text) if managed_text else None,
        "addedListEntry": list_owned,
        "moduleArrayHash": text_hash(new_config[owned_array.start:owned_array.end]) if owned_array else None,
        "addedCssImport": css_import_owned,
        "cssImportHash": text_hash(CSS_IMPORT) if css_import_owned else None,
        "moduleAssetOwned": module_asset_owned,
        "moduleAssetHash": file_hash(source_module) if module_asset_owned else None,
        "cssAssetOwned": css_asset_owned,
        "cssAssetHash": file_hash(source_css) if css_asset_owned else None,
        "commandOwned": command_owned,
    }
    touched = [config_path, style_path, module_asset, css_asset, command_link, install_state_path]
    backup_root = waybar_dir / ".waybar-ai-usage-backups"
    backup_dir = backup_destination(backup_root)
    if args.dry_run:
        print(f"Dry run passed. A real install would back up files under {backup_dir}")
        return
    backup_files(touched, backup_dir)

    states = {path: snapshot(path) for path in touched}
    was_running = subprocess.run(["pgrep", "-x", "waybar"], stdout=subprocess.DEVNULL).returncode == 0
    try:
        if new_config != original_config:
            atomic_write(config_path, new_config)
        if new_style != original_style:
            atomic_write(style_path, new_style)
        if module_asset_owned:
            shutil.copy2(source_module, module_asset)
        if css_asset_owned:
            shutil.copy2(source_css, css_asset)
        if command_owned:
            command_link.parent.mkdir(parents=True, exist_ok=True)
            if command_link.exists() or command_link.is_symlink():
                command_link.unlink()
            command_link.symlink_to(source_command)
        source_command.chmod(source_command.stat().st_mode | 0o111)
        atomic_write(install_state_path, json.dumps(install_state, indent=2) + "\n", 0o600)
        restart_waybar(was_running)
    except Exception:
        for path, state in states.items():
            restore(path, state)
        if was_running:
            try:
                restart_waybar(True)
            except Exception:
                pass
        raise

    print("Waybar AI Usage installed successfully.")
    print(f"Backup: {backup_dir}")
    print("Left-click the AI icon for the popup; right-click to refresh.")


def uninstall(args: argparse.Namespace, repo: Path) -> None:
    waybar_dir = Path(args.waybar_dir).expanduser().resolve()
    config_path = waybar_dir / "config.jsonc"
    style_path = waybar_dir / "style.css"
    module_asset = waybar_dir / "ai-usage-module.jsonc"
    css_asset = waybar_dir / "ai-usage.css"
    install_state_path = waybar_dir / INSTALL_STATE_NAME
    command_link = Path.home() / ".local" / "bin" / "waybar-ai-usage"
    if not config_path.is_file() or not style_path.is_file():
        raise RuntimeError("Waybar configuration was not found")
    state = read_install_state(install_state_path)
    if not state:
        raise RuntimeError("No installer ownership record found; refusing to remove user-managed Waybar configuration")

    original_config = config_path.read_text()
    new_config = original_config
    if state.get("managedDefinition"):
        current_block = managed_module_text(new_config)
        expected_hash = state.get("managedDefinitionHash")
        if current_block and isinstance(expected_hash, str) and text_hash(current_block) == expected_hash:
            new_config = remove_managed_module(new_config)
        else:
            print("Preserving modified module definition")
    if state.get("addedListEntry"):
        list_name = state.get("moduleList")
        expected_hash = state.get("moduleArrayHash")
        if isinstance(list_name, str):
            try:
                span = find_module_list(new_config, list_name)
            except RuntimeError:
                span = None
            if span and isinstance(expected_hash, str) and text_hash(new_config[span.start:span.end]) == expected_hash:
                new_config = remove_module_from_list(new_config, list_name)
            else:
                print(f'Preserving modified "{list_name}" module array')
    validate_jsonc(new_config, "uninstalled Waybar config")
    original_style = style_path.read_text()
    new_style = original_style
    if state.get("addedCssImport"):
        expected_hash = state.get("cssImportHash")
        if original_style.count(CSS_IMPORT) == 1 and expected_hash == text_hash(CSS_IMPORT):
            new_style = original_style.replace(CSS_IMPORT, "", 1)
        else:
            print("Preserving modified or duplicated CSS import")

    touched = [config_path, style_path, module_asset, css_asset, command_link, install_state_path]
    backup_dir = backup_destination(waybar_dir / ".waybar-ai-usage-backups", "-uninstall")
    if args.dry_run:
        print(f"Dry run passed. A real uninstall would back up files under {backup_dir}")
        return
    backup_files(touched, backup_dir)

    states = {path: snapshot(path) for path in touched}
    was_running = subprocess.run(["pgrep", "-x", "waybar"], stdout=subprocess.DEVNULL).returncode == 0
    try:
        atomic_write(config_path, new_config)
        atomic_write(style_path, new_style)
        if state.get("moduleAssetOwned") and module_asset.exists():
            expected = state.get("moduleAssetHash")
            if isinstance(expected, str) and file_hash(module_asset) == expected:
                module_asset.unlink()
            else:
                print(f"Preserving modified asset: {module_asset}")
        if state.get("cssAssetOwned") and css_asset.exists():
            expected = state.get("cssAssetHash")
            if isinstance(expected, str) and file_hash(css_asset) == expected:
                css_asset.unlink()
            else:
                print(f"Preserving modified asset: {css_asset}")
        if state.get("commandOwned") and command_link.is_symlink() and command_link.resolve() == (repo / "bin" / "waybar-ai-usage").resolve():
            command_link.unlink()
        install_state_path.unlink(missing_ok=True)
        restart_waybar(was_running)
    except Exception:
        for path, previous_state in states.items():
            restore(path, previous_state)
        if was_running:
            try:
                restart_waybar(True)
            except Exception:
                pass
        raise
    print("Waybar AI Usage uninstalled.")
    print(f"Backup: {backup_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Safely install Waybar AI Usage")
    parser.add_argument("--uninstall", action="store_true", help="Remove managed Waybar integration")
    parser.add_argument("--dry-run", action="store_true", help="Validate without changing files")
    parser.add_argument("--module-list", default="modules-center", help="Waybar module array to modify (default: modules-center)")
    parser.add_argument("--waybar-dir", default="~/.config/waybar", help=argparse.SUPPRESS)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    try:
        uninstall(args, repo) if args.uninstall else install(args, repo)
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Install failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
