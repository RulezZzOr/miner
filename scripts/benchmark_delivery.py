"""Bounded real-model delivery comparison with the same owner-supplied plan.

Both variants use real author/reviewer processes, controller checks and promotion.
The fixed plan is a benchmark input, never represented as model-generated work.
One pair is a pilot, not a statistically reliable model ranking.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from studio.missions import parse_plan
from studio.server import Studio, atomic_json

TOTALS = """Implement totals.py with total_cents(items). Each item is a dict with price_cents and quantity.
Both values must be nonnegative ints; reject bool, negative values and non-int values with ValueError.
Return the exact integer sum of price_cents * quantity. Empty list returns 0. Do not use floats.
"""
FORMAT = """Implement formatting.py with format_cents(value). Accept an int, including negative values;
reject bool and non-int values with ValueError. Return an exact string with a decimal point and two
fraction digits, no currency or grouping. Examples: 0 -> '0.00', 1 -> '0.01', -1 -> '-0.01',
105 -> '1.05', -12345 -> '-123.45'. Arbitrarily large integers must remain exact; no floats.
"""
GOAL = "Create two independent standard-library Python modules for exact money formatting and cart totals. " + TOTALS + FORMAT
CRITERIA = ["Cart totals are exact integers and invalid values raise ValueError.",
            "Money formatting is exact, including negative and arbitrarily large integer cents."]
CHECK = '''from totals import total_cents
from formatting import format_cents
assert total_cents([])==0
assert total_cents([{"price_cents":125,"quantity":3},{"price_cents":7,"quantity":0}])==375
for value in [True,False,-1,1.5,"2",None]:
 for key in ["price_cents","quantity"]:
  item={"price_cents":10,"quantity":2};item[key]=value
  try: total_cents([item])
  except ValueError: pass
  else: raise AssertionError((key,value))
for value in [True,False,1.5,"2",None]:
 try: format_cents(value)
 except ValueError: pass
 else: raise AssertionError(value)
for value in [0,1,-1,99,-99,100,-100,105,-12345,10**40+1,-10**40-1,*range(-503,504,7)]:
 sign="-" if value<0 else "";n=abs(value)
 expected=f"{sign}{n//100}.{n%100:02d}"
 assert format_cents(value)==expected,(value,format_cents(value),expected)
for n in range(100):
 items=[{"price_cents":n*10**20+1,"quantity":n},{"price_cents":5,"quantity":3}]
 assert total_cents(items)==(n*10**20+1)*n+15
print("PASS: exact totals, invalid values, signed and large integer formatting")
'''


def run_case(root, config, profile, decision_profile, seconds, progress):
    root.mkdir()
    project = root / "project"
    project.mkdir()
    (project / "PROJECT.md").write_text("# Fixed public benchmark specification\n\n" + GOAL)
    studio = Studio(project, root / "state", config)
    started = time.time()
    mission = None
    try:
        mission = studio.missions.create({"project": next(iter(studio.projects)),
            "title": "Exact amounts delivery comparison", "goal": GOAL, "criteria": CRITERIA,
            "constraints": "Write only totals.py and formatting.py plus required controller reports. "
                "Do not edit PROJECT.md. No packages, internet, deployment, or work outside this project. "
                "The two tasks are independent. Prefer completing review before beginning another build.",
            "profile": profile, "review_profile": profile, "decision_profile": decision_profile,
            "days": 1, "max_attempts": 12, "attempt_minutes": 5, "max_turns": 12, "auto_approve": True,
            "verification_checks": [{"argv": [sys.executable, "-c", CHECK], "timeout": 30,
                                     "label": "Independent exact amounts acceptance"}]})
        mission.update(status="awaiting_plan", phase="plan", deadline=time.time()+seconds,
            tasks=parse_plan({"tasks": [
                {"id": "totals", "title": "Exact cart totals", "instructions": TOTALS,
                 "criteria": [CRITERIA[0]], "depends_on": []},
                {"id": "format", "title": "Exact signed amount formatting", "instructions": FORMAT,
                 "criteria": [CRITERIA[1]], "depends_on": []}]}),
            message="Fixed owner-supplied benchmark plan, not a model planning result.")
        studio.missions.save(mission)
        studio.missions.action({"id": mission["id"], "action": "approve_plan"})
        last = None
        while time.time() < started + seconds:
            studio.missions.tick()
            mission = studio.missions.get(mission["id"])
            signature = (mission["status"], mission["active_attempt"], len(mission["attempts"]))
            if signature != last:
                progress({"case": root.name, "mission": mission["id"], "status": mission["status"],
                          "attempt": mission["active_attempt"], "message": mission["message"],
                          "seconds": time.time()-started})
                last = signature
            if mission["status"] == "ready":
                mission = studio.missions.action({"id": mission["id"], "action": "accept"})
                break
            if mission["status"] in {"blocked", "waiting", "awaiting_checks", "expired", "cancelled"}:
                break
            time.sleep(1)
        if mission["status"] not in {"accepted", "expired", "cancelled"}:
            terminal_observation = {"status": mission["status"], "message": mission["message"]}
            studio.missions.action({"id": mission["id"], "action": "cancel"})
        else:
            terminal_observation = None
        for _ in range(30):
            if not studio.processes:
                break
            time.sleep(0.2)
        studio.missions.tick()
        mission = studio.missions.get(mission["id"])
        with studio.missions.connect() as db:
            decisions = [json.loads(r[0]) for r in db.execute("SELECT data FROM decisions")]
        check = studio.missions.verifications.get(mission["verification_id"]) if mission.get("verification_id") else None
        promoted = {}
        if mission["status"] == "accepted":
            for name, sha in check["sources"].items():
                actual = hashlib.sha256((project / name).read_bytes()).hexdigest()
                if actual != sha:
                    raise AssertionError("Promoted contents differ from verification: " + name)
                promoted[name] = actual
        return {"variant": root.name, "decision_profile": decision_profile, "status": mission["status"],
            "seconds": time.time()-started, "mission": mission, "verification": check,
            "decisions": decisions, "promoted": promoted, "terminal_observation": terminal_observation,
            "summary": {"accepted": mission["status"] == "accepted", "attempts": len(mission["attempts"]),
                "failed_attempts": sum(bool(a.get("error")) for a in mission["attempts"]),
                "order": [[a["phase"], a["task"]] for a in mission["attempts"]],
                "agent_usage": [a.get("usage") for a in mission["attempts"]],
                "decision_usage": [d.get("usage") for d in decisions],
                "decision_fallbacks": sum(d["status"] == "fallback" for d in decisions)}}
    finally:
        for key in list(studio.processes):
            studio.stop(key)
        studio.missions.close()
        studio.deployments.close()
        studio.oauth.close()


def run(args):
    root = args.root.resolve()
    if root.exists():
        raise ValueError("Use a fresh evidence directory; previous results are never overwritten.")
    root.mkdir(parents=True)
    result = {"status": "waiting" if args.wait_mission else "running", "started": time.time(),
        "scope": "One real-model delivery pair with identical fixed owner plan, author, reviewer and executable acceptance checks.",
        "limitations": "Small sequential pilot: no statistical significance, cache/order effects are uncontrolled. No model-generated planning is evaluated.",
        "profile": args.profile, "cases": [], "current": None}
    def progress(value):
        result["current"] = value
        result["updated"] = time.time()
        atomic_json(root / "result.json", result)
        print(json.dumps(value, ensure_ascii=False), flush=True)
    try:
        if args.wait_mission:
            if not args.access_key_file:
                raise ValueError("--wait-mission requires --access-key-file; Studio authenticates every API call.")
            key = args.access_key_file.read_text().strip()
            if not key:
                raise ValueError(f"The access key file is empty: {args.access_key_file}")
            request = urllib.request.Request(args.studio_url + "/api/missions",
                                             headers={"Authorization": "Bearer " + key})
            until = time.monotonic()+3600
            while time.monotonic() < until:
                try:
                    with urllib.request.urlopen(request, timeout=5) as response:
                        missions = json.load(response)["missions"]
                    mission = next(m for m in missions if m["id"] == args.wait_mission)
                    if mission["status"] == "accepted":
                        break
                    progress({"waiting_for": args.wait_mission, "status": mission["status"]})
                except urllib.error.HTTPError as exc:
                    if exc.code in (401, 403):
                        raise PermissionError(
                            f"Studio rejected the access key (HTTP {exc.code}); check --access-key-file."
                        ) from exc
                    progress({"waiting_for": args.wait_mission, "observation_error": str(exc)[:200]})
                except (OSError, ValueError, StopIteration) as exc:
                    progress({"waiting_for": args.wait_mission, "observation_error": str(exc)[:200]})
                time.sleep(20)
            else:
                raise TimeoutError("The existing delivery did not finish within the wait budget.")
        result["status"] = "running"
        for name, selector in [("rules", None), ("selector", args.profile)]:
            result["cases"].append(run_case(root/name, args.config.resolve(), args.profile, selector,
                                             args.case_seconds, progress))
            progress({"finished_case": name, "status": result["cases"][-1]["status"]})
        result["status"] = "complete"
    except Exception as exc:
        result.update(status="failed", error=str(exc))
        raise
    finally:
        result["ended"] = time.time()
        atomic_json(root / "result.json", result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--case-seconds", type=int, default=2700)
    parser.add_argument("--wait-mission")
    parser.add_argument("--studio-url", default="http://127.0.0.1:4317")
    parser.add_argument("--access-key-file", type=Path,
                        help="owner access key for --wait-mission (e.g. .switch-agent/studio/access-key)")
    run(parser.parse_args())
