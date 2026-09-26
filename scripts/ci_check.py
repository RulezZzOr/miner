"""Run shipped gates and produce a commit-bound machine-readable receipt."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from studio.test_evidence import test_count
from scripts.build_release import build
from scripts.security_scan import scan_files, scan_language, tracked_files


def verify_archive(path):
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            content = {name.split("/", 1)[1]: archive.read(name) for name in archive.namelist()}
    else:
        with tarfile.open(path) as archive:
            content = {item.name.split("/", 1)[1]: archive.extractfile(item).read() for item in archive.getmembers() if item.isfile()}
    manifest = json.loads(content["release-manifest.json"])
    if set(content) != set(manifest["files"]) | {"release-manifest.json"}:
        raise ValueError("Release contains unlisted or missing files.")
    for name, digest in manifest["files"].items():
        if hashlib.sha256(content[name]).hexdigest() != digest:
            raise ValueError("Release hash mismatch: " + name)
    return {"path": path.name, "files": len(manifest["files"]), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def main():
    output = ROOT / "dist/ci-evidence"
    output.mkdir(parents=True, exist_ok=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT))
    receipt = {"commit": commit, "dirty": dirty, "ci_commit": os.getenv("GITHUB_SHA"), "started": time.time(), "checks": [], "status": "failed"}
    try:
        for name, argv in [("backend", [sys.executable, "-m", "unittest", "discover", "-s", "studio/tests", "-q"]),
                           ("frontend", ["node", "--test", "--test-reporter=tap", *[str(p.relative_to(ROOT)) for p in sorted((ROOT / "studio/tests").glob("*.cjs"))]])]:
            result = subprocess.run(argv, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=600)
            (output / (name + ".log")).write_text(result.stdout)
            count = test_count(result.stdout)
            receipt["checks"].append({"name": name, "exit_code": result.returncode, "executed_tests": count})
            print(f"{name}: exit {result.returncode}, executed tests {count}", flush=True)
            if result.returncode or not count:
                raise ValueError(name + " failed or did not execute tests. See its log.")
        tracked = tracked_files(ROOT)
        findings = scan_files(ROOT, tracked)
        receipt["credential_findings"] = findings
        if findings:
            raise ValueError("Publication credential scan found sensitive paths or signatures.")
        language = scan_language(ROOT, tracked)
        receipt["language_findings"] = language
        if language:
            raise ValueError("English-only scan found non-English text; see language_findings.")
        receipt["releases"] = [verify_archive(p) for p in build(ROOT, output / "releases")]
        final_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        final_dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT))
        if final_commit != commit:
            raise ValueError("Git revision changed during the checks.")
        if os.getenv("GITHUB_ACTIONS") and (dirty or final_dirty or commit != os.getenv("GITHUB_SHA")):
            raise ValueError("CI evidence is not bound to a clean expected commit.")
        receipt["status"] = "passed"
    except Exception as exc:
        receipt["error"] = str(exc)
        print(str(exc), file=sys.stderr)
    finally:
        receipt["ended"] = time.time()
        (output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt["status"] != "passed"


if __name__ == "__main__":
    sys.exit(main())
