#!/usr/bin/env python3
"""Compare task ordering with the optional selector on fixed, public synthetic cases.

This measures selection only, not code-generation quality or overall delivery time.
It intentionally never launches an agent or sends workspace files to the endpoint.
"""
import argparse
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from studio.missions import parse_plan
from studio.server import Studio, atomic_json

CASES = [
    ("Restore login for all users; decorative changes can wait.",
     [("build", "Change decorative footer color"), ("build", "Fix login returning HTTP 500 for every user")], 1),
    ("Restore broken checkout before adding visual enhancements.",
     [("build", "Fix checkout refusing every valid payment"), ("build", "Add optional animated product cards")], 0),
    ("Validate the implemented core before starting optional new features.",
     [("build", "Add optional dashboard chart"), ("review", "Review completed core API against acceptance tests")], 1),
    ("Review completed release documentation now; optional export can wait.",
     [("review", "Review completed release documentation"), ("build", "Add optional CSV export")], 0),
    ("Unblock the release by resolving a failing regression test, then improve docs.",
     [("build", "Improve optional documentation examples"), ("build", "Fix a reproducible regression blocking release")], 1),
    ("Fix critical data-loss bug before proceeding with optional tutorial content.",
     [("build", "Fix reproducible data loss on save"), ("build", "Write optional tutorial")], 0),
]


def run(config, profiles, output, repeats):
    result = {"scope": "Synthetic task ordering, not end-to-end product delivery",
              "case_design": "Six explicit-priority cases, three place expected choice first, three second.",
              "started": time.time(), "samples": [], "profiles": profiles}
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="studio-decisions-") as directory:
        root = Path(directory)
        project = root / "project"
        project.mkdir()
        studio = Studio(project, root / "state", config)
        try:
            project_id = next(iter(studio.projects))
            available, default = studio.profiles()
            for repeat in range(repeats):
                for index, (goal, choices, expected) in enumerate(CASES):
                    for profile in [None, *profiles]:
                        m = studio.missions.prepare({"project": project_id, "title": "Synthetic ordering case",
                            "goal": goal, "criteria": [goal], "profile": default, "review_profile": default,
                            "decision_profile": profile, "isolated": False})
                        tasks = parse_plan({"tasks": [{"id": f"task{i}", "title": title, "instructions": title,
                            "criteria": [title], "depends_on": []} for i, (_, title) in enumerate(choices)]})
                        for task, (phase, _) in zip(tasks, choices):
                            task["status"] = "review" if phase == "review" else "pending"
                        m.update(status="running", phase="build", tasks=tasks)
                        studio.missions.save(m)
                        started = time.monotonic()
                        selection = studio.missions.next_work(m)
                        while selection is None and time.monotonic() - started < 15:
                            time.sleep(0.02)
                            selection = studio.missions.next_work(m)
                        record = studio.missions.decisions.get(m["decision_request"]) if m.get("decision_request") else {}
                        row = {"repeat": repeat, "case": index, "backend": profile or "rules", "choice": selection,
                               "expected": [choices[expected][0], f"task{expected}"],
                               "correct": selection == (choices[expected][0], f"task{expected}"),
                               "seconds": time.monotonic() - started, "status": record.get("status", "rules"),
                               "reason": record.get("reason"), "usage": record.get("usage"),
                               "calls": int(bool(record)), "policy_sha256": studio.missions.decisions.policy["sha256"]}
                        result["samples"].append(row)
                        atomic_json(output, result)
                        print(json.dumps({k: row[k] for k in ("case", "backend", "correct", "seconds", "status")}), flush=True)
        finally:
            studio.missions.close()
            studio.oauth.close()
    result["ended"] = time.time()
    result["summary"] = {}
    for backend in ["rules", *profiles]:
        rows = [r for r in result["samples"] if r["backend"] == backend]
        result["summary"][backend] = {"correct": sum(r["correct"] for r in rows), "cases": len(rows),
            "fallbacks": sum(r["status"] == "fallback" for r in rows), "calls": sum(r["calls"] for r in rows),
            "mean_seconds": statistics.mean(r["seconds"] for r in rows),
            "provider_token_reports": [r["usage"] for r in rows if r["usage"] is not None]}
    atomic_json(output, result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--profile", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=1)
    args = parser.parse_args()
    run(args.config.resolve(), args.profile, args.output.resolve(), args.repeats)
