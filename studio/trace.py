"""Append-only operational decisions committed with mission state transitions."""
import hashlib
import json
import uuid


def record_transition(db, previous, current, at):
    before = json.loads(previous) if previous else {}
    fields = ("status", "phase", "active_attempt", "tasks", "questions", "attempts", "message",
              "verification_id", "verification_result", "verification_checks", "acceptance", "version_id", "decision",
              "profile", "review_profile", "attempt_minutes", "max_turns", "max_attempts")
    changed = [key for key in fields if before.get(key) != current.get(key)]
    if not changed:
        return
    attempt = current["attempts"][-1] if current["attempts"] else {}
    criteria = json.dumps(current["criteria"], ensure_ascii=False, sort_keys=True)
    event = {"id": uuid.uuid4().hex[:16], "mission": current["id"], "at": at,
             "kind": "created" if not before else "transition", "changed_fields": changed,
             "from": before.get("status"), "to": current["status"], "reason": current["message"],
             "next_action": current["status"],
             "inputs": {"criteria_sha256": hashlib.sha256(criteria.encode()).hexdigest(),
                        "base_version": current.get("base_version"), "report_sha256": attempt.get("report_sha256"),
                        "verification": current.get("verification_id") or (current.get("verification_result") or {}).get("id")},
             "attempt": attempt.get("id"), "phase": attempt.get("phase"),
             "tasks": [{"id": t["id"], "status": t["status"]} for t in current["tasks"]],
             "file_changes": attempt.get("file_changes", []), "usage": attempt.get("usage"),
             "elapsed_seconds": attempt.get("elapsed_seconds"), "version": current.get("version_id")}
    event["decision"] = current.get("decision")
    event["runtime"] = {k: current.get(k) for k in ("profile", "review_profile", "attempt_minutes", "max_turns", "max_attempts")}
    db.execute("INSERT INTO mission_trace VALUES (?, ?, ?, ?)",
               (event["id"], current["id"], at, json.dumps(event, ensure_ascii=False)))
