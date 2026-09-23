"Portfolia firmy a vázaný trvalý řadič sdílející transakci/uzamčení mise.\n\nOddělení je kontext, nikoli bezpečnostní hranice.Externí akce jsou doručenou schránkou, nikoli spustitelnými úkoly. Vlastní nástroje zachovávají oprávnění hostitelského uživatele.\n"
from __future__ import annotations

import json
import time
import uuid

try:
    from .missions import TERMINAL, number, strings, text
except ImportError:
    from missions import TERMINAL, number, strings, text

DEPARTMENTS = {"operations": "Vedení a provoz", "delivery": "Vývoj a dodávka",
               "growth": "Obchod a marketing", "finance": "Finance a administrativa",
               "platform": "Infrastruktura a data"}


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
            raise ValueError("Firma neexistuje.")
        return json.loads(row[0])

    def list(self):
        with self.lock, self.missions.connect() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT data FROM companies ORDER BY rowid")]

    def create(self, body):
        with self.lock:
            profiles, default = self.studio.profiles()
            profile, review = body.get("profile", default), body.get("review_profile", default)
            if profile not in profiles or review not in profiles:
                raise ValueError("Vyber dostupné modely.")
            projects = list(dict.fromkeys(strings(body.get("projects"), "projekty firmy", 100)))
            for key in projects:
                self.studio.project(key)
            now = self.clock()
            c = {"id": uuid.uuid4().hex[:16], "revision": 0, "created": now, "updated": now,
                 "name": text(body.get("name"), "název firmy", 160),
                 "purpose": text(body.get("purpose"), "cíle firmy", 6000),
                 "projects": projects, "profile": profile, "review_profile": review,
                 "status": "paused", "deadline": None, "tasks": [], "cycles": [],
                 "resume_missions": [], "errors": 0, "last_dispatch": 0,
                 "policy": {"days": 7, "run_budget": 100, "cycle_limit": 20,
                            "attempts_per_cycle": 10, "attempt_minutes": 20, "max_turns": 40,
                            "auto_tools": False, "auto_accept": False},
                 "message": "Firma založena. Přidej úkoly, zkontroluj limity a zapni Řadič."}
            self.save(c, {"action": "created", "message": "Firma založena; Řadič je vypnutý."})
            return c

    def usage(self, c):
        used, reserved = 0, 0
        for cycle in c["cycles"]:
            m = self.missions.get(cycle["mission"])
            used += len(m["attempts"])
            if m["status"] not in TERMINAL:
                reserved += max(0, m["max_attempts"] - len(m["attempts"]))
        return {"used_runs": used, "reserved_runs": reserved,
                "remaining_runs": max(0, c["policy"]["run_budget"] - used - reserved),
                "cycles": len(c["cycles"])}

    def add_task(self, c, body):
        if len(c["tasks"]) >= 200:
            raise ValueError("Firma má limit 200 pracovních pravidel.")
        project = body.get("project")
        if project not in c["projects"]:
            raise ValueError("Projekt není přiřazený firmě.")
        department = body.get("department", "operations")
        if department not in DEPARTMENTS:
            raise ValueError("Neznámé oddělení.")
        deps = body.get("depends_on", [])
        if not isinstance(deps, list) or any(not isinstance(d, str) for d in deps):
            raise ValueError("Závislosti musí být seznam ID.")
        known = {t["id"] for t in c["tasks"]}
        if not set(deps) <= known:
            raise ValueError("Závislost musí odkazovat na existující firemní úkol.")
        # Only earlier tasks can be dependencies: cycles cannot be introduced.
        kind = body.get("kind", "work")
        if kind not in {"work", "external"}:
            raise ValueError("Neznámý druh úkolu.")
        draft = self.missions.prepare({**body, "profile": c["profile"], "review_profile": c["review_profile"]})
        t = {"id": uuid.uuid4().hex[:12], "project": project, "department": department,
             "title": draft["title"], "goal": draft["goal"], "criteria": draft["criteria"],
             "verification_checks": draft["verification_checks"], "sources": draft["sources"],
             "constraints": draft["constraints"], "kind": kind,
             "priority": number(body.get("priority", 2), 1, 3, "Priorita"),
             "depends_on": list(dict.fromkeys(deps)), "enabled": True,
             "interval_hours": number(body.get("interval_hours", 0), 0, 8760, "Interval v hodinách"),
             "max_cycles": number(body.get("max_cycles", 1), 1, 100, "Počet opakování"),
             "created": self.clock(), "due": self.clock(), "last_mission": None,
             "status": "needs_owner" if kind == "external" else "queued", "outcome": None}
        c["tasks"].append(t)
        return t

    def pause(self, c, message):
        c.update(status="paused", message=message)
        # Persist authority and recovery intent BEFORE stopping processes.
        for cycle in c["cycles"]:
            m = self.missions.get(cycle["mission"])
            if m["status"] not in TERMINAL | {"paused", "blocked", "ready", "awaiting_checks"}:
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
                raise ValueError("Firma se mezitím změnila. Obnov přehled a zkus akci znovu.")
            action = body.get("action")
            revised_mission = None
            sync_permissions = action == "settings" or (action == "start" and c["status"] == "paused")
            if action == "pause":
                self.pause(c, "Řadič pozastaven. Nasazené služby tím nejsou zastaveny.")
                return c
            if action == "start":
                if c["deadline"] and c["deadline"] <= self.clock():
                    raise ValueError("Vypršel horizont. Obnov jej v nastavení pozastavené firmy.")
                c.update(status="active", errors=0, message="Řadič zapnutý.")
                c["deadline"] = c["deadline"] or self.clock() + c["policy"]["days"] * 86400
            elif action == "settings":
                if c["status"] != "paused":
                    raise ValueError("Před změnou oprávnění a limitů pozastav Řadič.")
                limits = {"days": (1, 365), "run_budget": (2, 100000), "cycle_limit": (1, 1000),
                          "attempts_per_cycle": (2, 1000), "attempt_minutes": (1, 360), "max_turns": (1, 200)}
                policy = {k: number(body.get(k, c["policy"][k]), *bounds, k) for k, bounds in limits.items()}
                policy.update(auto_tools=body.get("auto_tools") is True, auto_accept=body.get("auto_accept") is True)
                usage = self.usage(c)
                if policy["run_budget"] < usage["used_runs"] + usage["reserved_runs"] or policy["cycle_limit"] < len(c["cycles"]):
                    raise ValueError("Limit nesmí být nižší než spotřeba a rezervace existujících realizací.")
                if policy["attempts_per_cycle"] > policy["run_budget"]:
                    raise ValueError("Rozpočet firmy musí pokrýt alespoň jednu realizaci.")
                c["policy"] = policy
                if body.get("renew_horizon") is True:
                    c["deadline"] = None
                c["message"] = "Nastavení uloženo. Schvalování nástrojů platí i pro rozpracované realizace po pokračování řadiče."
            elif action == "reserve_runs":
                if c["status"] != "paused":
                    raise ValueError("Před změnou rezervace pozastav Řadič.")
                key = body.get("mission")
                if not any(x["mission"] == key for x in c["cycles"]):
                    raise ValueError("Realizace nepatří této firmě.")
                revised_mission = self.missions.get(key)
                if revised_mission["status"] in TERMINAL or revised_mission.get("active_attempt"):
                    raise ValueError("Rezervaci měň jen u nedokončené realizace bez aktivního běhu.")
                maximum = number(body.get("max_attempts"), revised_mission["max_attempts"] + 1, 1000, "Celkový počet běhů realizace")
                delta = maximum - revised_mission["max_attempts"]
                if delta > self.usage(c)["remaining_runs"]:
                    raise ValueError("Nová rezervace překračuje zbývající rozpočet firmy.")
                revised_mission["max_attempts"] = maximum
                revised_mission["company_context"]["max_attempts"] = maximum
                c["message"] = f"Spuštění {key}: rezervace zvýšena o {delta}, celkem {maximum} běhů. Rozpočet firmy se nemění."
            elif action == "add_task":
                self.add_task(c, body)
            elif action == "add_project":
                self.studio.project(body["project"])
                if body["project"] not in c["projects"]:
                    if len(c["projects"]) >= 100:
                        raise ValueError("Limit 100 projektů.")
                    c["projects"].append(body["project"])
            elif action in {"disable_task", "enable_task", "resolve_external"}:
                task = next((t for t in c["tasks"] if t["id"] == body.get("task")), None)
                if not task:
                    raise ValueError("Úkol neexistuje.")
                if action == "resolve_external":
                    if task["kind"] != "external" or task["status"] != "needs_owner":
                        raise ValueError("Toto není otevřená externí akce.")
                    outcome = body.get("outcome")
                    if outcome not in {"done", "rejected"}:
                        raise ValueError("Vyber provedeno vlastníkem nebo zamítnuto.")
                    task.update(status=outcome, outcome=text(body.get("note"), "doklad rozhodnutí", 3000))
                else:
                    if task["last_mission"] and self.missions.get(task["last_mission"])["status"] not in TERMINAL:
                        raise ValueError("Nejdřív dokonči nebo zruš otevřenou realizaci v Projektech AI.")
                    task["enabled"] = action == "enable_task"
            else:
                raise ValueError("Neznámá akce firmy.")
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
            "constraints": t["constraints"] + "\nFiremní úkol. Externí komunikaci, platby a produkční změny pouze připrav jako podklady; neprováděj je.",
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
        c.update(last_dispatch=now, message="Probíhá: " + t["title"])
        # Mission and reservation are inseparable across crash/retry.
        with self.missions.connect() as db:
            self.missions.save(m, db)
            self.store(db, c, bump=False)
            self.event(db, c, {"action": "dispatch", "task": t["id"], "mission": m["id"], "reserved_runs": allowance})
        return True

    def advance(self, c):
        self.reconcile(c)
        if c["status"] != "active":
            return
        if self.clock() >= c["deadline"]:
            self.pause(c, "Vypršel časový horizont firmy. Pro pokračování obnov horizont.")
            return
        for key in c["resume_missions"][:]:
            m = self.missions.get(key)
            if m["status"] == "paused" and (not m["active_attempt"] or self.studio.runs.get(m["active_attempt"], {}).get("status") not in {"running", "waiting", "stopping"}):
                self.missions.action({"id": key, "action": "resume"})
                c["resume_missions"].remove(key)
            elif m["status"] != "paused":
                c["resume_missions"].remove(key)
        for cycle in c["cycles"]:
            m = self.missions.get(cycle["mission"])
            if m["status"] == "awaiting_plan" and not any(q["answer"] is None for q in m["questions"]):
                self.missions.action({"id": m["id"], "action": "approve_plan"})
            elif m["status"] == "ready" and c["policy"]["auto_accept"]:
                self.missions.action({"id": m["id"], "action": "accept"})
        self.reconcile(c)
        # Don't queue competing modifications in the same original workspace.
        busy = {m["project"] for m in self.missions.list() if m["status"] not in TERMINAL}
        done = {t["id"] for t in c["tasks"] if t["status"] in {"done", "scheduled"}}
        ready = sorted((t for t in c["tasks"] if t["kind"] == "work" and t["enabled"]
                        and t["status"] in {"queued", "scheduled"} and t["due"] <= self.clock()
                        and set(t["depends_on"]) <= done and t["project"] not in busy),
                       key=lambda t: (t["priority"], t["due"], t["created"], t["id"]))
        if ready:
            if not self.dispatch(c, ready[0]):
                c["message"] = "Limit realizací nebo rezervací běhů vyčerpán. Otevřená práce může doběhnout."
        else:
            c["message"] = "Řadič sleduje práci a čeká na termín, dokončení nebo rozhodnutí."

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
                    c["message"] = "Řadič vyžaduje pozornost: " + str(exc)[:500]
                    errors.append(c["message"])
                    if c["errors"] >= 3:
                        self.pause(c, c["message"] + " Tři chyby: Řadič pozastaven.")
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
            raise ValueError("Firma neexistuje.")
        lines = [f"# {c['name']}", "", c["purpose"], "", f"Řadič: {c['status']}",
                 f"Běhy: {c['usage']['used_runs']} použito, {c['usage']['reserved_runs']} rezervováno / {c['policy']['run_budget']}",
                 "", "## Portfolio"]
        for key in c["projects"]:
            lines.append("- " + self.studio.projects[key]["name"])
        lines.extend(["", "## Práce a důkazy"])
        for t in c["tasks"]:
            lines.append(f"- {t['title']}: {t['status']} · provedení {t['last_mission'] or "none"}")
            if t["last_mission"]:
                mission = self.missions.get(t["last_mission"])
                final = mission.get("final_report") or {}
                for artifact in final.get("verified_artifacts", []):
                    lines.append(f"  - Artefakt: {artifact['path']} · SHA-256 {artifact['sha256']}")
                if mission.get("verification_id"):
                    lines.append("  - Nezávislá kontrola: " + mission["verification_id"])
                if mission.get("acceptance"):
                    lines.append("  - Převzetí: " + mission["acceptance"]["kind"])
            if t["kind"] == "external":
                lines.append("  - Externí krok: " + (t["outcome"] or "Čeká na vlastníka; nebyl proveden řadičem."))
        lines.extend(["", "## Rozhodnutí vlastníka"])
        for item in c["inbox"]:
            lines.append(f"- {item['title']}: {item['status']} — {item['message']}")
            lines.extend("  - " + q["question"] for q in item["questions"])
        lines.extend(["", "Externí systémy nejsou připojené. Limity běhů nejsou peněžní strop.",
                      "Oddělení jsou pracovní kontext, ne izolované účty. Nativní nástroje mají oprávnění uživatele."])
        return {"text": "\n".join(lines) + "\n"}
