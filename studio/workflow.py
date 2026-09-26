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
SENSITIVE = re.compile(r"\b(auth\w*|oauth|password\w*|passphrase\w*|credential\w*|secret\w*|security|permission\w*|"
                       r"payment\w*|billing|migration\w*|database\w*|schema|production|deploy\w*|"
                       r"firewall|identity|encryption)\b", re.I)
# Scope-wide read-only wording in the goal or constraints. A restriction on one object
# ("make no changes to PROJECT.md") or on one activity ("read-only remote discovery")
# is not a read-only scope; such wording is at most a hint for the planner's check.
READ_ONLY = re.compile(
    r"\bread[- ]only\s+(?:audit|review|inventory|inspection|assessment|analysis|survey|investigation|evaluation|study|"
    r"task|mission|mode|scope|engagement|exercise)s?\b|"
    r"\b(?:strictly|purely|entirely|completely)\s+read[- ]only\b|"
    r"\b(?:task|mission|work|scope|audit|review|engagement|everything)\s+(?:is|stays|remains|must\s+(?:be|stay|remain))\s+read[- ]only\b|"
    r"(?:^|[.;:!?]\s*)(?:scope:\s*)?read[- ]only\s*(?:[.;!?]|$)|"
    r"\bonly\s+(?:audit|inspect|observe)\b|"
    r"\b(?:audit|inspection|observation|review)[- ]only\b(?!\s+(?:the|a|an|this|these|those|of|for|on|in)\b)|"
    r"\b(?:make|making|apply|applying)\s+no\s+changes\b(?!\s+(?:to|on|in|at)\b)|"
    r"\bwithout\s+(?:making|applying)\s+(?:any\s+)?changes\b(?!\s+(?:to|on|in|at)\b)|"
    r"\b(?:do\s+not|don['\u2019]t|must\s+not|never)\s+(?:make\s+(?:any\s+)?changes\b(?!\s+(?:to|on|in|at)\b)|"
    r"(?:change|modify|alter|touch)\s+anything\b(?!\s+(?:else|outside|except|beyond|but|other)\b))|"
    r"\b(?:change|modify|alter)\s+nothing\b|\bno\s+changes\s+(?:to|on|in)\s+anything\b", re.I | re.M)
SCOPE_OBJECT = r"(?:the\s+|any\s+|all\s+)?(?:systems?|servers?|services?|infrastructure|environments?|networks?|hosts?|devices?|production)\b"
READ_ONLY_HINT = re.compile(rf"\bread[- ]only\b|\bno\s+changes\s+(?:to|on|in)\s+{SCOPE_OBJECT}|"
                            rf"\bwithout\s+(?:making\s+|applying\s+)?(?:any\s+)?changes\s+(?:to|on|in)\s+{SCOPE_OBJECT}", re.I)
OPERATED = r"(?:integrations?|services?|connectors?|endpoints?|systems?|apis?|servers?)"
OPERATING = (r"(?:work|working|functional|operational|running|online|reachable|up\s+and\s+running|back\s+(?:up|online)|"
             r"restored|fixed|repaired)")
# An explicit demand for a working or repaired service, checked clause by clause.
DEMANDS_RESULT = re.compile(
    # "All integrations must be functional", "The VPN service should be up and running"
    rf"\b{OPERATED}\b(?:\s+[\w/-]+){{0,3}}?\s+(?:must|shall|should|needs?\s+to|has\s+to|have\s+to|(?:is|are)\s+required\s+to)\s+"
    rf"(?:(?:be|become|get)\s+)?(?:(?:fully|all|again)\s+)?{OPERATING}\b|"
    # "Make every service work again", "Get the API running"
    rf"(?:^|,)\s*(?:(?:and|then|also|please)\s+)?(?:make|get|bring)\s+(?:[\w/-]+\s+){{0,5}}?{OPERATED}\b"
    rf"(?:\s+[\w/-]+){{0,3}}?\s+{OPERATING}\b|"
    # "Fix the failing mail service", "Restore the backup integration"
    rf"(?:^|,)\s*(?:(?:and|then|also|please)\s+)?(?:fix|repair|restore|re-?enable|re-?establish|reconnect|revive)\s+"
    rf"(?:[\w/-]+\s+){{0,5}}?{OPERATED}\b|"
    # "The mail service is restored"
    rf"\b{OPERATED}\b(?:\s+[\w/-]+){{0,3}}?\s+(?:is|are|has\s+been|have\s+been)\s+(?:fully\s+)?"
    r"(?:restored|fixed|repaired|re-?enabled|back\s+online)\b", re.I)
