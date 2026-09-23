"""Durable project controller. Model reports propose facts; files are checked locally.

One Studio process owns the queue. SQLite commits each transition before a worker
is launched. Attempt IDs also identify runs, so restart reconciliation is idempotent.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager

try:
    from .decisions import Decisions
    from .trace import record_transition
    from .verification import Verifications, validate_checks
    from .versions import Versions
except ImportError:
    from trace import record_transition

    from decisions import Decisions
    from verification import Verifications, validate_checks
    from versions import Versions

ACTIVE_RUN = {"running", "waiting", "stopping"}
TERMINAL = {"accepted", "cancelled", "expired"}
IDENTIFIER = re.compile(r"[a-zA-Z0-9_-]{1,60}\Z")


def text(value, name, limit=12000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"Fill in {name} (at most {limit} characters).")
    return value.strip()


def number(value, low, high, name):
    if isinstance(value, bool):
        raise ValueError(f"Invalid limit: {name}.")
    value = int(value)
    if not low <= value <= high:
        raise ValueError(f"{name}: rozsah {low}–{high}.")
    return value


def strings(value, name, maximum=50):
    if not isinstance(value, list) or not 1 <= len(value) <= maximum:
        raise ValueError(f"{name}: expected a non-empty list with at most {maximum} items.")
    return [text(v, name, 3000) for v in value]


def parse_plan(report):
    tasks = report.get("tasks")
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= 40:
        raise ValueError("Plan must have 1–40 tasks.")
    result = []
    ids = set()
    for item in tasks:
        key = text(item.get("id"), "Task ID", 60)
        if not IDENTIFIER.fullmatch(key) or key in ids:
            raise ValueError("Task IDs must be unique, without spaces or slashes.")
        ids.add(key)
        deps = item.get("depends_on", [])
        if not isinstance(deps, list) or any(not isinstance(d, str) for d in deps):
            raise ValueError("Dependencies must be a list of IDs.")
        result.append({"id": key, "title": text(item.get("title"), "name", 200),
                       "instructions": text(item.get("instructions"), "instrukce"),
                       "criteria": strings(item.get("criteria"), "criteria", 20),
                       "depends_on": list(dict.fromkeys(deps)), "status": "pending",
                       "cycles": 0, "artifacts": [], "feedback": ""})
    done = set()
    while len(done) < len(ids):
        ready = {t["id"] for t in result if set(t["depends_on"]) <= done} - done
        if not ready:
            raise ValueError("The plan contains a cycle or a non-existent dependency.")
        done.update(ready)
    return result


class Missions:
    def __init__(self, studio, clock=time.time):
        self.studio = studio
        self.clock = clock
        self.lock = threading.RLock()
        self.path = studio.data / "projects.sqlite3"
        self.stop_event = threading.Event()
        self.thread = None
        self.last_error = ""
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS missions (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS mission_trace (id TEXT PRIMARY KEY, mission TEXT NOT NULL, at REAL NOT NULL, data TEXT NOT NULL)")
            db.execute("CREATE INDEX IF NOT EXISTS mission_trace_time ON mission_trace(mission, at)")
        self.verifications = Verifications(self)
        self.versions = Versions(self)
        self.decisions = Decisions(self)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute("PRAGMA synchronous=FULL")
            with db:
                yield db
        finally:
            db.close()

    def save(self, m, db=None):
        m["updated"] = self.clock()
        if db is not None:
            previous = db.execute("SELECT data FROM missions WHERE id=?", (m["id"],)).fetchone()
            record_transition(db, previous[0] if previous else None, m, m["updated"])
            db.execute("INSERT INTO missions VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                       (m["id"], json.dumps(m, ensure_ascii=False)))
            return
        with self.connect() as db:
            self.save(m, db)

    def get(self, key):
        with self.connect() as db:
            row = db.execute("SELECT data FROM missions WHERE id=?", (key,)).fetchone()
        if not row:
            raise ValueError("The long-term project does not exist.")
        return json.loads(row[0])

    def trace(self, key):
        self.get(key)
        with self.connect() as db:
            rows = db.execute("SELECT data FROM mission_trace WHERE mission=? ORDER BY at DESC, rowid DESC LIMIT 500", (key,))
            return [json.loads(row[0]) for row in rows]

    def list(self, project=None):
        with self.lock, self.connect() as db:
            result = [json.loads(row[0]) for row in db.execute("SELECT data FROM missions")]
        return sorted([m for m in result if project is None or m["project"] == project or m.get("work_project") == project],
                      key=lambda m: m["created"], reverse=True)

    def create(self, body):
        with self.lock:
            m = self.prepare(body)
            self.save(m)
        return m

    def prepare(self, body):
        """Validate a draft without writing, for atomic product/mission creation."""
        root = self.studio.project(body["project"])
        profiles, default = self.studio.profiles()
        profile = body.get("profile", default)
        reviewer = body.get("review_profile", profile)
        if profile not in profiles or reviewer not in profiles:
            raise ValueError("Select an available model for work and review.")
        decision_profile = body.get("decision_profile") or None
        if decision_profile and (decision_profile not in profiles or profiles[decision_profile].get("protocol") != "chat_completions"
                                 or profiles[decision_profile].get("oauth_provider")):
            raise ValueError("The decision model must be a profile-compatible chat API without OAuth.")
        now = self.clock()
        m = {"id": uuid.uuid4().hex[:16], "project": body["project"],
             "title": text(body.get("title"), "project name", 160),
             "goal": text(body.get("goal"), "target product"),
             "criteria": strings(body.get("criteria"), "conditions for a completed product", 40),
             "constraints": str(body.get("constraints", ""))[:12000],
             "sources": str(body.get("sources", ""))[:12000],
             "profile": profile, "review_profile": reviewer,
             "decision_profile": decision_profile,
             "auto_approve": body.get("auto_approve") is True,
             "verification_checks": validate_checks(body.get("verification_checks", [])),
             "verification_id": None,
             "isolated": body.get("isolated", True) is not False,
             "days": number(body.get("days", 7), 1, 30, "Project duration in days"),
             "max_attempts": number(body.get("max_attempts", 100), 2, 1000, "Number of runs"),
             "attempt_minutes": number(body.get("attempt_minutes", 30), 1, 360, "Minutes per run"),
             "max_turns": number(body.get("max_turns", 40), 1, 200, "Steps per run"),
             "status": "draft", "phase": "plan", "created": now, "updated": now,
             "deadline": None, "attempts": [], "tasks": [], "questions": [],
             "active_attempt": None, "message": "Check the task brief and start preparation.",
             "retry_at": 0, "failures": 0, "final_report": None,
             "final_cycles": 0, "evidence": [],
             "workspace": str(root)}
        return m

    def questions(self, m, values, task=None):
        if not isinstance(values, list) or not 1 <= len(values) <= 20:
            raise ValueError("The block must contain 1–20 questions.")
        for item in values:
            m["questions"].append({"id": uuid.uuid4().hex[:12],
                "question": text(item.get("question"), "question", 3000),
                "reason": text(item.get("reason"), "question reason", 3000),
                "task": task, "answer": None, "created": self.clock()})

    def action(self, body):
        with self.lock:
            m = self.get(body["id"])
            action = body["action"]
            company = m.get("company_context")
            if company and action in {"accept", "manual_accept"}:
                self.require_active_product(m)
            if company and action == "runtime_settings":
                raise ValueError("Corporate execution has reserved limits and models. To change them, create a new task in the company.")
            if action in {"start", "resume", "approve_plan", "recheck"}:
                self.require_active_product(m)
            if action == "runtime_settings" and m["status"] in {"draft", "paused", "blocked"}:
                if m.get("active_attempt"):
                    raise ValueError("Wait for the ongoing run to finish.")
                profiles, _ = self.studio.profiles()
                profile = body.get("profile", m["profile"])
                reviewer = body.get("review_profile", m["review_profile"])
                if profile not in profiles or reviewer not in profiles:
                    raise ValueError("Select an available model for work and review.")
                minutes = number(body.get("attempt_minutes", m["attempt_minutes"]), 1, 360, "Minutes per run")
                turns = number(body.get("max_turns", m["max_turns"]), 1, 200, "Steps per run")
                attempts = number(body.get("max_attempts", m["max_attempts"]), max(2, len(m["attempts"]) + 1), 1000, "Number of runs")
                m.update(profile=profile, review_profile=reviewer, attempt_minutes=minutes,
                         max_turns=turns, max_attempts=attempts,
                         message="Models and limits saved. Continue independently; the overall deadline remains unchanged.")
            elif action == "revise_task" and m["status"] in {"paused", "blocked"}:
                if m.get("active_attempt"):
                    raise ValueError("Wait for the ongoing run to finish.")
                if company and self.studio.companies.get(company["id"])["status"] != "paused":
                    raise ValueError("Pause the parent Driver before editing the task brief.")
                task = next((t for t in m["tasks"] if t["id"] == body.get("task")), None)
                if not task or task["status"] not in {"pending", "waiting"}:
                    raise ValueError("Only tasks not yet accepted can be edited.")
                if body.get("expected_criteria") != task["criteria"]:
                    raise ValueError("The criteria have changed meanwhile. Refresh the overview.")
                reason = text(body.get("reason"), "reason for change", 3000)
                criteria = strings(body.get("criteria"), "task criteria", 20)
                instructions = text(body.get("instructions", task["instructions"]), "instrukce", 12000)
                m.setdefault("plan_revisions", []).append({"at": self.clock(), "task": task["id"],
                    "reason": reason, "before": {"criteria": task["criteria"], "instructions": task["instructions"]},
                    "after": {"criteria": criteria, "instructions": instructions}})
                task.update(criteria=criteria, instructions=instructions)
                for q in m["questions"]:
                    if q["task"] == task["id"] and q["answer"] is None and q.get("kind") == "criterion" and q.get("criterion") not in criteria:
                        q["answer"] = "Criterion replaced by recorded task brief adjustment: " + reason
                if task["status"] == "waiting" and not any(q["task"] == task["id"] and q["answer"] is None for q in m["questions"]):
                    task["status"] = "pending"
                m.update(failures=0, retry_at=0, message="Task brief adjusted; original wording remains in history. Continue independently.")
            elif action == "set_checks" and m["status"] in {"draft", "paused", "awaiting_plan", "awaiting_checks", "ready"}:
                checks = validate_checks(body.get("verification_checks"))
                if not checks:
                    raise ValueError("Add at least one verification command.")
                m.update(verification_checks=checks, verification_id=None)
                if m["status"] in {"awaiting_checks", "ready"}:
                    self.require_active_product(m)
                    m.update(status="verifying", message="I will execute approved verifications.")
                elif m["status"] == "paused" and m.get("final_report"):
                    m["resume_status"] = "verifying"
            elif action == "manual_accept" and m["status"] == "awaiting_checks":
                if body.get("acknowledge_unverified") is not True:
                    raise ValueError("Confirm manual acceptance without automatic verifications.")
                self.verify_delivery(m, require_checks=False)
                self.capture_delivery(m)
                m.update(status="accepted", acceptance={"kind": "manual", "at": self.clock()},
                         message="Manually accepted by owner without independent automatic verifications.")
            elif action == "answer":
                q = next((q for q in m["questions"] if q["id"] == body.get("question")), None)
                if not q or q["answer"] is not None or m["status"] in TERMINAL:
                    raise ValueError("The question is no longer open.")
                q["answer"] = text(body.get("answer"), "answer", 12000)
                pending = [x for x in m["questions"] if x["task"] == q["task"] and x["answer"] is None]
                if not pending and q["task"]:
                    task = next(t for t in m["tasks"] if t["id"] == q["task"])
                    task["status"] = "pending"
                if m["status"] == "waiting" and not any(q["answer"] is None for q in m["questions"]):
                    m["status"] = "awaiting_plan" if m["phase"] == "plan" and m["tasks"] else "running"
                m["message"] = "Answer saved."
            elif action == "start" and m["status"] == "draft":
                m.update(status="running", deadline=self.clock() + m["days"] * 86400,
                         message="Preparing the plan and input questions.")
            elif action == "approve_plan" and m["status"] == "awaiting_plan":
                if any(q["answer"] is None for q in m["questions"]):
                    raise ValueError("First, answer the input questions.")
                m.update(status="running", phase="build", message="Plan confirmed. Proceeding with execution.")
            elif action in {"pause", "cancel"} and m["status"] not in TERMINAL:
                if m["status"] != "paused":
                    m["resume_status"] = m["status"]
                m.update(status="paused" if action == "pause" else "cancelled",
                         message="Paused by user." if action == "pause" else "Terminated by user.")
                self.save(m)  # persist intent before terminating a worker
                self.verifications.stop(m.get("verification_id"))
                if m["active_attempt"] in self.studio.runs:
                    self.studio.stop(m["active_attempt"])
            elif action == "resume" and m["status"] in {"paused", "blocked"}:
                if m["deadline"] is not None and self.clock() >= m["deadline"]:
                    raise ValueError("Time limit expired; create a follow-up project with a new scope.")
                if len(m["attempts"]) >= m["max_attempts"]:
                    raise ValueError("Run limit exhausted; create a follow-up project.")
                previous = m.get("resume_status", "running") if m["status"] == "paused" else "running"
                if previous == "blocked":
                    previous = "running"
                if previous == "waiting" and not any(q["answer"] is None for q in m["questions"]):
                    previous = "awaiting_plan" if m["phase"] == "plan" and m["tasks"] else "running"
                m.update(status=previous, failures=0, retry_at=0, message="Continuing from saved state.")
            elif action == "recheck" and m["status"] in {"ready", "awaiting_checks"}:
                m.update(status="running", final_report=None, verification_id=None, message="Re-verifying the product.")
            elif action == "accept" and m["status"] == "ready":
                # Re-check the delivered files; accepting stale evidence would hide later edits.
                self.verify_delivery(m)
                self.capture_delivery(m)
                m.update(status="accepted", acceptance={"kind": "verified", "at": self.clock()},
                         message="Product accepted after independent verifications.")
            else:
                raise ValueError("This action is not available in the current state.")
            self.save(m)
            return m

    def require_active_product(self, m):
        """Parent pause is authoritative even for direct mission API calls."""
        company_id = (m.get("company_context") or {}).get("id")
        if company_id:
            company = self.studio.companies.get(company_id)
            if company["status"] != "active" or not company["deadline"] or self.clock() >= company["deadline"]:
                raise ValueError("First, restore the parent company and its time horizon.")
        product_id = (m.get("product_context") or {}).get("id")
        if product_id:
            product = self.studio.products.get(product_id)
            if product["status"] != "active":
                raise ValueError("First, restore the parent product; its execution is paused.")

    def ensure_workspace(self, m):
        if not m.get("isolated") or m.get("work_project"):
            return
        root = self.studio.project(m["project"])
        self.versions.recover(root)
        if not m.get("base_version"):
            m["base_version"] = self.versions.snapshot(root, label="Default execution state " + m["id"])["id"]
            self.save(m)
        base = self.versions.get(m["base_version"])
        destination = self.studio.data / "workspaces" / m["id"]
        if not destination.exists():
            staging = destination.with_name(m["id"] + "." + uuid.uuid4().hex + ".tmp")
            self.versions.materialize(base, staging)
            os.replace(staging, destination)
        else:
            from_manifest = self.versions.preview(destination, base)
            if from_manifest["changed"]:
                raise ValueError("Incomplete workspace preparation contains changes; automatic overwrite rejected.")
        project = self.studio.add_project(destination, name=m["title"][:70] + " · working version")
        m.update(work_project=project["id"], workspace=str(destination))
        self.save(m)

    def capture_delivery(self, m):
        expected = None
        expected_modes = None
        if m.get("verification_id"):
            evidence = self.verifications.verify(m)
            expected = evidence["sources"]
            expected_modes = evidence["source_modes"]
        version = self.versions.snapshot(m["workspace"], label=m["title"], expected=expected, expected_modes=expected_modes)
        if m.get("work_project"):
            operation = self.versions.apply(self.studio.project(m["project"]), version,
                                            base=self.versions.get(m["base_version"]))
            m["promotion"] = operation["id"]
        m["version_id"] = version["id"]

    def report_path(self, m, attempt):
        return f"company/projects/{m['id']}/reports/{attempt['id']}.json"

    def prompt(self, m, a):
        task = next((t for t in m["tasks"] if t["id"] == a["task"]), None)
        path = self.report_path(m, a)
        common = f"""You are working on the long-running AI Build Company project: {m['title']}.
