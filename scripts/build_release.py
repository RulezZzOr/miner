"""Build source distributions from an allowlist, never archive the workspace wholesale."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TREES = (
    "studio", "scripts", "frontier/apodex", "frontier/frontier_agent", "frontier/plugins",
    "frontier/workflows", "frontier/benchmarks", "frontier/assets", "frontier/docs",
)
LAUNCHERS = ("setup-studio", "switch-studio", "switch-studio-service", "switch")
FILES = (
    *LAUNCHERS, "agent.example.toml", "INSTALL.md", "THIRD_PARTY.md", "LICENSE", "NOTICE",
    "SECURITY.md", "docs/AUDIT-2026-09-24.md",
    "frontier/pyproject.toml", "frontier/uv.lock", "frontier/LICENSE", "frontier/README.md",
    "frontier/SWITCH.md", "frontier/config/providers.yaml",
)
SUFFIXES = {
    ".py", ".md", ".toml", ".yaml", ".yml", ".json", ".html", ".css", ".js",
    ".cjs", ".sh", ".svg", ".png", ".jpeg", ".jpg", ".webp", ".txt", ".j2", ".typed",
}
BLOCKED = {"__pycache__", "node_modules", "datasets", "results", "tasks-generated", "dist"}


def source_files(root: Path) -> list[Path]:
    selected = {root / name for name in FILES}
    for tree in TREES:
        for path in (root / tree).rglob("*"):
            parts = path.relative_to(root).parts
            if any(p.startswith(".") or p in BLOCKED for p in parts):
                continue
            if path.suffix in SUFFIXES or path.name in {"VERSION", "LICENSE", "NOTICE", "COPYING"}:
                selected.add(path)
    result = []
    for path in sorted(selected):
        relative = path.relative_to(root)
        if any(root.joinpath(*relative.parts[:i]).is_symlink()
               for i in range(1, len(relative.parts) + 1)):
            raise ValueError(f"Refusing symlink in release sources: {relative}")
        if not path.is_file():
            raise ValueError(f"Missing release source: {relative}")
        result.append(path)
    return result


def build(root: Path, output: Path) -> list[Path]:
    version = (root / "studio/VERSION").read_text().strip()
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.]+)?", version):
        raise ValueError("Invalid release version")
    sources = source_files(root)
    output.mkdir(parents=True, exist_ok=True)
    base = {str(p.relative_to(root)): p.read_bytes() for p in sources}
    base["README.md"] = (
        f"# Switch Studio {version}\n\n"
        "Local web IDE. See [INSTALL.md](INSTALL.md) for setup and platform limits.\n\n"
        "This source distribution installs Python and locked dependencies with uv. "
        "It contains no model weights, account credentials or user history. "
        "Windows runs through WSL2. This is an alpha, not a signed native installer.\n"
    ).encode()
    built = []
    for target, launchers in {
        "macos": ("Switch Studio.command", "Setup Studio.command"),
        "linux": (),
        "windows-wsl2": ("Switch Studio Windows.cmd",),
    }.items():
        payload = dict(base)
        for launcher in launchers:
            raw = (root / launcher).read_bytes()
            payload[launcher] = raw.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n") if launcher.endswith(".cmd") else raw
        manifest = {
            "version": version, "platform": target, "distribution": "source",
            "files": {name: hashlib.sha256(data).hexdigest() for name, data in sorted(payload.items())},
        }
        payload["release-manifest.json"] = (json.dumps(manifest, indent=2) + "\n").encode()
        prefix = f"switch-studio-{version}"
        archive = output / f"{prefix}-{target}{'.tar.gz' if target == 'linux' else '.zip'}"
        if target == "linux":
            with tarfile.open(archive, "w:gz") as dest:
                for name, raw in sorted(payload.items()):
                    info = tarfile.TarInfo(f"{prefix}/{name}")
                    info.size = len(raw)
                    info.mode = mode(name)
                    dest.addfile(info, io.BytesIO(raw))
        else:
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as dest:
                for name, raw in sorted(payload.items()):
                    info = zipfile.ZipInfo(f"{prefix}/{name}")
                    info.create_system = 3
                    info.external_attr = (0o100000 | mode(name)) << 16
                    info.compress_type = zipfile.ZIP_DEFLATED
                    dest.writestr(info, raw)
        built.append(archive)
    (output / "SHA256SUMS").write_text("".join(
        f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in built
    ))
    return built


def mode(name: str) -> int:
    return 0o755 if name in LAUNCHERS or name.endswith((".sh", ".command")) else 0o644


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    for archive in build(ROOT, args.output):
        print(f"{archive} ({archive.stat().st_size:,} bytes)")
