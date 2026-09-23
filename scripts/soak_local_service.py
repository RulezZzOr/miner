"""Bounded local deployment endurance test; fixture code, never a model-quality claim."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from studio.server import Studio, atomic_json, write_config

SERVICE = '''import argparse, os
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
p=argparse.ArgumentParser();p.add_argument('--host');p.add_argument('--port',type=int);a=p.parse_args()
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args): pass
 def do_GET(self):
  failed = BUGGY and (Path(os.environ['SWITCH_DATA_DIR'])/'incident').exists()
  self.send_response(503 if failed else 200);self.end_headers();self.wfile.write(b'SOAK VERSION')
HTTPServer((a.host,a.port),Handler).serve_forever()
'''


def run(root, seconds, interval):
    if root.exists():
        raise ValueError("Use a fresh evidence directory; existing runs are never overwritten.")
    root.mkdir(parents=True)
    project = root / "project"
    project.mkdir()
    config = root / "agent.toml"
    write_config(config, {"fixture": {"model": "not-used", "auth": "none", "protocol": "chat_completions",
                                    "base_url": "http://127.0.0.1:1/v1"}}, "fixture")
    studio = Studio(project, root / "state", config)
    report = {"scope": "Real service processes and HTTP, fixture source and repair; no LLM used",
              "status": "running", "started": time.time(), "duration_requested": seconds,
              "cycles": [], "samples": 0, "incidents": 0, "restarts": 0}
    process = psutil.Process()

    def record():
        report.update(updated=time.time(), seconds_observed=time.time()-report["started"],
                      rss=process.memory_info().rss, fds=process.num_fds())
        report["peak_rss"] = max(report.get("peak_rss", 0), report["rss"])
        report["peak_fds"] = max(report.get("peak_fds", 0), report["fds"])
        atomic_json(root / "result.json", report)

    def shutdown():
        studio.deployments.close()
        studio.missions.close()
        studio.oauth.close()

    def until(key, status):
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            studio.deployments.tick()
            deployment = studio.deployments.get(key)
            report["samples"] += 1
            record()
            if deployment["status"] == status:
                return deployment
            if deployment["status"] in {"unhealthy", "failed"} and status != "unhealthy":
                raise RuntimeError(str(deployment))
            time.sleep(0.5)
        raise TimeoutError(f"{key} did not become {status}")

    try:
        pid = next(iter(studio.projects))
        product = studio.products.create({"project": pid, "title": "Service endurance fixture",
            "goal": "Exercise controlled HTTP process failures, versions and restart recovery.",
            "criteria": ["Fixture server is readable"], "profile": "fixture", "review_profile": "fixture"})
        product = studio.products.action({"id": product["id"], "action": "deployment_settings",
            "argv": [sys.executable, "service.py", "--host", "{host}", "--port", "{port}"],
            "expected_text": "SOAK", "auto_repair": True, "restart_on_start": True})
        for number in (1, 2):
            (project / "service.py").write_text(SERVICE.replace("BUGGY", str(number == 1)).replace("VERSION", str(number)))
            mission = studio.missions.create({"project": pid, "title": f"Fixture verification {number}",
                "goal": "Validate fixture syntax without a model", "criteria": ["Valid Python"], "isolated": False,
                "verification_checks": [{"argv": [sys.executable, "-c",
                    "import ast; from pathlib import Path; ast.parse(Path('service.py').read_text())"]}]})
            check_id = studio.missions.verifications.start(mission)
            deadline = time.monotonic() + 10
            while studio.missions.verifications.get(check_id)["status"] == "running":
                if time.monotonic() > deadline:
                    raise TimeoutError("Fixture verification timed out")
                time.sleep(0.05)
            evidence = studio.missions.verifications.get(check_id)
            if evidence["status"] != "passed":
                raise RuntimeError(str(evidence))
            version = studio.missions.versions.snapshot(project, label=f"Fixture {number}",
                expected=evidence["sources"], expected_modes=evidence["source_modes"])
            # Explicit fixture metadata, not a fabricated model report or UI delivery.
            product["releases"].append({"number": number, "mission": mission["id"], "version_id": version["id"],
                "verification_id": check_id, "summary": "Synthetic endurance fixture", "artifacts": [], "accepted": time.time()})
        studio.products.save(product)
        current = studio.deployments.start(product, product["releases"][0])
        until(current["id"], "healthy")
        next_cycle = time.monotonic() + interval
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            studio.deployments.tick()
            if studio.deployments.get(current["id"])["status"] != "healthy":
                raise RuntimeError("Unexpected health loss between controlled incidents")
            report["samples"] += 1
            record()
            if time.monotonic() >= next_cycle:
                marker = studio.data / "deployment-data" / product["id"] / "incident"
                marker.write_text("controlled failure")
                failed = until(current["id"], "unhealthy")
                if not failed.get("incident_item"):
                    raise RuntimeError("Incident was not queued")
                report["incidents"] += 1
                fixed = studio.deployments.start(product, product["releases"][1])
                until(fixed["id"], "healthy")
                marker.unlink()
                current = studio.deployments.start(product, product["releases"][0])
                until(current["id"], "healthy")
                if studio.deployments.get(fixed["id"])["status"] != "stopped":
                    raise RuntimeError("Rollback retained superseded process")
                shutdown()
                studio = Studio(project, root / "state", config)
                studio.deployments.tick()
                current = studio.deployments.list(product["id"])[0]
                until(current["id"], "healthy")
                report["restarts"] += 1
                report["cycles"].append({"at": time.time(), "incident": failed["incident"], "deployment": current["id"]})
                print(json.dumps({"cycle": len(report["cycles"]), "seconds": time.time()-report["started"]}), flush=True)
                next_cycle = time.monotonic() + interval
            time.sleep(1)
        report["status"] = "passed"
    except BaseException as exc:
        report.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        shutdown()
        report["ended"] = time.time()
        record()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--seconds", type=int, default=3600)
    parser.add_argument("--interval", type=int, default=600)
    args = parser.parse_args()
    if not 20 <= args.seconds <= 86400 or not 5 <= args.interval <= args.seconds:
        parser.error("seconds must be 20..86400, interval 5..seconds")
    run(args.root.resolve(), args.seconds, args.interval)
