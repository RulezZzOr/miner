"""Recognize test-runner summaries without treating an exit-zero no-op as a test."""
import re
import subprocess
from pathlib import Path


def git_revision(root):
    try:
        top = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=3, check=True).stdout.strip()
        if Path(top).resolve() != Path(root).resolve():
            return None
        return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True,
                              text=True, timeout=3, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def check_kind(argv):
    words = [Path(a).name.lower() for a in argv]
    return "test" if any(w in {"pytest", "unittest", "--test"} for w in words) or (
        words[0] in {"npm", "pnpm", "yarn", "bun"} and any(w in {"test", "test:unit", "test:ci"} for w in words[1:])) else "command"


def test_count(log):
    clean = re.sub(r"\x1b\[[0-9;]*m", "", log)
    # unittest, then node --test: TAP ('# tests 2') or the spec reporter of Node >= 23 ('ℹ tests 2').
    for pattern, skipped_pattern in ((r"(?m)^Ran (\d+) tests? in ", r"\bskipped=(\d+)"),
                                     (r"(?m)^(?:#|ℹ) tests (\d+)\s*$", r"(?m)^(?:#|ℹ) skipped (\d+)\s*$")):
        matches = re.findall(pattern, clean)
        if matches:
            return max(0, sum(map(int, matches)) - sum(map(int, re.findall(skipped_pattern, clean))))
    # Jest ('Tests: 1 failed, 2 skipped, 1 passed, 4 total') and Vitest ('Tests  1 passed | 5 skipped (6)'):
    # only passed and failed tests were executed.
    summaries = re.findall(r"(?m)^\s*Tests:?[ \t]+([^\n]*(?:\d+ total|\(\d+\))[^\n]*)$", clean)
    if summaries:
        return sum(int(n) for line in summaries for n in re.findall(r"(\d+) (?:passed|failed)", line))
    # Skipped/deselected tests are not executed tests.
    lines = [line for line in clean.splitlines() if re.search(r"\d+ (?:passed|failed|skipped|deselected|error).* in [\d.]+s", line)]
    if lines:
        return sum(int(n) for n in re.findall(r"(\d+) (?:passed|failed|error)", lines[-1]))
    if re.search(r"no tests (?:ran|found)|collected 0 items", clean, re.I):
        return 0
    return None
