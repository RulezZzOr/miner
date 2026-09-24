"""Small controller-owned delivery policies; SQLite remains the workflow authority.

These checks qualify a brief, not the truth of external services. Semantic
readiness is assessed during bounded planning, before any execution worker.
"""
from __future__ import annotations

import hashlib
import json
import re


BRIEF_FIELDS = ("goal", "criteria", "constraints", "sources")
MODES = ("auto", "light", "standard", "sensitive")
SENSITIVE = re.compile(r"\b(auth\w*|oauth|password\w*|credential\w*|secret\w*|security|permission\w*|"
                       r"payment\w*|billing|migration\w*|database|schema|production|deploy\w*|"
                       r"firewall|identity|encryption|hesl\w*|oprávnění|platb\w*|databáz\w*)\b", re.I)
SENSITIVE_PATH = re.compile(r"(^|/)(auth[^/]*|security|migrations?|permissions?|billing|payments?|"
                            r"deploy[^/]*|infra|\.github)(/|\.|$)|(^|/)(schema|credentials|secrets?)[./]", re.I)


def brief_hash(m):
    data = {key: m.get(key, "") for key in BRIEF_FIELDS}
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def process_policy(m, paths=()):
    requested = m.get("process_mode", "auto")
    if requested not in MODES:
        raise ValueError("Process must be auto, light, standard or sensitive.")
    brief = " ".join([m.get("goal", ""), *m.get("criteria", [])])
    risk = SENSITIVE.search(brief) or next((p for p in paths if SENSITIVE_PATH.search(p)), None)
    previous = m.get("process", {}).get("effective")
    if risk or previous == "sensitive" or requested == "sensitive":
        mode, reason = "sensitive", "Sensitive scope or changed infrastructure/security files; lightweight override is not allowed."
    elif requested == "light" or (requested == "auto" and re.search(r"\b(documentation|readme|typo|spelling|copy edit)\b", brief, re.I)
                                  and len(brief) < 600 and len(m.get("criteria", [])) <= 3):
        mode, reason = "light", "Small task: shorter work and context budgets, with the same separate review and evidence gates."
    else:
        mode, reason = "standard", "Normal plan, implementation, separate review and independent checks."
    return {"requested": requested, "effective": mode, "reason": reason,
            "review_required": True, "max_build_seconds": 600 if mode == "light" else None,
            "max_build_turns": 16 if mode == "light" else None,
            "handoff_bytes": 4500 if mode == "light" else 8000,
            "project_notes_bytes": 4000 if mode == "light" else 12000}


def readiness(m):
    """Conservative deterministic contradictions; network unknowns are not blockers."""
    goal = m.get("goal", "")
    constraints = m.get("constraints", "")
    criteria = " ".join(m.get("criteria", []))
    read_only = re.search(r"\bread[- ]only\b|\bonly (?:audit|inspect)\b|pouze (?:audit|čtení)|bez změn", goal + " " + constraints, re.I)
    must_operate = re.search(r"(?:all|every|všechny|veškeré).{0,70}(?:integrations?|services?|integrace|služby).{0,70}(?:working|functional|operational|fungovat|funkční)|(?:integrations?|services?).{0,30}must (?:work|be (?:working|functional|operational))", criteria, re.I)
    questions = []
    if read_only and must_operate:
        questions.append({"question": "Should this be a read-only audit that may document unavailable services, or an implementation task that makes the integrations work?",
                          "reason": "The read-only scope conflicts with a required operational outcome. Revise the brief so the worker has one achievable scope."})
    policy = process_policy(m)
    complex_brief = policy["effective"] == "sensitive" or len(goal + constraints + criteria) > 900 or len(m.get("criteria", [])) > 4
    return {"status": "clarify" if questions else "ready", "brief_hash": brief_hash(m),
            "checks": [{"name": "Required brief fields", "passed": bool(goal and m.get("criteria"))},
                       {"name": "Scope and outcome agree", "passed": not questions}],
            "questions": questions, "semantic_required": complex_brief,
            "semantic_status": "pending" if complex_brief else "not_required",
            "limitations": "Local configuration and brief checks only. External access is verified during execution."}


def clip_bytes(value, limit):
    return value.encode("utf-8")[:limit].decode("utf-8", errors="ignore")


def handoff(m, manifest, verification=None):
    """Bounded structured routing data, never a replacement for source evidence."""
    references = {}
    for task in m.get("tasks", []):
        for artifact in task.get("artifacts", []):
            references[artifact["path"]] = artifact.get("sha256")
    for attempt in m.get("attempts", []):
        for change in attempt.get("file_changes", []):
            if change["path"] in references:
                references[change["path"]] = change.get("after")
    refs = [{"path": path, "recorded_sha256": sha, "current_sha256": manifest.get(path),
             "state": "unchanged" if manifest.get(path) == sha else "changed_read_again"}
            for path, sha in sorted(references.items())]
    checks = (verification or {}).get("checks", [])
    verified = next((c for c in reversed(checks) if c.get("exit_code") == 0 and not c.get("error")), None)
    packet = {"mission": m["id"], "brief_hash": brief_hash(m), "goal": clip_bytes(m["goal"], 1400),
              "goal_excerpt_truncated": len(m["goal"].encode()) > 1400,
              "done": [{"id": t["id"], "title": clip_bytes(t["title"], 160)} for t in m.get("tasks", []) if t["status"] == "done"],
              "next": [{"id": t["id"], "status": t["status"]} for t in m.get("tasks", []) if t["status"] != "done"],
              "blockers": [clip_bytes(q["question"], 350) for q in m.get("questions", []) if q["answer"] is None],
              "last_verified_command": {"argv": verified["argv"], "record": verification["id"]} if verified else None,
              "references": refs, "omitted_references": 0,
              "instruction": "Continue the selected pending task. Do not repeat completed actions. Changed or missing references must be read again; unchanged hashes are routing hints, not proof of correctness."}
    limit = m.get("process", {}).get("handoff_bytes", 8000)
    size = lambda: len(json.dumps(packet, ensure_ascii=False).encode())
    while packet["references"] and size() > limit:
        packet["references"].pop()
        packet["omitted_references"] += 1
    # Large plans/questions are summarized by IDs; full task/criteria remain in the current phase prompt.
    for key in ("done", "blockers", "next"):
        while packet[key] and size() > limit:
            packet[key].pop()
            packet["omitted_" + key] = packet.get("omitted_" + key, 0) + 1
    if size() > limit:
        packet["last_verified_command"] = None
        packet["last_verified_command_omitted"] = True
    return packet