PHASE OF THIS RUN: {a['phase']}. The product goal below is context; follow only the instructions for this phase.
The project root is {m['workspace']}. File tools accept /workspace as an alias for this folder.
The shell runs in the physical project directory: use relative paths; do not create a system /workspace.
Goal: {m['goal']}
Product criteria: {json.dumps(m['criteria'], ensure_ascii=False)}
Constraints: {m['constraints']}
Verification commands approved by the owner (the controller will run them independently after review): {json.dumps(m.get('verification_checks', []), ensure_ascii=False)}
Background and public sources: {m['sources']}
Owner answers: {json.dumps([q for q in m['questions'] if q['answer'] is not None], ensure_ascii=False)}
Work only within the task scope and the tools permitted for the current phase. Do not invent sources or checks.
Unknowns that can be resolved through exploration belong in execution tasks; ask the owner only for necessary decisions.
After an interruption, first inspect files and previous reports in company/projects/{m['id']}/reports/;
do not blindly repeat completed actions. Do not make payments, message third parties or deploy;
prepare these steps as material for the owner to decide on. Do not overwrite the Studio database or configuration.
If company/ai-build-company.json exists, read the relevant rules and role instructions.
Save results directly to the agreed project paths, not only to temporary /outputs.
Finally, call save_mission_report with the named fields status, tasks, questions or the result fields for the phase.
The application will validate the content and save the JSON file {path} in the project. Do not create this file manually.
Do not pass a path, content, data or serialized JSON text. Use the tool fields directly.
Follow the specified field names and status value exactly; do not invent a report format.
Errors from previous attempts that you must fix: {json.dumps([x.get('error') for x in m['attempts'][-4:] if x.get('error')], ensure_ascii=False)}
The report must not list itself as a product artifact. Paths are relative to the project root.
If you need a human decision, return {{"status":"blocked","questions":[{{"question":"...","reason":"..."}}]}}.
"""
        if m.get("company_context"):
            common += "\nCompany and department context: " + json.dumps(m["company_context"], ensure_ascii=False) + "\n"
        if m.get("product_context"):
            common += "\nContext of permanently managed product: " + json.dumps(m["product_context"], ensure_ascii=False) + "\n"
            common += ("Build upon existing files. Preserve existing functions and data; "
                       "do not rewrite the entire product without justification. Verify regression criteria as well as the new requirement. "
                       "Prepare instructions for running, verifying, and maintaining the product appropriate to its type. "
                       "Do not mark physical production, deployment, or external services as complete without actual proof.\n")
        if a["phase"] == "plan":
            return common + "You are the planner. PLAN ONLY NOW, DO NOT CREATE THE FINAL PRODUCT.\nUse at most three read calls for local references, then immediately save the plan.\nBe concise: usually 2–5 execution tasks, brief instructions, and specific criteria suffice.\nThe planner’s role is to outline steps, not to obtain results from those steps.\nShell and web access are intentionally unavailable at this stage; the worker may have them. This is not evidence of missing access.\nIf the task brief includes a server, URL, or access command, transfer it exactly into the execution task and plan its actual verification.\nFor example, if an existing SSH command is provided, the worker should first test it; do not ask again how to connect or whether they may perform the already-specified read.\nOnly ask about access issues after a concrete failure in execution. Never require disclosure of secrets.\nBefore asking a question, verify whether the task brief already resolves it. Missing Markdown template, non-existent output file,\nor exploration that has not yet been performed are not plan blockers. Design the format according to product criteria.\nAsk questions only where a decision by the owner is required to even draft a safe first task.\nExecution tasks must produce the desired product. Creating or verifying this planning JSON\nmust not be among them: plan validation and separate review are performed automatically by the controller.\nTask criteria must not tighten the owner’s goal. For an inventory that allows unknown or missing\nservices, a valid outcome is also documented non-discovery, stating the exploration scope and limitations.\nDo not require finding or service functionality whose existence the task brief is still determining.\nStrictly preserve target paths from the task brief. The folder company/projects/.../reports is only for internal reports,\nit is not automatically a product folder. File names mentioned in the task brief imply paths relative to the project root.\nDo not yet create the product or make changes outside your report. Publicly discoverable items belong in the research task.\nReport: {\"status\":\"plan\",\"questions\":[{\"question\":\"...\",\"reason\":\"...\"}],\"tasks\":[\n{\"id\":\"task-1\",\"title\":\"...\",\"instructions\":\"Specific work and target files\",\n\"depends_on\":[],\"criteria\":[\"Verifiable condition\"]}]}. Questions may be empty.\nTasks must have unique IDs, no cycles, and no dependencies on non-existent tasks. Maximum 40 tasks.\n"
        if a["phase"] == "build":
            template = {"status": "done", "summary": "Add summary of completed work.",
                "artifacts": ["relative/file"],
                "checks": [{"criterion": criterion, "passed": False, "evidence": "Add actual result."}
                           for criterion in task["criteria"]], "sources": []}
            return common + f"""You are the worker for task: {json.dumps(task, ensure_ascii=False)}
