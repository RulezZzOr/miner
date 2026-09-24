"""Fail publication on private runtime paths or common credential signatures.

This bounded scanner is a publication guard, not a claim that every secret or
vulnerability can be recognized. It prints locations and categories, never values.
"""
from pathlib import Path
import re
import subprocess
import sys

PATTERNS = {
    "private key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "GitHub token": re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{50,})\b"),
    "OpenAI-style key": re.compile(rb"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{40,}\b"),
    "AWS access key": re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
}


def scan_files(root, paths):
    findings = []
    for path in paths:
        parts = tuple(p.casefold() for p in Path(path).parts)
        name = Path(path).name.casefold()
        if (root / path).is_symlink():
            findings.append({"path": path, "kind": "tracked symlink"})
            continue
        if name in {"agent.toml", "ssh-targets.json", "id_rsa", "id_ed25519"} or any(p in {".switch-agent", ".apodex"} for p in parts) or (name.startswith(".env") and "example" not in name):
            findings.append({"path": path, "kind": "private runtime path"})
        raw = (root / path).read_bytes()
        for kind, pattern in PATTERNS.items():
            for match in pattern.finditer(raw):
                findings.append({"path": path, "line": raw[:match.start()].count(b"\n") + 1, "kind": kind})
    return findings


def main():
    root = Path(__file__).resolve().parents[1]
    paths = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode().split("\0")
    findings = scan_files(root, [p for p in paths if p and (root / p).is_file()])
    for item in findings:
        print(f"{item['path']}:{item.get('line', 1)}: {item['kind']}")
    print(f"Publication credential scan: {len(findings)} finding(s).")
    return bool(findings)


if __name__ == "__main__":
    sys.exit(main())
