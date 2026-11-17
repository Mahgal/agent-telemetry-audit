#!/usr/bin/env python3
"""Tests for telemetry_audit.py — stdlib unittest only.

Run:  python3 tests/run_tests.py
All fixtures live in temp HOME dirs; nothing touches the real $HOME.
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import telemetry_audit as ta  # noqa: E402

SCRIPT = Path(__file__).resolve().parent.parent / "telemetry_audit.py"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def audit_for(home: Path, env=None):
    return ta.run_audit(home, env if env is not None else {})


def tool(audit, name):
    return next(t for t in audit["tools"] if t["tool"] == name)


class TestDetection(unittest.TestCase):
    def test_cursor_on_detected(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            write(home / ".config/Cursor/User/settings.json",
                  '{"telemetry.telemetryLevel": "all"}')
            self.assertEqual(tool(audit_for(home), "Cursor")["status"], "ON")

    def test_vscode_off_detected(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            write(home / ".config/Code/User/settings.json",
                  '{"telemetry.telemetryLevel": "off"}')
            self.assertEqual(tool(audit_for(home), "VS Code")["status"], "OFF")

    def test_absent_file_is_not_installed(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            for name in ("Claude Code", "Cursor", "VS Code", "Codex",
                         "Gemini CLI", "OpenCode"):
                self.assertEqual(tool(audit_for(home), name)["status"],
                                 "NOT_INSTALLED", name)

    def test_claude_code_opt_out_via_settings_env_block(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            write(home / ".claude/settings.json",
                  '{"env": {"DISABLE_TELEMETRY": "1"}}')
            self.assertEqual(tool(audit_for(home), "Claude Code")["status"],
                             "OFF")

    def test_claude_code_no_opt_out_is_on(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            write(home / ".claude.json", '{"numStartups": 3}')
            self.assertEqual(tool(audit_for(home), "Claude Code")["status"],
                             "ON")

    def test_gemini_cli_enabled_true_is_on(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            write(home / ".gemini/settings.json",
                  '{"telemetry": {"enabled": true}}')
            self.assertEqual(tool(audit_for(home), "Gemini CLI")["status"],
                             "ON")

    def test_gemini_cli_absent_key_is_off_by_default(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            write(home / ".gemini/settings.json", '{"theme": "dark"}')
            self.assertEqual(tool(audit_for(home), "Gemini CLI")["status"],
                             "OFF")

    def test_codex_analytics_disabled_is_off(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            write(home / ".codex/config.toml",
                  'analytics.enabled = false\n[otel]\nexporter = "none"\n')
            self.assertEqual(tool(audit_for(home), "Codex")["status"], "OFF")

    def test_codex_analytics_enabled_is_on(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            write(home / ".codex/config.toml", 'analytics.enabled = true\n')
            self.assertEqual(tool(audit_for(home), "Codex")["status"], "ON")

    def test_opencode_off_by_default(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            write(home / ".config/opencode/opencode.json", '{"theme": "dark"}')
            self.assertEqual(tool(audit_for(home), "OpenCode")["status"],
                             "OFF")

    def test_opencode_otel_endpoint_is_on(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            write(home / ".config/opencode/opencode.json", '{}')
            result = tool(audit_for(home, {"OTEL_EXPORTER_OTLP_ENDPOINT":
                                           "http://localhost:4317"}),
                          "OpenCode")
            self.assertEqual(result["status"], "ON")

    def test_do_not_track_set_is_off(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            result = tool(audit_for(home, {"DO_NOT_TRACK": "1"}),
                          "DO_NOT_TRACK (env)")
            self.assertEqual(result["status"], "OFF")

    def test_do_not_track_unset_is_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            result = tool(audit_for(home, {}), "DO_NOT_TRACK (env)")
            self.assertEqual(result["status"], "UNKNOWN")


class TestRobustness(unittest.TestCase):
    def test_malformed_json_handled_gracefully(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            write(home / ".config/Code/User/settings.json",
                  '{"telemetry.telemetryLevel": ')
            result = tool(audit_for(home), "VS Code")
            self.assertEqual(result["status"], "UNKNOWN")
            self.assertIn("malformed", result["evidence"])

    def test_jsonc_comments_handled(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            write(home / ".config/Cursor/User/settings.json",
                  '// comment\n{"telemetry.telemetryLevel": "off",}')
            self.assertEqual(tool(audit_for(home), "Cursor")["status"], "OFF")

    def test_malformed_toml_handled_gracefully(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            write(home / ".codex/config.toml", 'analytics.enabled = [\n')
            result = tool(audit_for(home), "Codex")
            self.assertEqual(result["status"], "UNKNOWN")
            self.assertIn("malformed", result["evidence"])

    def test_other_telemetry_flags_listed(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            write(home / ".config/Code/User/settings.json",
                  '{"telemetry.telemetryLevel": "off", '
                  '"telemetry.enableCrashReporter": true}')
            result = tool(audit_for(home), "VS Code")
            self.assertTrue(any("enableCrashReporter" in f
                                for f in result["other_flags"]))

    def test_audit_completes_with_exit_zero(self):
        proc = subprocess.run([sys.executable, str(SCRIPT), "--demo"],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Cursor", proc.stdout)
        self.assertIn("ON", proc.stdout)


class TestOutputs(unittest.TestCase):
    def test_fix_output_contains_disable_command_for_each_on_tool(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            write(home / ".config/Cursor/User/settings.json",
                  '{"telemetry.telemetryLevel": "all"}')
            write(home / ".gemini/settings.json",
                  '{"telemetry": {"enabled": true}}')
            audit = audit_for(home)
            fix = ta.render_fix(audit)
            for name in ("Cursor", "Gemini CLI"):
                self.assertIn(name, fix)
            self.assertIn('"telemetry.telemetryLevel": "off"', fix)
            self.assertIn("GEMINI_TELEMETRY_ENABLED=false", fix)

    def test_json_output_parses(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            write(home / ".config/Code/User/settings.json",
                  '{"telemetry.telemetryLevel": "off"}')
            audit = audit_for(home)
            parsed = json.loads(json.dumps(audit))
            self.assertEqual(parsed["summary"]["OFF"], 1)
            self.assertEqual(len(parsed["tools"]), 7)

    def test_json_cli_parses(self):
        proc = subprocess.run([sys.executable, str(SCRIPT), "--demo", "--json"],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        parsed = json.loads(proc.stdout)
        self.assertEqual(parsed["summary"]["ON"], 1)
        self.assertEqual(parsed["summary"]["OFF"], 2)
        self.assertEqual(parsed["summary"]["NOT_INSTALLED"], 3)

    def test_demo_output_byte_identical_across_runs(self):
        outs = []
        for _ in range(2):
            proc = subprocess.run([sys.executable, str(SCRIPT), "--demo"],
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            outs.append(proc.stdout)
        self.assertEqual(outs[0], outs[1])

    def test_demo_summary_counts(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(ta.main(["--demo"]), 0)
        out = buf.getvalue()
        self.assertIn("Summary: 1 ON · 2 OFF · 1 UNKNOWN · 3 NOT_INSTALLED",
                      out)

    def test_usage_error_exits_one(self):
        proc = subprocess.run([sys.executable, str(SCRIPT), "--nope"],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
