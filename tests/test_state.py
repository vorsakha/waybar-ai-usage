from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
from contextlib import redirect_stdout
import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).parents[1] / "bin" / "waybar-ai-usage"
loader = SourceFileLoader("waybar_ai_usage", str(SCRIPT))
spec = spec_from_loader(loader.name, loader)
assert spec is not None
usage = module_from_spec(spec)
loader.exec_module(usage)


class UsageStateTests(unittest.TestCase):
    def test_settings_round_trip_and_validation(self):
        with tempfile.TemporaryDirectory() as temporary:
            config_dir = Path(temporary) / "config"
            settings_file = config_dir / "settings.json"
            chosen = {
                **usage.DEFAULT_SETTINGS,
                "displayMode": "single",
                "showTooltip": True,
                "providerSpacing": "comfortable",
            }
            with patch.object(usage, "CONFIG_DIR", config_dir), patch.object(
                usage, "SETTINGS_FILE", settings_file
            ):
                usage.write_settings(chosen)
                self.assertEqual(usage.read_settings(), chosen)
                self.assertEqual(os.stat(settings_file).st_mode & 0o777, 0o600)

    def test_compact_provider_options_are_independent(self):
        provider = {
            "windows": {
                "fiveHour": {"usedPercent": 42, "resetsAt": 13_900},
            }
        }
        settings = {
            **usage.DEFAULT_SETTINGS,
            "showPercentages": False,
            "showResetCountdown": True,
        }
        with patch.object(usage.time, "time", return_value=10_000):
            text = usage.compact_provider_text("claude", provider, settings)
        self.assertIn("󰚩</span> 1h5m", text)
        self.assertNotIn("42%", text)

    def provider(self, updated_at: float, percent: float, **extra):
        return {
            "name": "Provider",
            "updatedAt": updated_at,
            "windows": {"weekly": {"usedPercent": percent}},
            **extra,
        }

    @patch.object(usage.time, "time", return_value=10_000)
    def test_one_stale_provider_marks_whole_indicator_stale(self, _time):
        data = {
            "providers": {
                "claude": self.provider(9_950, 10),
                "codex": self.provider(8_000, 20),
            }
        }
        state, highest, stale = usage.usage_state(data)
        self.assertEqual(state, "normal")
        self.assertEqual(highest, 20)
        self.assertTrue(stale)

    @patch.object(usage.time, "time", return_value=10_000)
    def test_failed_refresh_reports_cached_data_age(self, _time):
        data = {
            "providers": {
                "claude": self.provider(9_880, 10, error="offline", stale=True),
                "codex": self.provider(9_900, 20),
            }
        }
        self.assertEqual(usage.data_freshness_text(data), "Refresh failed · data 2m ago")
        self.assertTrue(usage.usage_state(data)[2])

    @patch.object(usage.time, "time", return_value=10_000)
    def test_waybar_text_shows_each_provider_highest_percentage(self, _time):
        data = {
            "attemptedAt": 9_990,
            "providers": {
                "claude": {
                    **self.provider(9_990, 12),
                    "windows": {
                        "fiveHour": {"usedPercent": 12, "resetsAt": 11_800},
                        "weekly": {"usedPercent": 34, "resetsAt": 97_000},
                    },
                },
                "codex": {
                    **self.provider(9_990, 56),
                    "windows": {"weekly": {"usedPercent": 56, "resetsAt": 13_900}},
                },
            },
        }
        output = io.StringIO()
        with patch.object(usage, "read_json", return_value=data), patch.object(
            usage, "read_settings", return_value=dict(usage.DEFAULT_SETTINGS)
        ), patch.object(usage, "spawn_background_refresh"), redirect_stdout(output):
            usage.waybar_output()
        payload = json.loads(output.getvalue())
        self.assertIn("#D97757\">󰚩</span> 34%", payload["text"])
        self.assertIn("#10A37F\">󰚩</span> 56%", payload["text"])
        self.assertNotIn("1d", payload["text"])
        self.assertNotIn("1h5m", payload["text"])
        self.assertEqual(payload["tooltip"], "")

        alternate = {**usage.DEFAULT_SETTINGS, "displayMode": "single", "showTooltip": True}
        output = io.StringIO()
        with patch.object(usage, "read_json", return_value=data), patch.object(
            usage, "read_settings", return_value=alternate
        ), patch.object(usage, "spawn_background_refresh"), redirect_stdout(output):
            usage.waybar_output()
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["text"], usage.ICON)
        self.assertIn("Weekly", payload["tooltip"])

    @patch.object(usage.time, "time", return_value=10_000)
    def test_high_usage_sets_critical_without_stale(self, _time):
        data = {
            "providers": {
                "claude": self.provider(9_990, 92),
                "codex": self.provider(9_990, 30),
            }
        }
        self.assertEqual(usage.usage_state(data), ("critical", 92, False))


if __name__ == "__main__":
    unittest.main()
