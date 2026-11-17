#!/usr/bin/env python3
"""agent-telemetry-audit — audit your own machine for coding-agent telemetry.

Windows 11 now logs which coding agents and developer tools you run
(xusheng.dev, 2026-11-16, Hacker News top story 2026-11-17). The OS-level
part needs Windows Settings, but the tools themselves also phone home —
each with its own opt-out, hidden in a different config file.

This script reads those config files on YOUR machine and reports, per tool,
whether telemetry is ON, OFF, UNKNOWN, or the tool is NOT_INSTALLED,
with the exact file + setting as evidence and the exact setting to turn
it off. It never edits anything: --fix only prints the commands.

    python3 telemetry_audit.py            audit $HOME
    python3 telemetry_audit.py --demo     reproducible demo (fake HOME)
    python3 telemetry_audit.py --fix      print how to disable everything ON
    python3 telemetry_audit.py --json     machine-readable output

Exit code is 0 on a completed audit (findings are data, not failure)
and 1 only on a usage error. Python 3.11+ standard library only.
"""

import argparse
import json
import os
import sys
import tomllib
from pathlib import Path

__version__ = "1.0.0"

# Keys whose presence in a config file is worth surfacing even when they
# are not the tool's main telemetry switch (crash reporting, prompt
# logging, update checks, conversation sharing, ...).
OTHER_FLAG_WORDS = ("telemetry", "analytics", "crash", "usage",
                    "error_report", "errorreport", "otel", "share",
                    "autoupdate", "feedback")

# ---------------------------------------------------------------- helpers

def display_path(home: Path, path: Path) -> str:
    """Show a path as ~/.config/... so output is stable across machines."""
    try:
        return "~/" + str(path.relative_to(home))
    except ValueError:
        return str(path)


def flatten(obj, prefix=""):
    """Yield (dotted.key.path, scalar value) for nested dicts/lists."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            new = f"{prefix}.{key}" if prefix else str(key)
            yield from flatten(value, new)
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            yield from flatten(value, f"{prefix}[{i}]")
    else:
        yield (prefix, obj)


def is_telemetry_key(key: str) -> bool:
    lowered = key.lower()
    return any(word in lowered for word in OTHER_FLAG_WORDS)


def other_flags(data) -> list:
    """All telemetry-ish keys found in a parsed config, as 'key = value'."""
    found = []
    for key, value in flatten(data):
        if is_telemetry_key(key):
            found.append(f"{key} = {json.dumps(value)}")
    return found


def strip_jsonc(text: str) -> str:
    """Best-effort strip of // and /* */ comments + trailing commas.

    VS Code / Cursor settings.json files are JSONC in practice, so plain
    json.loads fails on them. This is string-aware: comment markers
    inside quoted strings are left alone.
    """
    out = []
    i, n = 0, len(text)
    in_str = False
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
            i += 1
            continue
        if text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        out.append(ch)
        i += 1
    text = "".join(out)
    # trailing commas before } or ]
    cleaned = []
    i, n = 0, len(text)
    in_str = False
    while i < n:
        ch = text[i]
        if in_str:
            cleaned.append(ch)
            if ch == "\\" and i + 1 < n:
                cleaned.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            cleaned.append(ch)
            i += 1
            continue
        if ch == ",":
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j < n and text[j] in "}]":
                i += 1  # drop the trailing comma
                continue
        cleaned.append(ch)
        i += 1
    return "".join(cleaned)


def load_json_file(path: Path):
    """Return (data, error). data is None when unreadable or malformed."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, f"unreadable ({exc.strerror or exc})"
    try:
        return json.loads(text), None
    except json.JSONDecodeError:
        pass
    try:
        return json.loads(strip_jsonc(text)), None
    except json.JSONDecodeError as exc:
        return None, f"malformed JSON ({exc.msg} at line {exc.lineno})"


def load_toml_file(path: Path):
    """Return (data, error). data is None when unreadable or malformed."""
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh), None
    except OSError as exc:
        return None, f"unreadable ({exc.strerror or exc})"
    except tomllib.TOMLDecodeError as exc:
        return None, f"malformed TOML ({exc})"


