"""Company portfolio and bounded durable driver, sharing the mission transaction/lock.

A department is context, not a security boundary. External actions are an inbox,
never executable jobs. Native tools retain the host user's permissions.
"""
from __future__ import annotations

import json
import math
import time
import uuid

try:
    from .missions import (RECOVERABLE, ROUND_KINDS, TERMINAL, failure_kind, number,
                           recovery_question, released, strings, text, used_runs)
except ImportError:
    from missions import (RECOVERABLE, ROUND_KINDS, TERMINAL, failure_kind, number,
                          recovery_question, released, strings, text, used_runs)

DEPARTMENTS = {"operations": "Management and Operations", "delivery": "Development and Delivery",
               "growth": "Sales and marketing", "finance": "Finance and Administration",
               "platform": "Infrastructure and Data"}
POLICY = {"days": 7, "run_budget": 100, "cycle_limit": 20, "attempts_per_cycle": 10, "attempt_minutes": 20,
          "max_turns": 40, "recovery_rounds": 3, "auto_tools": False, "auto_accept": False}
LIMITS = {"days": (1, 365), "run_budget": (2, 100000), "cycle_limit": (1, 1000), "attempts_per_cycle": (2, 1000),
          "attempt_minutes": (1, 360), "max_turns": (1, 200), "recovery_rounds": (0, 10)}
RECOVERY_DELAY = 300  # seconds before the first Driver recovery; doubles each round
# Recovery rounds for these kinds raise the per-run limits (x1.5 minutes, +8 steps) within LIMITS.
RAISE_LIMITS = {"time_limit", "max_turns", "no_report"}
PARENT_PAUSE_MESSAGE = "First, restore the parent company"


def legacy_kind(message):
    """Failure kind of a block recorded before blocks were typed."""
    for prefix in ("Run stopped without automatic retry. ", "Three unsuccessful attempts. "):
        message = message.removeprefix(prefix)
    return failure_kind(message) or "unknown"


