from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
from contextlib import redirect_stdout
import argparse
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

    def test_fetch_claude_parses_numeric_and_http_date_retry_after(self):
        cases = [
            ("2915", 2_915),
            ("Thu, 01 Jan 1970 02:00:00 GMT", 6_200),
        ]
        credentials = {"claudeAiOauth": {"accessToken": "test-token"}}
        for header, expected in cases:
            with self.subTest(header=header):
                response = usage.urllib.error.HTTPError(
                    usage.CLAUDE_USAGE_URL,
                    429,
                    "rate limited",
                    {"Retry-After": header},
                    None,
                )
                with patch.object(usage, "read_json", return_value=credentials), patch.object(
                    usage.urllib.request, "urlopen", side_effect=response
                ), patch.object(usage.time, "time", return_value=1_000):
                    with self.assertRaises(usage.UsageRateLimitError) as raised:
                        usage.fetch_claude()
                self.assertEqual(raised.exception.retry_after, expected)
                if raised.exception.__cause__:
                    raised.exception.__cause__.close()
                    raised.exception.__cause__ = None

    def test_fetch_claude_marks_unauthorized_session_expired(self):
        credentials = {"claudeAiOauth": {"accessToken": "expired-token"}}
        response = usage.urllib.error.HTTPError(usage.CLAUDE_USAGE_URL, 401, "unauthorized", {}, None)
        with patch.object(usage, "read_json", return_value=credentials), patch.object(
            usage.urllib.request, "urlopen", side_effect=response
        ):
            with self.assertRaisesRegex(usage.UsageAuthError, "session expired") as raised:
                usage.fetch_claude()
        if raised.exception.__cause__:
            raised.exception.__cause__.close()
            raised.exception.__cause__ = None

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

    def test_manual_force_bypasses_errors_but_not_active_rate_limit(self):
        ordinary_error = self.provider(9_000, 10, error="offline", nextRefreshAt=12_000)
        rate_limit = self.provider(9_000, 10, rateLimited=True, backoffUntil=12_000, nextRefreshAt=13_600)
        self.assertTrue(usage.provider_refresh_due(ordinary_error, 3_600, 10_000, force=True))
        self.assertFalse(usage.provider_refresh_due(rate_limit, 3_600, 10_000, force=True))
        self.assertTrue(usage.provider_refresh_due(rate_limit, 3_600, 12_001, force=True))

    def test_only_transient_codex_errors_use_fast_retry(self):
        transient = usage.CodexTransientError("Codex usage request timed out")
        permanent = RuntimeError("Codex returned an invalid response")
        self.assertEqual(usage.provider_error_retry_seconds("codex", transient, 600), 60)
        self.assertEqual(usage.provider_error_retry_seconds("codex", permanent, 600), 600)
        self.assertTrue(usage.codex_error_is_transient("error sending request for url"))
        self.assertFalse(usage.codex_error_is_transient("unknown JSON-RPC method"))

    def test_cache_refresh_due_uses_provider_deadlines_not_global_attempt_time(self):
        data = {
            "attemptedAt": 10_000,
            "providers": {
                "claude": self.provider(9_900, 10, nextRefreshAt=20_000),
                "codex": self.provider(9_900, 20, error="timeout", nextRefreshAt=9_999),
            },
        }
        self.assertTrue(usage.cache_refresh_due(data, now=10_000))

    def test_manual_refresh_command_forces_due_check(self):
        args = argparse.Namespace(background=False, notify=False)
        with patch.object(usage, "refresh_cache", return_value=(usage.empty_cache(), True)) as refresh:
            usage.refresh_command(args)
        refresh.assert_called_once_with(blocking_lock=True, force=True)

    @patch.object(usage.time, "time", return_value=10_000)
    def test_refresh_cache_skips_claude_until_hourly_deadline(self, _time):
        with tempfile.TemporaryDirectory() as temporary:
            cache_dir = Path(temporary)
            cache_file = cache_dir / "usage.json"
            lock_file = cache_dir / "refresh.lock"
            cache_file.write_text(json.dumps({
                "attemptedAt": 9_000,
                "providers": {
                    "claude": self.provider(9_500, 10, nextRefreshAt=12_000),
                    "codex": self.provider(9_000, 20, nextRefreshAt=9_500),
                },
            }))
            refreshed_codex = self.provider(10_000, 21)
            with patch.object(usage, "CACHE_DIR", cache_dir), patch.object(
                usage, "CACHE_FILE", cache_file
            ), patch.object(usage, "LOCK_FILE", lock_file), patch.object(
                usage, "fetch_claude"
            ) as claude, patch.object(
                usage, "fetch_codex", return_value=refreshed_codex
            ), patch.object(usage, "signal_waybar"):
                data, changed = usage.refresh_cache()
            self.assertTrue(changed)
            claude.assert_not_called()
            self.assertEqual(data["providers"]["claude"]["updatedAt"], 9_500)
            self.assertEqual(data["providers"]["codex"]["windows"]["weekly"]["usedPercent"], 21)
            self.assertEqual(data["providers"]["codex"]["nextRefreshAt"], 10_600)

    @patch.object(usage.time, "time", return_value=10_000)
    def test_claude_rate_limit_sets_hourly_backoff_without_discarding_cache(self, _time):
        with tempfile.TemporaryDirectory() as temporary:
            cache_dir = Path(temporary)
            cache_file = cache_dir / "usage.json"
            lock_file = cache_dir / "refresh.lock"
            cache_file.write_text(json.dumps({
                "attemptedAt": 9_000,
                "providers": {
                    "claude": self.provider(5_000, 10),
                    "codex": self.provider(9_900, 20, nextRefreshAt=20_000),
                },
            }))
            error = usage.UsageRateLimitError("Claude usage endpoint rate limited", 2_915)
            with patch.object(usage, "CACHE_DIR", cache_dir), patch.object(
                usage, "CACHE_FILE", cache_file
            ), patch.object(usage, "LOCK_FILE", lock_file), patch.object(
                usage, "fetch_claude", side_effect=error
            ), patch.object(usage, "fetch_codex") as codex, patch.object(usage, "signal_waybar"):
                data, _changed = usage.refresh_cache()
            codex.assert_not_called()
            claude = data["providers"]["claude"]
            self.assertEqual(claude["windows"]["weekly"]["usedPercent"], 10)
            self.assertTrue(claude["rateLimited"])
            self.assertEqual(claude["backoffUntil"], 12_915)
            self.assertEqual(claude["nextRefreshAt"], 13_600)
            self.assertTrue(usage.provider_refresh_due(claude, 3_600, 13_000, force=True))
            self.assertNotIn("stale", claude)
            self.assertEqual(usage.data_freshness_text(data), "Claude delayed · data 1h ago")

    @patch.object(usage.time, "time", return_value=10_000)
    def test_claude_auth_failure_is_actionable_and_codex_timeout_retries_quickly(self, _time):
        with tempfile.TemporaryDirectory() as temporary:
            cache_dir = Path(temporary)
            cache_file = cache_dir / "usage.json"
            lock_file = cache_dir / "refresh.lock"
            cache_file.write_text(json.dumps({
                "providers": {
                    "claude": self.provider(5_000, 10),
                    "codex": self.provider(5_000, 20),
                },
            }))
            with patch.object(usage, "CACHE_DIR", cache_dir), patch.object(
                usage, "CACHE_FILE", cache_file
            ), patch.object(usage, "LOCK_FILE", lock_file), patch.object(
                usage, "fetch_claude", side_effect=usage.UsageAuthError("Claude session expired")
            ), patch.object(
                usage, "fetch_codex", side_effect=usage.CodexTransientError("Codex usage request timed out")
            ), patch.object(usage, "signal_waybar"):
                data, _changed = usage.refresh_cache()
            claude = data["providers"]["claude"]
            codex = data["providers"]["codex"]
            self.assertTrue(claude["authExpired"])
            self.assertEqual(claude["nextRefreshAt"], 13_600)
            self.assertEqual(codex["nextRefreshAt"], 10_060)
            self.assertIn("open Claude Code", usage.provider_tooltip(claude)[-1])

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