def result(tool, status, evidence, disable, files_checked, flags=None):
    return {
        "tool": tool,
        "status": status,
        "evidence": evidence,
        "disable": disable,
        "files_checked": files_checked,
        "other_flags": flags or [],
    }

# ---------------------------------------------------------------- checkers
# Each checker returns the result dict for one tool. A checker only ever
# reports what it actually read; a missing file is NOT_INSTALLED, a file
# with no explicit switch is UNKNOWN (with the documented default noted).

def check_claude_code(home: Path, env: dict):
    files = [home / ".claude.json", home / ".claude" / "settings.json"]
    checked = [display_path(home, p) for p in files]
    existing = [p for p in files if p.is_file()]
    if not existing:
        return result("Claude Code", "NOT_INSTALLED",
                      "not found: " + ", ".join(checked),
                      "Not installed — nothing to disable.",
                      checked)
    flags = []
    opt_out = None  # (where, value)
    for path in existing:
        data, err = load_json_file(path)
        if err:
            return result("Claude Code", "UNKNOWN",
                          f"{display_path(home, path)}: {err}",
                          "Fix the file, then re-run this audit.",
                          checked)
        flags.extend(other_flags(data))
        # task-specified keys first
        for key in ("telemetry", "telemetryEnabled"):
            if key in data and isinstance(data[key], bool):
                opt_out = (f"{display_path(home, path)}: {key}", data[key])
                if data[key] is False:
                    return result("Claude Code", "OFF",
                                  f"{display_path(home, path)}: {key} = false",
                                  "Already off.",
                                  checked, flags)
        # env block in settings.json (the documented opt-out surface)
        env_block = data.get("env") if isinstance(data, dict) else None
        if isinstance(env_block, dict):
            for var in ("DISABLE_TELEMETRY",
                        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"):
                if env_block.get(var):
                    opt_out = (f"{display_path(home, path)} env.{var}",
                               env_block[var])
                    return result("Claude Code", "OFF",
                                  f"{display_path(home, path)}: "
                                  f"env.{var} = {json.dumps(env_block[var])}",
                                  "Already off.",
                                  checked, flags)
            if env_block.get("DO_NOT_TRACK") == "1":
                return result("Claude Code", "OFF",
                              f"{display_path(home, path)}: "
                              f"env.DO_NOT_TRACK = \"1\"",
                              "Already off.",
                              checked, flags)
    # process environment counts too (shell profile export)
    for var in ("DISABLE_TELEMETRY", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"):
        if env.get(var):
            return result("Claude Code", "OFF",
                          f"environment: {var}={env[var]}",
                          "Already off (set in this shell).",
                          checked, flags)
    if env.get("DO_NOT_TRACK") == "1":
        return result("Claude Code", "OFF",
                      "environment: DO_NOT_TRACK=1",
                      "Already off (set in this shell).",
                      checked, flags)
    where = f" ({opt_out[0]} = {opt_out[1]})" if opt_out else ""
    return result(
        "Claude Code", "ON",
        "no opt-out set in " + ", ".join(display_path(home, p) for p in existing)
        + " and no DISABLE_TELEMETRY/DO_NOT_TRACK in the environment"
        + " (default: usage metrics on)" + where,
        "Add to ~/.claude/settings.json:\n"
        '  { "env": { "DISABLE_TELEMETRY": "1" } }\n'
        "or export DISABLE_TELEMETRY=1 in your shell profile.\n"
        "Stronger (also kills update checks, surveys, feature flags):\n"
        "  export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1",
        checked, flags)


def _vscode_family(name, rel_path, home: Path, env: dict):
    path = home / rel_path
    checked = [display_path(home, path)]
    if not path.is_file():
        return result(name, "NOT_INSTALLED",
                      f"not found: {checked[0]}",
                      "Not installed — nothing to disable.",
                      checked)
    data, err = load_json_file(path)
    if err:
        return result(name, "UNKNOWN",
                      f"{checked[0]}: {err}",
                      "Fix the file, then re-run this audit.",
                      checked)
    flags = other_flags(data)
    level = data.get("telemetry.telemetryLevel")
    disable = ("Set in " + checked[0] + ":\n"
               '  "telemetry.telemetryLevel": "off"')
    if level == "off":
        return result(name, "OFF",
                      f"{checked[0]}: telemetry.telemetryLevel = \"off\"",
                      "Already off.", checked, flags)
    if level in ("all", "error", "crash"):
        return result(name, "ON",
                      f"{checked[0]}: telemetry.telemetryLevel = \"{level}\"",
                      disable, checked, flags)
    default_note = (" (VS Code default is \"all\")"
                    if name == "VS Code" else " (default unverified)")
    return result(name, "UNKNOWN",
                  f"{checked[0]}: telemetry.telemetryLevel not set"
                  + default_note,
                  disable + "\n(not explicitly disabled — apply it to be sure.)",
                  checked, flags)


def check_cursor(home: Path, env: dict):
    return _vscode_family("Cursor", ".config/Cursor/User/settings.json",
                          home, env)


def check_vscode(home: Path, env: dict):
    return _vscode_family("VS Code", ".config/Code/User/settings.json",
                          home, env)


def check_codex(home: Path, env: dict):
    path = home / ".codex" / "config.toml"
    checked = [display_path(home, path)]
    if not path.is_file():
        return result("Codex", "NOT_INSTALLED",
                      f"not found: {checked[0]}",
                      "Not installed — nothing to disable.",
                      checked)
    data, err = load_toml_file(path)
    if err:
        return result("Codex", "UNKNOWN",
                      f"{checked[0]}: {err}",
                      "Fix the file, then re-run this audit.",
                      checked)
    flags = other_flags(data)
    analytics = None
    if isinstance(data.get("analytics"), dict):
        analytics = data["analytics"].get("enabled")
    if analytics is None:
        analytics = data.get("analytics_enabled")
    otel = data.get("otel") if isinstance(data.get("otel"), dict) else {}
    exporter = otel.get("exporter")
    disable = ("Add to ~/.codex/config.toml:\n"
               "  analytics.enabled = false\n"
               "  [otel]\n"
               '  exporter = "none"\n'
               "  log_user_prompt = false")
    if analytics is False:
        return result("Codex", "OFF",
                      f"{checked[0]}: analytics.enabled = false",
                      "Analytics off. Also check the [otel] block: "
                      "exporter should be \"none\".",
                      checked, flags)
    if analytics is True:
        return result("Codex", "ON",
                      f"{checked[0]}: analytics.enabled = true",
                      disable, checked, flags)
    if isinstance(exporter, dict) or (
            isinstance(exporter, str) and exporter != "none"):
        return result("Codex", "ON",
                      f"{checked[0]}: [otel] exporter = "
                      f"{json.dumps(exporter)} (exporting)",
                      disable, checked, flags)
    return result("Codex", "UNKNOWN",
                  f"{checked[0]}: analytics.enabled not set "
                  "(default: anonymous usage metrics on; "
                  "OTel export is off by default)",
                  disable + "\n(not explicitly disabled — apply it to be sure.)",
                  checked, flags)


def check_gemini(home: Path, env: dict):
    path = home / ".gemini" / "settings.json"
    checked = [display_path(home, path)]
    if not path.is_file():
        return result("Gemini CLI", "NOT_INSTALLED",
                      f"not found: {checked[0]}",
                      "Not installed — nothing to disable.",
                      checked)
    data, err = load_json_file(path)
    if err:
        return result("Gemini CLI", "UNKNOWN",
                      f"{checked[0]}: {err}",
                      "Fix the file, then re-run this audit.",
                      checked)
    flags = other_flags(data)
    telemetry = data.get("telemetry") if isinstance(data.get("telemetry"), dict) else {}
    enabled = telemetry.get("enabled")
    env_override = env.get("GEMINI_TELEMETRY_ENABLED")
    disable = ("Set in ~/.gemini/settings.json:\n"
               '  { "telemetry": { "enabled": false, "logPrompts": false } }\n'
               "or: export GEMINI_TELEMETRY_ENABLED=false")
    if env_override in ("true", "1"):
        return result("Gemini CLI", "ON",
                      "environment: GEMINI_TELEMETRY_ENABLED="
                      f"{env_override} (overrides settings.json)",
                      disable, checked, flags)
    if env_override in ("false", "0"):
        return result("Gemini CLI", "OFF",
                      "environment: GEMINI_TELEMETRY_ENABLED="
                      f"{env_override}",
                      "Already off (set in this shell).",
                      checked, flags)
    if enabled is True:
        return result("Gemini CLI", "ON",
                      f"{checked[0]}: telemetry.enabled = true",
                      disable, checked, flags)
    if enabled is False:
        return result("Gemini CLI", "OFF",
                      f"{checked[0]}: telemetry.enabled = false",
                      "Already off. Also set telemetry.logPrompts = false "
                      "if you ever enable telemetry.",
                      checked, flags)
    return result("Gemini CLI", "OFF",
                  f"{checked[0]}: telemetry.enabled not set "
                  "(default: false — telemetry is opt-in)",
                  "Already off by default. To be explicit, set "
                  "telemetry.enabled = false.",
                  checked, flags)


def check_opencode(home: Path, env: dict):
    path = home / ".config" / "opencode" / "opencode.json"
    checked = [display_path(home, path)]
    endpoint = env.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not path.is_file():
        if endpoint:
            return result("OpenCode", "ON",
                          "environment: OTEL_EXPORTER_OTLP_ENDPOINT="
                          f"{endpoint} (no config file found)",
                          "Unset OTEL_EXPORTER_OTLP_ENDPOINT — "
                          "OpenCode exports nothing without it.",
                          checked)
        return result("OpenCode", "NOT_INSTALLED",
                      f"not found: {checked[0]}",
                      "Not installed — nothing to disable.",
                      checked)
    data, err = load_json_file(path)
    if err:
        return result("OpenCode", "UNKNOWN",
                      f"{checked[0]}: {err}",
                      "Fix the file, then re-run this audit.",
                      checked)
    flags = other_flags(data)
    if endpoint:
        return result("OpenCode", "ON",
                      "environment: OTEL_EXPORTER_OTLP_ENDPOINT="
                      f"{endpoint} (OpenCode exports OTel data "
                      "only when an endpoint is set)",
                      "Unset OTEL_EXPORTER_OTLP_ENDPOINT.",
                      checked, flags)
    return result(
        "OpenCode", "OFF",
        f"{checked[0]}: no OTel endpoint configured "
        "(OpenCode exports no telemetry by default)",
        "Nothing to disable. Note: /share still syncs conversations "
        "to OpenCode servers on demand — set \"share\": \"disabled\" "
        "in opencode.json to turn that off too.",
        checked, flags)


def check_do_not_track(home: Path, env: dict):
    value = env.get("DO_NOT_TRACK")
    if value == "1":
        return result("DO_NOT_TRACK (env)", "OFF",
                      "environment: DO_NOT_TRACK=1",
                      "Already set. Many CLI tools honor it.",
                      ["environment"])
    if value is not None:
        return result("DO_NOT_TRACK (env)", "ON",
                      f"environment: DO_NOT_TRACK={value} "
                      "(only \"1\" opts out)",
                      "export DO_NOT_TRACK=1 in your shell profile.",
                      ["environment"])
    return result("DO_NOT_TRACK (env)", "UNKNOWN",
                  "environment: DO_NOT_TRACK not set",
                  "export DO_NOT_TRACK=1 in your shell profile — "
                  "Claude Code, Codex and other CLI tools honor it.",
                  ["environment"])


CHECKERS = (check_claude_code, check_cursor, check_vscode, check_codex,
            check_gemini, check_opencode, check_do_not_track)

# ---------------------------------------------------------------- audit

def run_audit(home: Path, env: dict) -> dict:
    tools = [checker(home, env) for checker in CHECKERS]
    summary = {status: 0 for status in
               ("ON", "OFF", "UNKNOWN", "NOT_INSTALLED")}
    for tool in tools:
        summary[tool["status"]] += 1
    return {"home": str(home), "tools": tools, "summary": summary}


def render_table(audit: dict, home: Path) -> str:
    rows = [["Tool", "Telemetry", "Evidence", "How to disable"]]
    for tool in audit["tools"]:
        disable = tool["disable"].replace("\n", " / ")
        rows.append([tool["tool"], tool["status"], tool["evidence"], disable])
    widths = [max(len(row[i]) for row in rows) for i in range(4)]
    lines = []
    for i, row in enumerate(rows):
        lines.append(" | ".join(cell.ljust(widths[j])
                                for j, cell in enumerate(row)))
        if i == 0:
            lines.append("-+-".join("-" * w for w in widths))
    lines.append("")
    for tool in audit["tools"]:
        if tool["other_flags"]:
            lines.append(f"Other flags in {tool['tool']}: "
                         + "; ".join(tool["other_flags"]))
    if any(t["other_flags"] for t in audit["tools"]):
        lines.append("")
    s = audit["summary"]
    lines.append(f"Summary: {s['ON']} ON · {s['OFF']} OFF · "
                 f"{s['UNKNOWN']} UNKNOWN · {s['NOT_INSTALLED']} NOT_INSTALLED")
    lines.append("This tool reads config files only. It never edits them, "
                 "and it cannot check the Windows 11 OS-level telemetry "
                 "(see README).")
    return "\n".join(lines) + "\n"


def render_fix(audit: dict) -> str:
    lines = ["# How to disable what this audit found ON",
             "",
             "These are instructions only — nothing was changed. "
             "Apply them yourself.",
             ""]
    on_tools = [t for t in audit["tools"] if t["status"] == "ON"]
    if on_tools:
        for tool in on_tools:
            lines.append(f"## {tool['tool']} — ON")
            lines.append(tool["disable"])
            lines.append("")
    else:
        lines.append("Nothing found ON. 🎉")
        lines.append("")
    unknown = [t for t in audit["tools"] if t["status"] == "UNKNOWN"]
    if unknown:
        lines.append("## Not explicitly disabled (UNKNOWN)")
        lines.append("These tools have no explicit opt-out in their config, "
                     "so they may still be phoning home. Apply the same "
                     "setting to be sure:")
        lines.append("")
        for tool in unknown:
            lines.append(f"### {tool['tool']}")
            lines.append(tool["disable"])
            lines.append("")
    return "\n".join(lines)


class UsageErrorParser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        raise SystemExit(1)


def build_parser() -> argparse.ArgumentParser:
    parser = UsageErrorParser(
        description="Audit your own machine for coding-agent telemetry.")
    parser.add_argument("--home", default=None,
                        help="home directory to audit (default: $HOME)")
    parser.add_argument("--demo", action="store_true",
                        help="audit the bundled examples/demo-home instead "
                             "of your real $HOME (reproducible output)")
    parser.add_argument("--fix", action="store_true",
                        help="print how to disable everything found ON "
                             "(prints only, never edits)")
    parser.add_argument("--json", action="store_true",
                        help="machine-readable JSON output")
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {__version__}")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    env = dict(os.environ)
    if args.demo:
        home = Path(__file__).resolve().parent / "examples" / "demo-home"
        # Fixed environment so demo output is byte-identical anywhere.
        for var in ("DO_NOT_TRACK", "DISABLE_TELEMETRY",
                    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
                    "DISABLE_ERROR_REPORTING",
                    "GEMINI_TELEMETRY_ENABLED",
                    "OTEL_EXPORTER_OTLP_ENDPOINT"):
            env.pop(var, None)
        home_label = "~"
    elif args.home:
        home = Path(args.home).expanduser()
        home_label = str(home)
    else:
        home = Path.home()
        home_label = str(home)
    audit = run_audit(home, env)
    audit["home"] = home_label
    if args.json:
        print(json.dumps(audit, indent=2, sort_keys=True))
    elif args.fix:
        print(render_fix(audit), end="")
    else:
        print(f"agent-telemetry-audit {__version__} — auditing {home_label}")
        print(render_table(audit, home), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