Complete this task and check its outputs. The report must cover this task's criteria;
general product criteria are not a substitute. Preserve the exact criterion wording from this template:
{json.dumps(template, ensure_ascii=False)}
Fill in the actual files, summary and evidence; change passed to true only after verification.
Do not mark a failure as passed. If you used sources, each sources entry has url and finding fields.
"""
        criteria = task["criteria"] if task else m["criteria"]
        evidence = task if task else m["tasks"]
        template = {"status": "changes", "summary": "Add findings or review result.",
            "artifacts": ["verified/file"],
            "checks": [{"criterion": criterion, "passed": False, "evidence": "Add your own findings from files."}
                       for criterion in criteria]}
        return common + f"""You are an independent reviewer in a new session. Inspect the actual files and evaluate the criteria against their content;
do not rely on the author's claims. Do not edit product files, only your report. Return findings to the author.
You have no shell in this phase. The controller will run independent commands after the final review; do not claim you ran them yourself.
Background: {json.dumps(evidence, ensure_ascii=False)}
Criteria to verify: {json.dumps(criteria, ensure_ascii=False)}
Preserve the exact criterion wording from this template and add your own findings:
{json.dumps(template, ensure_ascii=False)}
Provide a check for EVERY criterion, with passed true only after actual verification. Change status
to pass only when all criteria are met; otherwise use changes.
For changes, describe specific reproducible defects in summary.
"""

    def artifacts(self, m, paths):
        paths = strings(paths, "output files", 100)
        result = []
        for path in dict.fromkeys(paths):
            if path.startswith("company/projects/") or path.startswith(".apodex/"):
                raise ValueError("Report or temporary output is not a final product file.")
            revision = self.studio.artifact_revision(m.get("work_project", m["project"]), path)
            result.append({"path": path, "sha256": revision})
        return result

    def checks(self, report, criteria, passing=True):
        checks = report.get("checks")
        if not isinstance(checks, list) or len(checks) > 100:
            raise ValueError("Missing documented checks.")
        found = set()
        for item in checks:
            criterion = text(item.get("criterion"), "criterion", 3000)
            text(item.get("evidence"), "proof of check", 6000)
            if not isinstance(item.get("passed"), bool) or (passing and not item["passed"]):
                raise ValueError("Some checks failed.")
            found.add(criterion)
        if not set(criteria) <= found:
            missing = [criterion for criterion in criteria if criterion not in found]
            raise ValueError("Report does not cover all criteria. Missing precise wording: " +
                             json.dumps(missing, ensure_ascii=False)[:1800])
        return checks

    def verify_delivery(self, m, require_checks=True):
        report = m["final_report"]
        if not report:
            raise ValueError("Missing final review.")
        for item in report["verified_artifacts"]:
            project = m["project"] if m["status"] == "accepted" else m.get("work_project", m["project"])
            current = self.studio.artifact_revision(project, item["path"])
            if current != item["sha256"]:
                raise ValueError(f"File {item['path']} has changed since review. New verification is required.")
        legacy_accepted = m["status"] == "accepted" and "verification_checks" not in m
        if require_checks and not legacy_accepted and (m.get("acceptance") or {}).get("kind") != "manual":
            self.verifications.verify(m)

    def consume(self, m, a):
        raw = self.studio.read_file(m.get("work_project", m["project"]), self.report_path(m, a))
        report = json.loads(raw["content"])
        # Some compatible models pass an already serialized object to
        # create_file(data=...). Accept that single extra JSON encoding;
        # retain the original bytes/hash as evidence and validate every field.
        if isinstance(report, str):
            report = json.loads(report)
        if not isinstance(report, dict):
            raise ValueError("Report must be a JSON object.")
        phase, task_id = a["phase"], a["task"]
        task = next((t for t in m["tasks"] if t["id"] == task_id), None)
        status = report.get("status")
        if status == "blocked":
            self.questions(m, report.get("questions"), task_id)
            if task:
                task["status"] = "waiting"
            else:
                m["status"] = "waiting"
            m["message"] = "I need an answer; independent tasks may proceed."
        elif phase == "plan" and status == "plan":
            tasks = parse_plan(report)
            if any(a["id"] + ".json" in json.dumps(t, ensure_ascii=False) for t in tasks):
                raise ValueError("Execution task must not create or verify its own planning report. "
                                 "Remove this internal step; plan validation and review are performed by the controller.")
            if report.get("questions"):
                self.questions(m, report["questions"])
            m.update(tasks=tasks, status="waiting" if any(q["answer"] is None for q in m["questions"]) else "awaiting_plan",
                     message="Review input questions and proposed plan.")
        elif phase == "build" and status == "done":
            checked = self.artifacts(m, report.get("artifacts"))
            checks = self.checks(report, task["criteria"], passing=False)
            failed = [c for c in checks if not c["passed"]]
            if failed:
                question_start = len(m["questions"])
                self.questions(m, [{"question": "Unmet criterion: " + c["criterion"][:2300] +
                    " — add supporting details or adjust task brief.", "reason": c["evidence"][:3000]}
                    for c in failed[:20]], task_id)
                for q, check in zip(m["questions"][question_start:], failed):
                    q.update(kind="criterion", criterion=check["criterion"])
                task["status"] = "waiting"
                m["message"] = "Result does not meet criteria. Waiting for clarification without repeating the same attempt."
            else:
                task.update(status="review", artifacts=checked, checks=checks,
                            summary=text(report.get("summary"), "summary"), build_run=a["id"])
        elif phase in {"review", "final"} and status in {"pass", "changes"}:
            summary = text(report.get("summary"), "review summary")
            if status == "changes":
                if task:
                    task["cycles"] += 1
                    task.update(status="pending", feedback=summary)
                    if task["cycles"] >= 3:
                        m.update(status="blocked", message="Three rounds of corrections without acceptance. Task brief needs adjustment.")
                else:
                    m["final_cycles"] += 1
                    m.update(status="blocked" if m["final_cycles"] >= 3 else "running",
                             message="Final check returned defects: " + summary)
                    # Preserve completed work but reopen a delivery task with the findings.
                    m["tasks"][-1].update(status="pending", feedback=summary)
            else:
                criteria = task["criteria"] if task else m["criteria"]
                checks = self.checks(report, criteria)
                checked = self.artifacts(m, report.get("artifacts"))
                expected = task["artifacts"] if task else [x for t in m["tasks"] for x in t["artifacts"]]
                if not {x["path"] for x in expected} <= {x["path"] for x in checked}:
                    raise ValueError("Review does not cover all output files.")
                if task:
                    # A reviewer may not quietly change what it was asked to review.
                    if any(x not in checked for x in expected):
                        raise ValueError("Outputs changed during review; reprocessing is required.")
                    task.update(status="done", review_run=a["id"], review_summary=summary, review_checks=checks)
                else:
                    m.update(status="verifying", verification_id=None,
                             message="Review completed. Independent checks follow.",
                             final_report={**report, "verified_artifacts": checked, "run": a["id"]})
        else:
            raise ValueError(f"Unexpected report for phase {phase}: {status}.")
        a["report"] = self.report_path(m, a)
        a["report_sha256"] = hashlib.sha256(raw["content"].encode()).hexdigest()
        m["evidence"].append({"attempt": a["id"], "phase": phase, "task": task_id,
                              "report": report, "recorded": self.clock(),
                              "report_sha256": a["report_sha256"]})
        m["failures"] = 0

    def fail(self, m, a, reason):
        a["error"] = str(reason)[:2000]
        m["failures"] += 1
        m["message"] = str(reason)[:2000]
        if str(reason).startswith("progress_guard:") or (a["phase"] == "plan" and
                ("time limit" in str(reason) or str(reason) == "max_turns")):
            m["status"] = "blocked"
            m["message"] = "Run stopped without automatic retry. " + str(reason)[:1600]
            if not any(q["answer"] is None for q in m["questions"]):
                self.questions(m, [{"question": "How should I adjust the approach before restarting the task?", "reason": m["message"]}])
        elif m["failures"] >= 3:
            m["status"] = "blocked"
            m["message"] = "Three unsuccessful attempts. " + m["message"]
        else:
            m["retry_at"] = self.clock() + 30 * 2 ** (m["failures"] - 1)

    def next_work(self, m):
        if m["phase"] == "plan":
            return "plan", None
        done = {t["id"] for t in m["tasks"] if t["status"] == "done"}
        candidates = []
        for t in m["tasks"]:
            if set(t["depends_on"]) <= done and t["status"] in {"pending", "review"}:
                candidates.append(("review" if t["status"] == "review" else "build", t["id"]))
        if candidates:
            return self.decisions.select(m, candidates)
        if len(done) == len(m["tasks"]) and done:
            return "final", None
        m.update(status="waiting", message="Waiting for answers to blocked tasks.")
        return None

    def tick(self):
        with self.lock:
            for m in reversed(self.list()):  # oldest first, sharing Studio's single worker slot
                self.advance(m)

    def advance(self, m):
        now = self.clock()
        a = next((a for a in m["attempts"] if a["id"] == m["active_attempt"]), None)
        if m["status"] not in TERMINAL | {"paused", "ready", "awaiting_checks"}:
            try:
                self.require_active_product(m)
            except ValueError as exc:
                m.update(resume_status=m["status"], status="paused", message=str(exc))
                self.save(m)
                self.verifications.stop(m.get("verification_id"))
                if a and a["id"] in self.studio.runs:
                    self.studio.stop(a["id"])
        if m["deadline"] and now >= m["deadline"] and m["status"] not in TERMINAL | {"ready", "awaiting_checks"}:
            m.update(status="expired", message="Project time limit exceeded; results remain saved.")
            self.save(m)
            self.verifications.stop(m.get("verification_id"))
            if a and a["id"] in self.studio.runs:
                self.studio.stop(a["id"])
        if a:
            run = self.studio.runs.get(a["id"])
            if run and run["status"] in ACTIVE_RUN:
                error_before = a.get("error")
                waiting = self.studio.approval_wait_seconds(a["id"], now)
                if now - a["started"] - waiting > a.get("budget_seconds", m["attempt_minutes"] * 60):
                    a["error"] = "Run exceeded time limit."
                    self.studio.stop(a["id"])
                directory = self.studio.run_dir(a["id"])
                size = sum(p.stat().st_size for p in directory.glob('*') if p.is_file())
                if size > 128_000_000 or size + sum(x.get("log_bytes", 0) for x in m["attempts"]) > 1_000_000_000:
                    a["error"] = "Project exceeded log limit (128 MB per run, 1 GB per project)."
                    self.studio.stop(a["id"])
                    m["status"] = "blocked"
                if a.get("error") != error_before:
                    self.save(m)
                return
            m["active_attempt"] = None
            a["ended"] = now
            if run:
                a["log_bytes"] = sum(p.stat().st_size for p in self.studio.run_dir(a["id"]).glob('*') if p.is_file())
                a["usage"] = run.get("usage")
                a["elapsed_seconds"] = max(0, run.get("ended", now) - run.get("created", a["started"]))
            if a.get("source_version"):
                try:
                    from_version = self.versions.get(a["source_version"])
                    current_version = self.versions.snapshot(m["workspace"], label="After run " + a["id"])
                    before, after = from_version["files"], current_version["files"]
                    a["result_version"] = current_version["id"]
                    a["file_changes"] = [{"path": p, "before": before.get(p), "after": after.get(p),
                                          "mode_before": from_version["modes"].get(p),
                                          "mode_after": current_version["modes"].get(p)}
                                         for p in sorted(before.keys() | after.keys())
                                         if before.get(p) != after.get(p) or from_version["modes"].get(p) != current_version["modes"].get(p)]
                except Exception as exc:
                    a["change_capture_error"] = str(exc)[:2000]
            if m["status"] in {"paused", "cancelled", "expired"}:
                a["outcome"] = "interrupted"
            elif run and run["status"] == "completed":
                try:
                    if a.get("change_capture_error"):
                        raise ValueError(a["change_capture_error"])
                    if a["phase"] in {"plan", "review", "final"} and a.get("file_changes"):
                        raise ValueError("Planning or review modified product files; output cannot be accepted.")
                    # Validate on a copy so rejected reports cannot partially advance the project.
                    copy = json.loads(json.dumps(m))
                    ca = next(x for x in copy["attempts"] if x["id"] == a["id"])
                    self.consume(copy, ca)
                    ca["outcome"] = "reported"
                    m = copy
                except (ValueError, TypeError, KeyError, OSError, RuntimeError) as exc:
                    self.fail(m, a, f"Invalid output: {exc}")
                except Exception as exc:
                    # Studio's protected-file errors also invalidate a report.
                    self.fail(m, a, f"Cannot verify output: {exc}")
            else:
                self.fail(m, a, a.get("error") or (run or {}).get("reason") or "Interrupted run; verifying existing files and resuming.")
            self.save(m)
        if m["status"] == "verifying":
            self.advance_verification(m)
            return
        if m["status"] != "running" or now < m["retry_at"]:
            return
        if self.verifications.active():
            return
        if any(r["status"] in ACTIVE_RUN for r in self.studio.runs.values()):
            return
        try:
            self.ensure_workspace(m)
        except Exception as exc:
            m.update(status="blocked", message="Workspace preparation failed: " + str(exc))
            self.save(m)
            return
        if len(m["attempts"]) >= m["max_attempts"]:
            m.update(status="blocked", message="Run limit exhausted; current results are saved.")
            self.save(m)
            return
        work = self.next_work(m)
        if not work:
            self.save(m)
            return
        phase, task_id = work
        try:
            from .progress_guard import phase_limits
        except ImportError:
            from progress_guard import phase_limits
        seconds, turns = phase_limits(phase, m["attempt_minutes"] * 60, m["max_turns"])
        a = {"id": uuid.uuid4().hex[:16], "phase": phase, "task": task_id, "started": now, "budget_seconds": seconds}
        try:
            a["source_version"] = self.versions.snapshot(m["workspace"], label="Before run " + a["id"])["id"]
        except Exception as exc:
            m.update(status="blocked", message="Cannot save initial run state: " + str(exc))
            self.save(m)
            return
        m["attempts"].append(a)
        m["active_attempt"] = a["id"]
        m["message"] = {"plan": "Preparing plan and questions.", "build": "Working on task: ",
                        "review": "Model review of task: ", "final": "Final model review of product."}[phase] + (task_id or "")
        self.save(m)  # crash before launch is a retryable reserved attempt, never a duplicate
        try:
            self.studio.launch({"project": m.get("work_project", m["project"]), "task": self.prompt(m, a),
                "profile": m["review_profile"] if phase in {"review", "final"} else m["profile"],
                "mode": "react", "max_turns": turns, "auto_approve": m["auto_approve"]},
                mission={"id": m["id"], "attempt": a["id"], "phase": phase,
                         "attempt_seconds": seconds})
        except Exception as exc:
            m["active_attempt"] = None
            self.fail(m, a, str(exc))
            self.save(m)

    def advance_verification(self, m):
        if not m.get("verification_checks"):
            m.update(status="awaiting_checks", message="Review complete. Provide commands for independent checks, or explicitly accept the result manually.")
            self.save(m)
            return
        key = m.get("verification_id")
        if key:
            record = self.verifications.get(key)
            if record["status"] == "running":
                return
            m["verification_result"] = {k: record[k] for k in ("id", "status", "started", "ended")}
            m["verification_result"]["checks"] = [{k: c[k] for k in ("id", "label", "exit_code", "started", "ended")}
                                                   for c in record["checks"]]
            if record["status"] == "passed":
                try:
                    self.verify_delivery(m)
                    m.update(status="ready", message="Independent checks and review passed. Ready for acceptance.")
                except Exception as exc:
                    m.update(status="blocked", resume_status="verifying", verification_id=None, message=str(exc))
            else:
                reason = record.get("error", "Check failed.")
                m["final_cycles"] += 1
                log = "\n".join(c.get("log", "")[-6000:] for c in record["checks"][-2:])
                m["tasks"][-1].update(status="pending", feedback=f"Independent check {key}: {reason}\n{log}")
                m.update(status="blocked" if m["final_cycles"] >= 3 else "running", final_report=None,
                         verification_id=None, message="Independent checks returned corrections: " + reason)
            self.save(m)
        elif not self.verifications.active() and not any(r["status"] in ACTIVE_RUN for r in self.studio.runs.values()):
            m["verification_id"] = self.verifications.start(m)
            self.save(m)

    def start(self):
        if self.thread:
            return
        def loop():
            while not self.stop_event.is_set():
                try:
                    if hasattr(self.studio, "deployments"):
                        self.studio.deployments.tick()
                    if hasattr(self.studio, "products"):
                        self.studio.products.tick()
                    if hasattr(self.studio, "companies"):
                        self.studio.companies.tick()
                    self.tick()
                    self.last_error = ""
                except Exception as exc:
                    self.last_error = str(exc)[:1000]
                self.stop_event.wait(2)
        self.thread = threading.Thread(target=loop, daemon=True, name="studio-projects")
        self.thread.start()

    def capabilities(self):
        from dotenv import dotenv_values

        fallback = self.studio.config.parent / "frontier" / ".env"
        env = {**dotenv_values(fallback), **dotenv_values(self.studio.config.parent / ".env"), **os.environ}
        key = env.get("SERPER_API_KEY", "") or ""
        return {"web_search_configured": bool(key.strip()),
                "search_status": "Serper login configured; availability not live-verified."
                if key.strip() else "Search not configured (Serper). Provide links or set SERPER_API_KEY.",
                "direct_fetch": True, "controller": "Studio on this computer; it must remain running."}

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=10)
        self.verifications.close()
        self.decisions.close()