class Companies:
    def __init__(self, studio, clock=time.time):
        self.studio, self.clock = studio, clock
        self.missions = studio.missions
        self.lock = self.missions.lock
        self.last_error = ""
        with self.missions.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS companies (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS company_events (id INTEGER PRIMARY KEY, company TEXT NOT NULL, at REAL NOT NULL, data TEXT NOT NULL)")
            db.execute("CREATE INDEX IF NOT EXISTS company_event_time ON company_events(company, id)")

    def store(self, db, c, *, bump=True):
        c["updated"] = self.clock()
        if bump:
            c["revision"] += 1
        db.execute("INSERT INTO companies VALUES (?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                   (c["id"], json.dumps(c, ensure_ascii=False)))

    def save(self, c, event=None, *, bump=True):
        with self.missions.connect() as db:
            self.store(db, c, bump=bump)
            if event:
                self.event(db, c, event)

    def event(self, db, c, message):
        db.execute("INSERT INTO company_events(company,at,data) VALUES (?,?,?)",
                   (c["id"], self.clock(), json.dumps(message, ensure_ascii=False)))

    def get(self, key):
        with self.missions.connect() as db:
            row = db.execute("SELECT data FROM companies WHERE id=?", (key,)).fetchone()
        if not row:
            raise ValueError("Company does not exist.")
        return json.loads(row[0])

    def list(self):
        with self.lock, self.missions.connect() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT data FROM companies ORDER BY rowid")]

    def create(self, body):
        with self.lock:
            profiles, default = self.studio.profiles()
            profile, review = body.get("profile", default), body.get("review_profile", default)
            if profile not in profiles or review not in profiles:
                raise ValueError("Select available models.")
            projects = list(dict.fromkeys(strings(body.get("projects"), "company projects", 100)))
            for key in projects:
                self.studio.project(key)
            now = self.clock()
            c = {"id": uuid.uuid4().hex[:16], "revision": 0, "created": now, "updated": now,
                 "name": text(body.get("name"), "company name", 160),
                 "purpose": text(body.get("purpose"), "company goals", 6000),
                 "projects": projects, "profile": profile, "review_profile": review,
                 "status": "paused", "deadline": None, "tasks": [], "cycles": [],
                 "resume_missions": [], "errors": 0, "last_dispatch": 0,
                 "policy": dict(POLICY),
                 "message": "Company created. Add tasks, check limits, and enable Driver."}
            self.save(c, {"action": "created", "message": "Company created; Driver is disabled."})
            return c

    def usage(self, c):
        used, reserved = 0, 0
        for cycle in c["cycles"]:
            m = self.missions.get(cycle["mission"])
            runs = used_runs(m)
            used += runs
            # Finished work and blocks waiting for the owner return their unused runs;
            # every way back to work re-reserves them first (Companies.reserve).
            if not released(m):
                reserved += max(0, m["max_attempts"] - runs)
        return {"used_runs": used, "reserved_runs": reserved,
                "remaining_runs": max(0, c["policy"]["run_budget"] - used - reserved),
                "cycles": len(c["cycles"])}

    def reserve(self, m):
        """Reserve runs again for a released company mission; called under the shared lock.

        Returns a note when the budget only allows a smaller reservation; raises when none is left.
        """
        c = self.get(m["company_context"]["id"])
        runs = used_runs(m)
        need = m["max_attempts"] - runs
        available = self.usage(c)["remaining_runs"]  # this mission is still released in storage
        if need <= 0 or available >= need:
            return ""
        if available < 1:
            raise ValueError("The company run budget is fully used or reserved. Pause the Driver, raise run_budget "
                             "or cancel other work, then continue this execution.")
        m["max_attempts"] = runs + available
        m["company_context"]["max_attempts"] = m["max_attempts"]
        return f"The company budget allowed {available} more runs for this execution."

    def note(self, c, message):
        """Record a company event immediately; visible even if the rest of the tick fails."""
        with self.missions.connect() as db:
            self.event(db, c, message)

    def add_task(self, c, body):
        if len(c["tasks"]) >= 200:
            raise ValueError("Company has a limit of 200 work rules.")
        project = body.get("project")
        if project not in c["projects"]:
            raise ValueError("Project is not assigned to a company.")
        department = body.get("department", "operations")
        if department not in DEPARTMENTS:
            raise ValueError("Unknown department.")
        deps = body.get("depends_on", [])
        if not isinstance(deps, list) or any(not isinstance(d, str) for d in deps):
            raise ValueError("Dependencies must be a list of IDs.")
        known = {t["id"] for t in c["tasks"]}
        if not set(deps) <= known:
            raise ValueError("Dependency must reference an existing company task.")
        # Only earlier tasks can be dependencies: cycles cannot be introduced.
        kind = body.get("kind", "work")
        if kind not in {"work", "external"}:
            raise ValueError("Unknown task type.")
        draft = self.missions.prepare({**body, "profile": c["profile"], "review_profile": c["review_profile"]})
        t = {"id": uuid.uuid4().hex[:12], "project": project, "department": department,
             "title": draft["title"], "goal": draft["goal"], "criteria": draft["criteria"],
             "verification_checks": draft["verification_checks"], "sources": draft["sources"],
             "constraints": draft["constraints"], "kind": kind, "process_mode": draft["process_mode"],
             "priority": number(body.get("priority", 2), 1, 3, "Priority"),
             "depends_on": list(dict.fromkeys(deps)), "enabled": True,
             "interval_hours": number(body.get("interval_hours", 0), 0, 8760, "Interval in hours"),
             "max_cycles": number(body.get("max_cycles", 1), 1, 100, "Number of repetitions"),
             "created": self.clock(), "due": self.clock(), "last_mission": None,
             "status": "needs_owner" if kind == "external" else "queued", "outcome": None}
        c["tasks"].append(t)
        return t

    def pause(self, c, message):
        c.update(status="paused", message=message)
        # Persist authority and recovery intent BEFORE stopping processes.
        for cycle in c["cycles"]:
            m = self.missions.get(cycle["mission"])
            # Blocked, finished and owner-paused work keeps its status and reason; only
            # work the Driver stops here is resumed by it later.
            if m["status"] not in TERMINAL | {"paused", "blocked", "ready", "awaiting_checks", "draft"}:
                if m["id"] not in c["resume_missions"]:
                    c["resume_missions"].append(m["id"])
        self.save(c, {"action": "paused", "message": message})
        for key in c["resume_missions"]:
            m = self.missions.get(key)
            if m["status"] not in TERMINAL | {"paused"}:
                self.missions.action({"id": key, "action": "pause"})

    def action(self, body):
        with self.lock:
            c = self.get(body["id"])
            if body.get("revision") != c["revision"]:
                raise ValueError("The company has changed meanwhile. Refresh the overview and try the action again.")
            action = body.get("action")
            revised_mission = None
            sync_permissions = action == "settings" or (action == "start" and c["status"] == "paused")
            if action == "pause":
                self.pause(c, "Driver paused. Deployed services are not stopped.")
                return c
            renewed = False
            changed_limits = {}
            if action == "start":
                if c["deadline"] and c["deadline"] <= self.clock():
                    raise ValueError("Horizon expired. Renew it in the settings of the paused company.")
                renewed = sync_permissions and not c["deadline"] and bool(c["cycles"])
                c.update(status="active", errors=0, message="Driver enabled.")
                c["deadline"] = c["deadline"] or self.clock() + c["policy"]["days"] * 86400
                if renewed:
                    # Unfinished work continues under the renewed horizon.
                    requeued = self.requeue_expired(c)
                    c["message"] = ("Driver enabled with a renewed horizon; unfinished executions continue"
                                    + (f" and {requeued} expired tasks are queued again." if requeued else "."))
            elif action == "settings":
                if c["status"] != "paused":
                    raise ValueError("Pause the Driver before changing permissions and limits.")
                old = {**POLICY, **c["policy"]}
                policy = {k: number(body.get(k, old[k]), *bounds, k) for k, bounds in LIMITS.items()}
                policy.update(auto_tools=body.get("auto_tools") is True, auto_accept=body.get("auto_accept") is True)
                usage = self.usage(c)
                if policy["run_budget"] < usage["used_runs"] + usage["reserved_runs"] or policy["cycle_limit"] < len(c["cycles"]):
                    raise ValueError("The limit must not be lower than the consumption and reservations of existing executions.")
                if policy["attempts_per_cycle"] > policy["run_budget"]:
                    raise ValueError("The company budget must cover at least one execution.")
                c["policy"] = policy
                # Minutes and steps are not budget units: changed limits reach unfinished executions too.
                changed_limits = {k: policy[k] for k in ("attempt_minutes", "max_turns") if policy[k] != old[k]}
                if body.get("renew_horizon") is True:
                    c["deadline"] = None
                c["message"] = ("Settings saved. Tool approval" + (" and per-run limits" if changed_limits else "") +
                                " also apply to unfinished executions after resuming the Driver.")
            elif action == "reserve_runs":
                if c["status"] != "paused":
                    raise ValueError("Pause the Driver before changing reservations.")
                key = body.get("mission")
                if not any(x["mission"] == key for x in c["cycles"]):
                    raise ValueError("Execution does not belong to this company.")
                revised_mission = self.missions.get(key)
                if revised_mission["status"] in TERMINAL or revised_mission.get("active_attempt"):
                    raise ValueError("Change reservations only for incomplete executions without an active run.")
                maximum = number(body.get("max_attempts"), revised_mission["max_attempts"] + 1, 1000, "Total number of execution runs")
                delta = maximum - revised_mission["max_attempts"]
                # A released reservation must fit completely; it is re-reserved when the work resumes.
                needed = maximum - used_runs(revised_mission) if released(revised_mission) else delta
                if needed > self.usage(c)["remaining_runs"]:
                    raise ValueError("New reservation exceeds the remaining company budget.")
                revised_mission["max_attempts"] = maximum
                revised_mission["company_context"]["max_attempts"] = maximum
                c["message"] = f"Execution {key}: reservation increased by {delta}, total {maximum} runs. The company budget is unchanged."
            elif action == "add_task":
                self.add_task(c, body)
            elif action == "add_project":
                self.studio.project(body["project"])
                if body["project"] not in c["projects"]:
                    if len(c["projects"]) >= 100:
                        raise ValueError("Limit of 100 projects.")
                    c["projects"].append(body["project"])
            elif action in {"disable_task", "enable_task", "resolve_external", "requeue_task"}:
                task = next((t for t in c["tasks"] if t["id"] == body.get("task")), None)
                if not task:
                    raise ValueError("Task does not exist.")
                if action == "requeue_task":
                    last = self.missions.get(task["last_mission"]) if task["last_mission"] else None
                    if task["kind"] != "work" or task["status"] not in {"cancelled", "expired"} or (
                            last and last["status"] not in {"cancelled", "expired"}):
                        raise ValueError("Only a task whose execution was cancelled or expired can be queued again.")
                    if len(c["cycles"]) >= c["policy"]["cycle_limit"]:
                        raise ValueError("The cycle limit is reached. Pause the Driver and raise cycle_limit in the settings first.")
                    if self.usage(c)["remaining_runs"] < c["policy"]["attempts_per_cycle"]:
                        raise ValueError("The remaining company run budget does not cover one execution. "
                                         "Pause the Driver and raise run_budget in the settings first.")
                    task.update(status="queued", due=self.clock(), last_mission=None, enabled=True)
                    c["message"] = f"Task {task['title']} is queued again."
                elif action == "resolve_external":
                    if task["kind"] != "external" or task["status"] != "needs_owner":
                        raise ValueError("This is not an open external action.")
                    outcome = body.get("outcome")
                    if outcome not in {"done", "rejected"}:
                        raise ValueError("Select completed by owner or rejected.")
                    task.update(status=outcome, outcome=text(body.get("note"), "decision documentation", 3000))
                else:
                    if task["last_mission"] and self.missions.get(task["last_mission"])["status"] not in TERMINAL:
                        raise ValueError("First complete or cancel the open execution in AI Projects.")
                    task["enabled"] = action == "enable_task"
            else:
                raise ValueError("Unknown company action.")
            # Persist the parent's policy and its children's permission snapshots
            # together. Pausing has stopped the old workers; resumed workers read
            # the updated mission. Budgets and completed history stay unchanged.
            with self.missions.connect() as db:
                if revised_mission:
                    self.missions.save(revised_mission, db)
                if sync_permissions:
                    for cycle in c["cycles"]:
                        row = db.execute("SELECT data FROM missions WHERE id=?", (cycle["mission"],)).fetchone()
                        m = json.loads(row[0])
                        if m["status"] in TERMINAL or m.get("company_context", {}).get("id") != c["id"]:
                            continue
                        m["auto_approve"] = c["policy"]["auto_tools"]
                        m["company_context"]["auto_tools"] = c["policy"]["auto_tools"]
                        m.update(changed_limits)
                        m["company_context"].update(changed_limits)
                        if renewed:
                            horizon = min(c["deadline"], self.clock() + m["days"] * 86400)
                            if not m["deadline"] or m["deadline"] < horizon:
                                m["deadline"] = horizon
                        self.missions.save(m, db)
                self.store(db, c)
                self.event(db, c, {"action": action, "task": body.get("task"), "message": c["message"]})
            return c

    def reconcile(self, c):
        for t in c["tasks"]:
            if not t["last_mission"]:
                continue
            m = self.missions.get(t["last_mission"])
            cycle = next(x for x in c["cycles"] if x["mission"] == m["id"])
            if cycle["status"] != m["status"]:
                cycle["status"] = m["status"]
                if m["status"] == "accepted":
                    t["due"] = self.clock() + t["interval_hours"] * 3600
            count = sum(x["task"] == t["id"] for x in c["cycles"])
            t["status"] = ("scheduled" if t["interval_hours"] and count < t["max_cycles"] else "done") if m["status"] == "accepted" else m["status"]

    def dispatch(self, c, t):
        usage = self.usage(c)
        allowance = c["policy"]["attempts_per_cycle"]
        if usage["remaining_runs"] < allowance or len(c["cycles"]) >= c["policy"]["cycle_limit"]:
            return False
        now = self.clock()
        m = self.missions.prepare({"project": t["project"], "title": t["title"], "goal": t["goal"],
            "criteria": t["criteria"], "verification_checks": t["verification_checks"], "sources": t["sources"],
            "process_mode": t.get("process_mode", "auto"),
            "constraints": t["constraints"] + "\nCompany task. Prepare external communications, payments, and production changes only as background material; do not execute them.",
            "profile": c["profile"], "review_profile": c["review_profile"], "auto_approve": c["policy"]["auto_tools"],
            "max_attempts": allowance, "attempt_minutes": c["policy"]["attempt_minutes"],
            "max_turns": c["policy"]["max_turns"], "days": min(30, c["policy"]["days"]), "isolated": True})
        m["company_context"] = {"id": c["id"], "name": c["name"], "purpose": c["purpose"],
                                "department": DEPARTMENTS[t["department"]], "task": t["id"],
                                "max_attempts": allowance, "auto_tools": c["policy"]["auto_tools"],
                                "attempt_minutes": m["attempt_minutes"], "max_turns": m["max_turns"]}
        m.update(status="running", deadline=min(c["deadline"], now + m["days"] * 86400))
        t.update(last_mission=m["id"], status="running")
        c["cycles"].append({"task": t["id"], "mission": m["id"], "status": "running", "created": now})
        c.update(last_dispatch=now, message="In progress: " + t["title"])
        # Mission and reservation are inseparable across crash/retry.
        with self.missions.connect() as db:
            self.missions.save(m, db)
            self.store(db, c, bump=False)
            self.event(db, c, {"action": "dispatch", "task": t["id"], "mission": m["id"], "reserved_runs": allowance})
        return True

    def requeue_expired(self, c):
        """After a horizon renewal, queue tasks whose execution expired with the old horizon."""
        count = 0
        for t in c["tasks"]:
            if t["kind"] == "work" and t["status"] == "expired" and t["last_mission"]:
                if self.missions.get(t["last_mission"])["status"] == "expired":
                    t.update(status="queued", due=self.clock(), last_mission=None)
                    count += 1
        return count

    def run_active(self, m):
        """True until the mission controller has recorded the end of the stopped worker."""
        return bool(m.get("active_attempt"))

    def resume(self, c, m, message):
        """Resume a mission the Driver stopped or the owner asked to continue; never raises.

        driver_resume records an owner decision (answer, task or brief revision) deferred until the Driver
        runs, so it is applied as the owner's: fresh correction rounds and a fresh set of Driver recoveries.
        """
        try:
            note = self.missions.resume(m, by="owner" if m.get("driver_resume") else "driver")
        except ValueError as exc:
            m = self.missions.get(m["id"])
            kind = ("run_limit" if used_runs(m) >= m["max_attempts"] else
                    "deadline" if m["deadline"] and self.clock() >= m["deadline"] else "resume_failed")
            reason = str(exc)
            m.pop("driver_resume", None)
            m.pop("paused_by_parent", None)
            if m["status"] == "paused":
                m.pop("resume_status", None)
            self.missions.block(m, kind, "The Driver could not resume this execution: " + reason)
            self.missions.save(m)
            self.note(c, {"action": "resume_failed", "mission": m["id"], "task": m["company_context"].get("task"),
                          "message": f"Execution {m['id']} was not resumed: {reason}"})
            return False
        m["message"] = message + (" " + note if note else "")
        self.missions.save(m)
        return True

    def ask_owner(self, c, m, problem, question=True):
        """Raise ONE owner question for a block the Driver will not (or can no longer) recover."""
        m["owner_needed"] = True  # releases the unused reservation until the owner continues it
        if question and not any(q["answer"] is None for q in m["questions"]):
            kind = m.get("blocked_kind") or "unknown"
            revise = " Revise the task brief first if the corrections keep failing." if kind in ROUND_KINDS else ""
            self.missions.questions(m, [{
                "question": (f"Execution '{m['title']}' is blocked and {problem}. How should the company continue? "
                             "Options: (1) answer 'continue' to resume it with the current limits; "
                             "(2) pause the Driver, raise attempt_minutes or max_turns in the company settings or add runs "
                             "with reserve_runs, start the Driver and answer 'continue'; (3) pause the Driver and revise "
                             "the task brief; (4) cancel the execution and requeue the task later. Any answer other than "
                             "'continue' is saved and keeps the execution blocked." + revise),
                "reason": f"Block ({kind}): {m['message']}"[:3000]}])
            m["questions"][-1]["kind"] = "recovery"
        self.missions.save(m)
        self.note(c, {"action": "owner_needed", "mission": m["id"], "task": m["company_context"].get("task"),
                      "message": f"Execution {m['id']} needs the owner: {problem}."})

    def recover(self, c, m):
        """Bounded, visible automatic recovery of a blocked company mission (K7)."""
        if m.get("owner_needed"):
            return
        now = self.clock()
        kind = m.get("blocked_kind") or legacy_kind(m["message"])
        limit = c["policy"].get("recovery_rounds", POLICY["recovery_rounds"])
        rounds = m.get("driver_recoveries", 0)
        open_questions = [q for q in m["questions"] if q["answer"] is None]
        if open_questions and not all(recovery_question(q) for q in open_questions):
            self.ask_owner(c, m, "it waits for answers to its open questions", question=False)
            return
        problem = ("this block needs an owner decision" if kind not in RECOVERABLE else
                   f"automatic recovery already used {rounds}/{limit} rounds" if rounds >= limit else
                   "its time limit has expired" if m["deadline"] and now >= m["deadline"] else
                   "its reserved runs are used up" if used_runs(m) >= m["max_attempts"] else "")
        if problem:
            self.ask_owner(c, m, problem)
            return
        if now < m.get("blocked_at", 0) + RECOVERY_DELAY * 2 ** rounds:
            return
        reason = m["message"][:600]
        try:
            note = self.missions.resume(m, by="driver")
        except ValueError as exc:
            m = self.missions.get(m["id"])
            self.ask_owner(c, m, "automatic recovery failed: " + str(exc))
            return
        rounds += 1
        m["driver_recoveries"] = rounds
        raised = ""
        if kind in RAISE_LIMITS:
            minutes = min(LIMITS["attempt_minutes"][1], math.ceil(m["attempt_minutes"] * 1.5))
            turns = min(LIMITS["max_turns"][1], m["max_turns"] + 8)
            m.update(attempt_minutes=minutes, max_turns=turns)
            m["company_context"].update(attempt_minutes=minutes, max_turns=turns)
            raised = f" Per-run limits raised to {minutes} min and {turns} steps."
        summary = f"Driver recovery {rounds}/{limit}: {reason}"
        raised += (" " + note) if note else ""
        m["message"] = (summary + raised)[:2000]
        self.missions.save(m)
        self.note(c, {"action": "recovery", "mission": m["id"], "task": m["company_context"].get("task"),
                      "message": (summary + raised)[:2000]})

    def supervise(self, c, m):
        """One child's automatic step. Errors stay with that child and never stop the Driver."""
        if m["status"] == "awaiting_plan" and not any(q["answer"] is None for q in m["questions"]):
            self.missions.action({"id": m["id"], "action": "approve_plan"})
        elif m["status"] == "ready" and c["policy"]["auto_accept"]:
            run = (m.get("final_report") or {}).get("run")
            if (m.get("auto_accept_error") or {}).get("run", False) == run:
                return  # already failed for this final report; the owner decides
            try:
                self.missions.action({"id": m["id"], "action": "accept"})
            except ValueError as exc:
                m = self.missions.get(m["id"])
                m["auto_accept_error"] = {"run": run, "at": self.clock(), "message": str(exc)[:500]}
                m["message"] = "Automatic acceptance failed; the result waits for the owner: " + str(exc)[:1500]
                self.missions.save(m)
                self.note(c, {"action": "accept_failed", "mission": m["id"], "message": m["message"]})
        elif m["status"] in {"paused", "blocked"} and (m.get("driver_resume") or m.get("paused_by_parent")):
            if not self.run_active(m):
                self.resume(c, m, "The owner's decision is applied; continuing." if m.get("driver_resume") else
                            "The Driver is active again; continuing.")
        elif (m["status"] == "paused" and m.get("resume_status") == "blocked"
                and m["message"].startswith(PARENT_PAUSE_MESSAGE)):
            # Older versions turned blocked work into 'paused' on a Driver pause; restore the block.
            error = next((a["error"] for a in reversed(m["attempts"]) if a.get("error")), "")
            m.update(status="blocked", blocked_kind=legacy_kind(error), blocked_at=self.clock(),
                     message=("Blocked before a Driver pause. Last run error: " + error)[:2000] if error else
                     "Blocked before a Driver pause; the original reason is in the execution history.")
            m.pop("resume_status", None)
            self.missions.save(m)
        elif m["status"] == "blocked":
            self.recover(c, m)

    def advance(self, c):
        self.reconcile(c)
        if c["status"] != "active":
            return
        if self.clock() >= c["deadline"]:
            self.pause(c, "Company time horizon expired. Renew the horizon to continue.")
            return
        for key in c["resume_missions"][:]:
            m = self.missions.get(key)
            if m["status"] == "paused" and not self.run_active(m):
                self.resume(c, m, "The Driver is active again; continuing.")
                c["resume_missions"].remove(key)
            elif m["status"] != "paused":
                c["resume_missions"].remove(key)
        for cycle in c["cycles"]:
            m = self.missions.get(cycle["mission"])
            if m["status"] in TERMINAL or m["id"] in c["resume_missions"]:
                continue
            try:
                self.supervise(c, m)
            except Exception as exc:
                # Visible once per distinct error; the other executions continue.
                message = f"Execution {m['id']}: {str(exc)[:500]}"
                if c.setdefault("child_errors", {}).get(m["id"]) != message:
                    c["child_errors"][m["id"]] = message
                    self.note(c, {"action": "child_error", "mission": m["id"], "message": message})
        self.reconcile(c)
        # Don't queue competing modifications in the same original workspace. A draft or
        # never-started paused/blocked mission holds no base snapshot, so it does not block.
        busy = {}
        for m in self.missions.list():
            if m["status"] in TERMINAL or m["status"] == "draft":
                continue
            if m["status"] in {"blocked", "paused", "waiting"} and not m.get("base_version") and not m["attempts"]:
                continue
            busy.setdefault(m["project"], m)
        done = {t["id"] for t in c["tasks"] if t["status"] in {"done", "scheduled"}}
        waiting = sorted((t for t in c["tasks"] if t["kind"] == "work" and t["enabled"]
                          and t["status"] in {"queued", "scheduled"} and t["due"] <= self.clock()
                          and set(t["depends_on"]) <= done),
                         key=lambda t: (t["priority"], t["due"], t["created"], t["id"]))
        ready = [t for t in waiting if t["project"] not in busy]
        if ready:
            if not self.dispatch(c, ready[0]):
                usage = self.usage(c)
                c["message"] = (f"Run budget or cycle limit reached ({usage['used_runs']} used, {usage['reserved_runs']} reserved "
                                f"of {c['policy']['run_budget']} runs; {usage['cycles']}/{c['policy']['cycle_limit']} cycles). "
                                "Open work may finish; raise the limits in the settings to start more.")
            return
        if waiting:
            blocker = busy[waiting[0]["project"]]
            name = self.studio.projects.get(blocker["project"], {}).get("name", blocker["project"])
            c["message"] = (f"Waiting: project {name} is used by execution {blocker['id']} ({blocker['status']}: "
                            f"{blocker['title']}). Task {waiting[0]['title']} starts when it is finished or cancelled.")
            if c.get("busy_notice") != blocker["id"]:
                c["busy_notice"] = blocker["id"]
                self.note(c, {"action": "project_busy", "task": waiting[0]["id"], "mission": blocker["id"],
                              "message": c["message"]})
            return
        owner = [self.missions.get(x["mission"]) for x in c["cycles"]]
        owner = [m["title"] for m in owner if m["status"] == "waiting" or m["status"] == "blocked" and m.get("owner_needed")]
        c["message"] = ("Owner decision needed: " + "; ".join(owner[:5]) + ("…" if len(owner) > 5 else "")
                        if owner else "Driver monitors work and waits for deadline, completion, or decision.")

    def tick(self):
        errors = []
        with self.lock:
            for c in sorted(self.list(), key=lambda x: x["last_dispatch"]):
                before = json.dumps(c, sort_keys=True)
                try:
                    self.advance(c)
                    c["errors"] = 0
                except Exception as exc:
                    # Reload after a failed transaction; never persist a phantom reservation.
                    c = self.get(c["id"])
                    c["errors"] += 1
                    c["message"] = "Driver requires attention: " + str(exc)[:500]
                    errors.append(c["message"])
                    if c["errors"] >= 3:
                        self.pause(c, c["message"] + " Three errors: Driver paused.")
                if json.dumps(c, sort_keys=True) != before:
                    self.save(c, bump=False)
        self.last_error = "\n".join(errors)[:2000]

    def snapshot(self):
        with self.lock:
            items = []
            for c in self.list():
                # Read-only projection; polling cannot affect scheduling or revisions.
                self.reconcile(c)
                missions = [self.missions.get(x["mission"]) for x in c["cycles"]]
                with self.missions.connect() as db:
                    events = [{"at": r[0], **json.loads(r[1])} for r in db.execute(
                        "SELECT at,data FROM company_events WHERE company=? ORDER BY id DESC LIMIT 100", (c["id"],))]
                items.append({**c, "usage": self.usage(c), "events": events,
                    "inbox": [{"mission": m["id"], "project": m["project"], "title": m["title"],
                               "status": m["status"], "message": m["message"],
                               "blocked_kind": m.get("blocked_kind"), "owner_needed": bool(m.get("owner_needed")),
                               "driver_recoveries": m.get("driver_recoveries", 0),
                               "questions": [q for q in m["questions"] if q["answer"] is None]}
                              for m in missions if m["status"] in {"waiting", "blocked", "paused", "ready", "awaiting_checks"}
                              or m["status"] == "awaiting_plan" and any(q["answer"] is None for q in m["questions"])],
                    "products": [{"id": p["id"], "title": p["title"], "project": p["project"],
                                  "status": p["status"], "releases": len(p["releases"])}
                                 for p in self.studio.products.list() if p["project"] in c["projects"]]})
            return {"companies": items, "departments": DEPARTMENTS, "controller_error": self.last_error,
                    "capabilities": {"remote_production": False, "crm": False, "payments": False,
                                     "email_send": False, "os_sandbox": False, "worker_slots": 1}}

    def report(self, key):
        c = next((c for c in self.snapshot()["companies"] if c["id"] == key), None)
        if not c:
            raise ValueError("Company does not exist.")
        lines = [f"# {c['name']}", "", c["purpose"], "", f"Driver: {c['status']}",
                 f"Runs: {c['usage']['used_runs']} used, {c['usage']['reserved_runs']} reserved / {c['policy']['run_budget']}",
                 "", "## Portfolio"]
        for key in c["projects"]:
            lines.append("- " + self.studio.projects[key]["name"])
        lines.extend(["", "## Work and Evidence"])
        for t in c["tasks"]:
            lines.append(f"- {t['title']}: {t['status']} · execution {t['last_mission'] or "none"}")
            if t["last_mission"]:
                mission = self.missions.get(t["last_mission"])
                final = mission.get("final_report") or {}
                for artifact in final.get("verified_artifacts", []):
                    lines.append(f"  - Artifact: {artifact['path']} · SHA-256 {artifact['sha256']}")
                if mission.get("verification_id"):
                    lines.append("  - Independent check: " + mission["verification_id"])
                if mission.get("acceptance"):
                    lines.append("  - Acceptance: " + mission["acceptance"]["kind"])
            if t["kind"] == "external":
                lines.append("  - External step: " + (t["outcome"] or "Waiting for owner; not performed by Driver."))
        lines.extend(["", "## Owner Decision"])
        for item in c["inbox"]:
            lines.append(f"- {item['title']}: {item['status']} — {item['message']}")
            lines.extend("  - " + q["question"] for q in item["questions"])
        lines.extend(["", "External systems are not connected. Run limits are not monetary caps.",
                      "Departments are work contexts, not isolated accounts. Native tools have user permissions."])
        return {"text": "\n".join(lines) + "\n"}
