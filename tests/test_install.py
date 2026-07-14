from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
import argparse
import json
import os
import shutil
import tempfile
import types
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).parents[1] / "scripts" / "install.py"
loader = SourceFileLoader("waybar_ai_usage_install", str(SCRIPT))
spec = spec_from_loader(loader.name, loader)
assert spec is not None
installer = module_from_spec(spec)
loader.exec_module(installer)

SNIPPET = '''"custom/ai-usage": {
  "exec": "waybar-ai-usage waybar",
  "return-type": "json"
}
'''


class InstallerTests(unittest.TestCase):
    def sample(self):
        return '''{
  // Waybar comment
  "modules-center": ["clock", "custom/weather",],
  "clock": {"tooltip": "https://example.com/a//b",},
}
'''

    def test_jsonc_parser_preserves_comment_markers_inside_strings(self):
        parsed = json.loads(installer.strip_jsonc(self.sample()))
        self.assertEqual(parsed["clock"]["tooltip"], "https://example.com/a//b")

    def test_structural_scanner_ignores_braces_and_brackets_in_comments_and_strings(self):
        config = '''/* fake root { [ ] } */
{
  "note": "literal { and ] stay inside this string",
  "modules-center": ["clock", /* fake close ] */ "custom/weather"]
}
'''
        patched = installer.add_module_definition(config, SNIPPET)
        patched = installer.add_module_to_list(patched, "modules-center")
        parsed = json.loads(installer.strip_jsonc(patched))
        self.assertEqual(parsed["note"], "literal { and ] stay inside this string")
        self.assertIn(installer.MODULE_NAME, parsed["modules-center"])
        self.assertIn(installer.MODULE_NAME, parsed)

    def test_duplicate_module_entries_are_rejected(self):
        config = self.sample().replace(
            '"clock", "custom/weather",',
            '"clock", "custom/ai-usage", "custom/ai-usage",',
        )
        with self.assertRaises(RuntimeError):
            installer.add_module_to_list(config, "modules-center")

    def test_install_patch_is_valid_and_idempotent(self):
        patched = installer.add_module_definition(self.sample(), SNIPPET)
        patched = installer.add_module_to_list(patched, "modules-center")
        installer.validate_jsonc(patched, "test")
        patched_again = installer.add_module_definition(patched, SNIPPET)
        patched_again = installer.add_module_to_list(patched_again, "modules-center")
        self.assertEqual(patched_again, patched)
        parsed = json.loads(installer.strip_jsonc(patched))
        self.assertEqual(parsed["modules-center"].count(installer.MODULE_NAME), 1)
        self.assertEqual(parsed[installer.MODULE_NAME]["return-type"], "json")

    def test_uninstall_removes_managed_definition_and_module_entry(self):
        patched = installer.add_module_definition(self.sample(), SNIPPET)
        patched = installer.add_module_to_list(patched, "modules-center")
        cleaned = installer.remove_managed_module(patched)
        cleaned = installer.remove_module_from_lists(cleaned)
        installer.validate_jsonc(cleaned, "test")
        parsed = json.loads(installer.strip_jsonc(cleaned))
        self.assertNotIn(installer.MODULE_NAME, parsed)
        self.assertNotIn(installer.MODULE_NAME, parsed["modules-center"])

    def test_commented_definition_does_not_block_real_definition(self):
        config = self.sample().replace(
            "// Waybar comment",
            '// "custom/ai-usage": {"exec": "commented"}\n  /* "custom/ai-usage": block comment */',
        )
        patched = installer.add_module_definition(config, SNIPPET)
        parsed = json.loads(installer.strip_jsonc(patched))
        self.assertIn(installer.MODULE_NAME, parsed)
        self.assertIn(installer.MANAGED_START, patched)

    def test_existing_user_definition_is_not_duplicated_or_managed(self):
        config = self.sample().replace(
            '"clock":',
            '"custom/ai-usage": {"exec": "custom-command"},\n  "clock":',
        )
        patched = installer.add_module_definition(config, SNIPPET)
        self.assertEqual(patched.count('"custom/ai-usage"'), 1)
        self.assertNotIn(installer.MANAGED_START, patched)

    def test_changing_owned_module_list_moves_instead_of_duplicates(self):
        config = self.sample().replace(
            '"clock": {',
            '"modules-right": ["network"],\n  "clock": {',
        )
        installed = installer.add_module_to_list(config, "modules-center")
        moved = installer.remove_module_from_lists(installed)
        moved = installer.add_module_to_list(moved, "modules-right")
        parsed = json.loads(installer.strip_jsonc(moved))
        self.assertNotIn(installer.MODULE_NAME, parsed["modules-center"])
        self.assertEqual(parsed["modules-right"].count(installer.MODULE_NAME), 1)

    def test_backup_destinations_do_not_collide(self):
        root = Path("/tmp/backups")
        self.assertNotEqual(installer.backup_destination(root), installer.backup_destination(root))

    def run_without_waybar(self, function, *args):
        result = types.SimpleNamespace(returncode=1)
        with patch.object(installer.subprocess, "run", return_value=result):
            return function(*args)

    def test_clean_install_and_uninstall_only_managed_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home"
            waybar = home / ".config" / "waybar"
            waybar.mkdir(parents=True)
            (waybar / "config.jsonc").write_text(self.sample())
            (waybar / "style.css").write_text("* { color: white; }\n")
            args = argparse.Namespace(waybar_dir=str(waybar), module_list="modules-center", dry_run=False)
            with patch.dict(os.environ, {"HOME": str(home)}):
                self.run_without_waybar(installer.install, args, SCRIPT.parents[1])
                state = installer.read_install_state(waybar / installer.INSTALL_STATE_NAME)
                self.assertTrue(state["managedDefinition"])
                self.assertTrue(state["addedListEntry"])
                self.assertTrue(state["addedCssImport"])
                self.run_without_waybar(installer.uninstall, args, SCRIPT.parents[1])
            parsed = json.loads(installer.strip_jsonc((waybar / "config.jsonc").read_text()))
            self.assertNotIn(installer.MODULE_NAME, parsed)
            self.assertNotIn(installer.MODULE_NAME, parsed["modules-center"])
            self.assertNotIn(installer.CSS_IMPORT, (waybar / "style.css").read_text())
            self.assertFalse((waybar / installer.INSTALL_STATE_NAME).exists())

    def test_modified_owned_asset_is_not_overwritten_or_deleted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home"
            waybar = home / ".config" / "waybar"
            waybar.mkdir(parents=True)
            (waybar / "config.jsonc").write_text(self.sample())
            (waybar / "style.css").write_text("* { color: white; }\n")
            args = argparse.Namespace(waybar_dir=str(waybar), module_list="modules-center", dry_run=False)
            with patch.dict(os.environ, {"HOME": str(home)}):
                self.run_without_waybar(installer.install, args, SCRIPT.parents[1])
                css_asset = waybar / "ai-usage.css"
                css_asset.write_text("/* user replacement */\n")
                with self.assertRaises(RuntimeError):
                    self.run_without_waybar(installer.install, args, SCRIPT.parents[1])
                self.run_without_waybar(installer.uninstall, args, SCRIPT.parents[1])
            self.assertEqual(css_asset.read_text(), "/* user replacement */\n")

    def test_retargeted_owned_command_link_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home"
            waybar = home / ".config" / "waybar"
            waybar.mkdir(parents=True)
            (waybar / "config.jsonc").write_text(self.sample())
            (waybar / "style.css").write_text("* { color: white; }\n")
            replacement = root / "replacement-command"
            replacement.write_text("#!/bin/sh\n")
            args = argparse.Namespace(waybar_dir=str(waybar), module_list="modules-center", dry_run=False)
            with patch.dict(os.environ, {"HOME": str(home)}):
                self.run_without_waybar(installer.install, args, SCRIPT.parents[1])
                command = home / ".local" / "bin" / "waybar-ai-usage"
                command.unlink()
                command.symlink_to(replacement)
                with self.assertRaises(RuntimeError):
                    self.run_without_waybar(installer.install, args, SCRIPT.parents[1])
            self.assertTrue(command.is_symlink())
            self.assertEqual(command.resolve(), replacement.resolve())

    def test_restart_failure_rolls_back_all_installed_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home"
            waybar = home / ".config" / "waybar"
            waybar.mkdir(parents=True)
            config = self.sample()
            style = "* { color: white; }\n"
            (waybar / "config.jsonc").write_text(config)
            (waybar / "style.css").write_text(style)
            args = argparse.Namespace(waybar_dir=str(waybar), module_list="modules-center", dry_run=False)
            running = types.SimpleNamespace(returncode=0)
            with patch.dict(os.environ, {"HOME": str(home)}), patch.object(
                installer.subprocess, "run", return_value=running
            ), patch.object(installer, "restart_waybar", side_effect=RuntimeError("restart failed")):
                with self.assertRaises(RuntimeError):
                    installer.install(args, SCRIPT.parents[1])
            self.assertEqual((waybar / "config.jsonc").read_text(), config)
            self.assertEqual((waybar / "style.css").read_text(), style)
            self.assertFalse((waybar / installer.INSTALL_STATE_NAME).exists())
            self.assertFalse((waybar / "ai-usage-module.jsonc").exists())
            self.assertFalse((waybar / "ai-usage.css").exists())
            self.assertFalse((home / ".local" / "bin" / "waybar-ai-usage").exists())

    def test_uninstall_preserves_user_modified_owned_fragments(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home"
            waybar = home / ".config" / "waybar"
            waybar.mkdir(parents=True)
            (waybar / "config.jsonc").write_text(self.sample())
            (waybar / "style.css").write_text("* { color: white; }\n")
            args = argparse.Namespace(waybar_dir=str(waybar), module_list="modules-center", dry_run=False)
            with patch.dict(os.environ, {"HOME": str(home)}):
                self.run_without_waybar(installer.install, args, SCRIPT.parents[1])
                config = (waybar / "config.jsonc").read_text()
                config = config.replace('"interval": 60', '"interval": 61')
                config = config.replace('"custom/ai-usage"]', '"custom/ai-usage", "custom/user"]')
                (waybar / "config.jsonc").write_text(config)
                with (waybar / "style.css").open("a") as stream:
                    stream.write(installer.CSS_IMPORT + "\n")
                self.run_without_waybar(installer.uninstall, args, SCRIPT.parents[1])
            preserved = (waybar / "config.jsonc").read_text()
            self.assertIn('"interval": 61', preserved)
            self.assertIn(installer.MODULE_NAME, json.loads(installer.strip_jsonc(preserved))["modules-center"])
            self.assertEqual((waybar / "style.css").read_text().count(installer.CSS_IMPORT), 2)

    def test_uninstall_preserves_preexisting_user_integration(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home"
            waybar = home / ".config" / "waybar"
            bin_dir = home / ".local" / "bin"
            waybar.mkdir(parents=True)
            bin_dir.mkdir(parents=True)
            config = self.sample().replace(
                '"modules-center": ["clock", "custom/weather",],',
                '"modules-center": ["clock", "custom/weather", "custom/ai-usage"],',
            ).replace(
                '"clock":',
                '"custom/ai-usage": {"exec": "user-command"},\n  "clock":',
            )
            style = "#custom-ai-usage { color: red; }\n"
            (waybar / "config.jsonc").write_text(config)
            (waybar / "style.css").write_text(style)
            shutil.copy2(SCRIPT.parents[1] / "waybar" / "module.jsonc", waybar / "ai-usage-module.jsonc")
            shutil.copy2(SCRIPT.parents[1] / "waybar" / "style.css", waybar / "ai-usage.css")
            command = bin_dir / "waybar-ai-usage"
            command.symlink_to(SCRIPT.parents[1] / "bin" / "waybar-ai-usage")
            args = argparse.Namespace(waybar_dir=str(waybar), module_list="modules-center", dry_run=False)
            with patch.dict(os.environ, {"HOME": str(home)}):
                self.run_without_waybar(installer.install, args, SCRIPT.parents[1])
                state = installer.read_install_state(waybar / installer.INSTALL_STATE_NAME)
                self.assertFalse(state["managedDefinition"])
                self.assertFalse(state["addedListEntry"])
                self.assertFalse(state["addedCssImport"])
                self.run_without_waybar(installer.uninstall, args, SCRIPT.parents[1])
            self.assertEqual((waybar / "config.jsonc").read_text(), config)
            self.assertEqual((waybar / "style.css").read_text(), style)
            self.assertTrue((waybar / "ai-usage-module.jsonc").exists())
            self.assertTrue((waybar / "ai-usage.css").exists())
            self.assertTrue(command.is_symlink())


if __name__ == "__main__":
    unittest.main()