# A demand phrase preceded by a negation in its clause is a restriction, not a demand.
NEGATION = re.compile(r"\b(?:not|never|no|none|nor|without|avoid\w*|refrain\w*|cannot)\b|n['\u2019]t\b", re.I)
CLAUSES = re.compile(r"[.;:!?]+(?:\s+|$)|\s*\b(?:but|however)\b\s*", re.I)
REPORTS_STATUS = re.compile(
    r"\bwhether\b|\bor not\b|\bstatus\b|\bhow to\b|\b(?:lists?|listed|documents?|documented|records?|recorded|reports?|reported|"
    r"describes?|described|states?|stated|notes?|noted|identif(?:y|ies|ied)|inventor(?:y|ies)|flags?|flagged|marks?|marked|"
    r"explains?|explained|recommend\w*|propos\w*|suggest\w*)\b", re.I)
STATUS_OUTCOME = re.compile(r"(?:all|every|each).{0,70}\b(?:integrations?|services?).{0,70}\b(?:working|functional|operational)\b", re.I)
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


def operational_demands(criteria):
    """Criteria that explicitly demand a working or repaired service, and uncertain ones.

    A clause that negates the demand ("never repair a service") is a restriction. A clause
    that also reports or documents ("explain how to restore") is uncertain, not a demand.
    """
    demands, uncertain = [], []
    for criterion in criteria:
        kinds = set()
        for clause in CLAUSES.split(criterion):
            found = DEMANDS_RESULT.search(clause)
            if found and not NEGATION.search(clause[:found.end()]):
                kinds.add("uncertain" if REPORTS_STATUS.search(clause) else "demand")
        if "demand" in kinds:
            demands.append(criterion)
        elif kinds:
            uncertain.append(criterion)
    return demands, uncertain


def readiness(m):
    """Conservative deterministic contradictions; network unknowns are not blockers.

    Only a scope-wide read-only brief with an explicit operational demand is a
    deterministic conflict. Anything less certain goes to the planner's semantic check.
    """
    goal = m.get("goal", "")
    constraints = m.get("constraints", "")
    criteria = " ".join(m.get("criteria", []))
    scope = goal + "\n" + constraints
    read_only = READ_ONLY.search(scope)
    # Reporting whether a service works is a normal audit outcome, not a demand.
    demands, uncertain = operational_demands(m.get("criteria", []))
    questions = []
    if read_only and demands:
        questions.append({"question": "Should this be a read-only audit that may document unavailable services, or an implementation task that makes the integrations work?",
                          "reason": "The read-only scope conflicts with a required operational outcome: " + clip_bytes(demands[0], 300) +
                                    " Revise the brief so the worker has one achievable scope."})
    policy = process_policy(m)
    # Uncertain wording is left to the planner's semantic readiness check instead of blocking.
    hint = read_only or READ_ONLY_HINT.search(scope)
    ambiguous = bool(hint and (uncertain or (demands and not read_only) or (not demands and STATUS_OUTCOME.search(criteria))))
    complex_brief = (policy["effective"] == "sensitive" or ambiguous or len(goal + constraints + criteria) > 900
                     or len(m.get("criteria", [])) > 4)
    return {"status": "clarify" if questions else "ready", "brief_hash": brief_hash(m),
            "checks": [{"name": "Required brief fields", "passed": bool(goal and m.get("criteria"))},
                       {"name": "Scope and outcome agree", "passed": not questions}],
            "questions": questions, "semantic_required": complex_brief,
            "semantic_status": "pending" if complex_brief else "not_required",
            "limitations": "Local configuration and brief checks only. External access is verified during execution."}


def clip_bytes(value, limit):
    return value.encode("utf-8")[:limit].decode("utf-8", errors="ignore")


# Concrete next-step guidance for a failed or interrupted attempt, by failure kind.
RECOVERY_GUIDANCE = {
    "time_limit": "The previous attempt ran out of time. Skip discovery that is already done, continue the partial files and save the report earlier.",
    "max_turns": "The previous attempt used all its turns. Continue the partial files, batch related edits and save the report before the last turn.",
    "progress_guard": "The previous attempt repeated the same requests without progress. Use what is already known and save the report.",
    "no_report": "The previous attempt ended without a saved report. Continue the partial files and call save_mission_report before the budget ends.",
    "invalid_output": "The previous report was rejected. Fix exactly the stated problem and save the report again.",
}


def failure_kind(attempt):
    """The runner's failure kind when recorded, otherwise derived from the error text."""
    kind = attempt.get("failure_kind")
    if kind:
        return kind
    error = str(attempt.get("error") or "")
    if re.search(r"time limit", error, re.I):
        return "time_limit"
    if "max_turns" in error or re.search(r"turn limit", error, re.I):
        return "max_turns"
    if error.startswith("progress_guard:"):
        return "progress_guard"
    if error.startswith(("Invalid output", "Cannot verify output")):
        return "invalid_output"
    return ""


def partial_outputs(m, manifest, task_id=None, limit=20):
    """Files written by failed or interrupted build attempts of unfinished tasks."""
    open_tasks = {t["id"] for t in m.get("tasks", []) if t["status"] != "done"}
    if task_id:
        open_tasks &= {task_id}
    recorded = {a["path"] for t in m.get("tasks", []) for a in t.get("artifacts", [])}
    found = {}
    for attempt in m.get("attempts", []):
        if attempt.get("phase") != "build" or attempt.get("task") not in open_tasks or attempt.get("outcome") == "reported":
            continue
        for change in attempt.get("file_changes", []):
            path = change["path"]
            if (change.get("after") is None or path in recorded or manifest.get(path) is None
                    or path.startswith(("company/projects/", ".apodex/"))):
                continue
            found[path] = {"path": clip_bytes(path, 300), "task": attempt["task"], "attempt": attempt["id"],
                           "current_sha256": manifest[path],
                           "state": "unchanged" if manifest[path] == change["after"] else "changed_read_again",
                           "error": clip_bytes(str(attempt.get("error") or "interrupted"), 200)}
    return sorted(found.values(), key=lambda item: item["path"])[:limit]


def handoff(m, manifest, verification=None, task_id=None):
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
    partial = partial_outputs(m, manifest, task_id)
    last = next((a for a in reversed(m.get("attempts", [])) if a.get("phase") == "build"
                 and (not task_id or a.get("task") == task_id)), None)
    failed = last if last and last.get("outcome") != "reported" and (last.get("error") or last.get("failure_kind")) else None
    packet = {"mission": m["id"], "brief_hash": brief_hash(m), "goal": clip_bytes(m["goal"], 1400),
              "goal_excerpt_truncated": len(m["goal"].encode()) > 1400,
              "done": [{"id": t["id"], "title": clip_bytes(t["title"], 160)} for t in m.get("tasks", []) if t["status"] == "done"],
              "next": [{"id": t["id"], "status": t["status"]} for t in m.get("tasks", []) if t["status"] != "done"],
              "blockers": [clip_bytes(q["question"], 350) for q in m.get("questions", []) if q["answer"] is None],
              "last_verified_command": {"argv": verified["argv"], "record": verification["id"]} if verified else None,
              "references": refs, "omitted_references": 0,
              "instruction": "Continue the selected pending task. Do not repeat completed actions. Changed or missing references must be read again; unchanged hashes are routing hints, not proof of correctness."}
    if partial:
        packet["partial_outputs"] = partial
        packet["partial_outputs_instruction"] = ("Files written by earlier failed or interrupted attempts. Inspect and continue "
                                                 "these files; do not recreate them. They are unreviewed work, not accepted results.")
    guidance = RECOVERY_GUIDANCE.get(failure_kind(failed)) if failed else None
    if guidance:
        packet["recovery_guidance"] = guidance
    limit = m.get("process", {}).get("handoff_bytes", 8000)
    size = lambda: len(json.dumps(packet, ensure_ascii=False).encode())
    while packet["references"] and size() > limit:
        packet["references"].pop()
        packet["omitted_references"] += 1
    while packet.get("partial_outputs") and size() > limit:
        packet["partial_outputs"].pop()
        packet["omitted_partial_outputs"] = packet.get("omitted_partial_outputs", 0) + 1
    # Large plans/questions are summarized by IDs; full task/criteria remain in the current phase prompt.
    for key in ("done", "blockers", "next"):
        while packet[key] and size() > limit:
            packet[key].pop()
            packet["omitted_" + key] = packet.get("omitted_" + key, 0) + 1
    if size() > limit:
        packet["last_verified_command"] = None
        packet["last_verified_command_omitted"] = True
    return packet
