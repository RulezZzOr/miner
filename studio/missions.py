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
        raise ValueError(f"Vyplň {name} (nejvýše {limit} znaků).")
    return value.strip()


def number(value, low, high, name):
    if isinstance(value, bool):
        raise ValueError(f"Neplatný limit: {name}.")
    value = int(value)
    if not low <= value <= high:
        raise ValueError(f"{name}: rozsah {low}–{high}.")
    return value


def strings(value, name, maximum=50):
    if not isinstance(value, list) or not 1 <= len(value) <= maximum:
        raise ValueError(f"{name}: očekáván neprázdný seznam, nejvýše {maximum} položek.")
    return [text(v, name, 3000) for v in value]


def parse_plan(report):
    tasks = report.get("tasks")
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= 40:
        raise ValueError("Plán musí mít 1–40 úkolů.")
    result = []
    ids = set()
    for item in tasks:
        key = text(item.get("id"), "ID úkolu", 60)
        if not IDENTIFIER.fullmatch(key) or key in ids:
            raise ValueError("ID úkolů musí být jedinečná, bez mezer a lomítek.")
        ids.add(key)
        deps = item.get("depends_on", [])
        if not isinstance(deps, list) or any(not isinstance(d, str) for d in deps):
            raise ValueError("Závislosti musí být seznam ID.")
        result.append({"id": key, "title": text(item.get("title"), "název", 200),
                       "instructions": text(item.get("instructions"), "instrukce"),
                       "criteria": strings(item.get("criteria"), "kritéria", 20),
                       "depends_on": list(dict.fromkeys(deps)), "status": "pending",
                       "cycles": 0, "artifacts": [], "feedback": ""})
    done = set()
    while len(done) < len(ids):
        ready = {t["id"] for t in result if set(t["depends_on"]) <= done} - done
        if not ready:
            raise ValueError("Plán obsahuje cyklus nebo neexistující závislost.")
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
            raise ValueError("Dlouhodobý projekt neexistuje.")
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
            raise ValueError("Vyber dostupný model pro práci i review.")
        decision_profile = body.get("decision_profile") or None
        if decision_profile and (decision_profile not in profiles or profiles[decision_profile].get("protocol") != "chat_completions"
                                 or profiles[decision_profile].get("oauth_provider")):
            raise ValueError("Rozhodovací model musí být profil kompatibilního chat API bez OAuth.")
        now = self.clock()
        m = {"id": uuid.uuid4().hex[:16], "project": body["project"],
             "title": text(body.get("title"), "název projektu", 160),
             "goal": text(body.get("goal"), "cílový produkt"),
             "criteria": strings(body.get("criteria"), "podmínky hotového produktu", 40),
             "constraints": str(body.get("constraints", ""))[:12000],
             "sources": str(body.get("sources", ""))[:12000],
             "profile": profile, "review_profile": reviewer,
             "decision_profile": decision_profile,
             "auto_approve": body.get("auto_approve") is True,
             "verification_checks": validate_checks(body.get("verification_checks", [])),
             "verification_id": None,
             "isolated": body.get("isolated", True) is not False,
             "days": number(body.get("days", 7), 1, 30, "Délka projektu ve dnech"),
             "max_attempts": number(body.get("max_attempts", 100), 2, 1000, "Počet běhů"),
             "attempt_minutes": number(body.get("attempt_minutes", 30), 1, 360, "Minuty na běh"),
             "max_turns": number(body.get("max_turns", 40), 1, 200, "Kroky na běh"),
             "status": "draft", "phase": "plan", "created": now, "updated": now,
             "deadline": None, "attempts": [], "tasks": [], "questions": [],
             "active_attempt": None, "message": "Zkontroluj zadání a spusť přípravu.",
             "retry_at": 0, "failures": 0, "final_report": None,
             "final_cycles": 0, "evidence": [],
             "workspace": str(root)}
        return m

    def questions(self, m, values, task=None):
        if not isinstance(values, list) or not 1 <= len(values) <= 20:
            raise ValueError("Blokace musí obsahovat 1–20 otázek.")
        for item in values:
            m["questions"].append({"id": uuid.uuid4().hex[:12],
                "question": text(item.get("question"), "otázku", 3000),
                "reason": text(item.get("reason"), "důvod otázky", 3000),
                "task": task, "answer": None, "created": self.clock()})

    def action(self, body):
        with self.lock:
            m = self.get(body["id"])
            action = body["action"]
            company = m.get("company_context")
            if company and action in {"accept", "manual_accept"}:
                self.require_active_product(m)
            if company and action == "runtime_settings":
                raise ValueError("Firemní realizace má rezervované limity a modely. Pro změnu založ nový úkol ve firmě.")
            if action in {"start", "resume", "approve_plan", "recheck"}:
                self.require_active_product(m)
            if action == "runtime_settings" and m["status"] in {"draft", "paused", "blocked"}:
                if m.get("active_attempt"):
                    raise ValueError("Počkej na ukončení rozpracovaného běhu.")
                profiles, _ = self.studio.profiles()
                profile = body.get("profile", m["profile"])
                reviewer = body.get("review_profile", m["review_profile"])
                if profile not in profiles or reviewer not in profiles:
                    raise ValueError("Vyber dostupný model pro práci i review.")
                minutes = number(body.get("attempt_minutes", m["attempt_minutes"]), 1, 360, "Minuty na běh")
                turns = number(body.get("max_turns", m["max_turns"]), 1, 200, "Kroky na běh")
                attempts = number(body.get("max_attempts", m["max_attempts"]), max(2, len(m["attempts"]) + 1), 1000, "Počet běhů")
                m.update(profile=profile, review_profile=reviewer, attempt_minutes=minutes,
                         max_turns=turns, max_attempts=attempts,
                         message="Modely a limity uloženy. Pokračování spusť samostatně; celkový termín se nemění.")
            elif action == "revise_task" and m["status"] in {"paused", "blocked"}:
                if m.get("active_attempt"):
                    raise ValueError("Počkej na ukončení rozpracovaného běhu.")
                if company and self.studio.companies.get(company["id"])["status"] != "paused":
                    raise ValueError("Před úpravou zadání pozastav nadřazený Driver.")
                task = next((t for t in m["tasks"] if t["id"] == body.get("task")), None)
                if not task or task["status"] not in {"pending", "waiting"}:
                    raise ValueError("Upravovat lze jen dosud nepřijatý úkol.")
                if body.get("expected_criteria") != task["criteria"]:
                    raise ValueError("Kritéria se mezitím změnila. Obnov přehled.")
                reason = text(body.get("reason"), "důvod změny", 3000)
                criteria = strings(body.get("criteria"), "kritéria úkolu", 20)
                instructions = text(body.get("instructions", task["instructions"]), "instrukce", 12000)
                m.setdefault("plan_revisions", []).append({"at": self.clock(), "task": task["id"],
                    "reason": reason, "before": {"criteria": task["criteria"], "instructions": task["instructions"]},
                    "after": {"criteria": criteria, "instructions": instructions}})
                task.update(criteria=criteria, instructions=instructions)
                for q in m["questions"]:
                    if q["task"] == task["id"] and q["answer"] is None and q.get("kind") == "criterion" and q.get("criterion") not in criteria:
                        q["answer"] = "Kritérium nahrazeno zaznamenanou úpravou zadání: " + reason
                if task["status"] == "waiting" and not any(q["task"] == task["id"] and q["answer"] is None for q in m["questions"]):
                    task["status"] = "pending"
                m.update(failures=0, retry_at=0, message="Zadání úkolu upraveno; původní znění zůstává v historii. Pokračování spusť samostatně.")
            elif action == "set_checks" and m["status"] in {"draft", "paused", "awaiting_plan", "awaiting_checks", "ready"}:
                checks = validate_checks(body.get("verification_checks"))
                if not checks:
                    raise ValueError("Přidej alespoň jeden příkaz kontroly.")
                m.update(verification_checks=checks, verification_id=None)
                if m["status"] in {"awaiting_checks", "ready"}:
                    self.require_active_product(m)
                    m.update(status="verifying", message="Spustím schválené kontroly.")
                elif m["status"] == "paused" and m.get("final_report"):
                    m["resume_status"] = "verifying"
            elif action == "manual_accept" and m["status"] == "awaiting_checks":
                if body.get("acknowledge_unverified") is not True:
                    raise ValueError("Potvrď ruční převzetí bez automatických kontrol.")
                self.verify_delivery(m, require_checks=False)
                self.capture_delivery(m)
                m.update(status="accepted", acceptance={"kind": "manual", "at": self.clock()},
                         message="Ručně převzato vlastníkem bez nezávislých automatických kontrol.")
            elif action == "answer":
                q = next((q for q in m["questions"] if q["id"] == body.get("question")), None)
                if not q or q["answer"] is not None or m["status"] in TERMINAL:
                    raise ValueError("Otázka už není otevřená.")
                q["answer"] = text(body.get("answer"), "odpověď", 12000)
                pending = [x for x in m["questions"] if x["task"] == q["task"] and x["answer"] is None]
                if not pending and q["task"]:
                    task = next(t for t in m["tasks"] if t["id"] == q["task"])
                    task["status"] = "pending"
                if m["status"] == "waiting" and not any(q["answer"] is None for q in m["questions"]):
                    m["status"] = "awaiting_plan" if m["phase"] == "plan" and m["tasks"] else "running"
                m["message"] = "Odpověď uložena."
            elif action == "start" and m["status"] == "draft":
                m.update(status="running", deadline=self.clock() + m["days"] * 86400,
                         message="Připravuji plán a vstupní otázky.")
            elif action == "approve_plan" and m["status"] == "awaiting_plan":
                if any(q["answer"] is None for q in m["questions"]):
                    raise ValueError("Nejdřív zodpověz vstupní otázky.")
                m.update(status="running", phase="build", message="Plán potvrzen. Pokračuji realizací.")
            elif action in {"pause", "cancel"} and m["status"] not in TERMINAL:
                if m["status"] != "paused":
                    m["resume_status"] = m["status"]
                m.update(status="paused" if action == "pause" else "cancelled",
                         message="Pozastaveno uživatelem." if action == "pause" else "Ukončeno uživatelem.")
                self.save(m)  # persist intent before terminating a worker
                self.verifications.stop(m.get("verification_id"))
                if m["active_attempt"] in self.studio.runs:
                    self.studio.stop(m["active_attempt"])
            elif action == "resume" and m["status"] in {"paused", "blocked"}:
                if m["deadline"] is not None and self.clock() >= m["deadline"]:
                    raise ValueError("Časový limit vypršel; založ navazující projekt s novým rozsahem.")
                if len(m["attempts"]) >= m["max_attempts"]:
                    raise ValueError("Limit běhů byl vyčerpán; založ navazující projekt.")
                previous = m.get("resume_status", "running") if m["status"] == "paused" else "running"
                if previous == "blocked":
                    previous = "running"
                if previous == "waiting" and not any(q["answer"] is None for q in m["questions"]):
                    previous = "awaiting_plan" if m["phase"] == "plan" and m["tasks"] else "running"
                m.update(status=previous, failures=0, retry_at=0, message="Pokračuji z uloženého stavu.")
            elif action == "recheck" and m["status"] in {"ready", "awaiting_checks"}:
                m.update(status="running", final_report=None, verification_id=None, message="Znovu ověřím produkt.")
            elif action == "accept" and m["status"] == "ready":
                # Re-check the delivered files; accepting stale evidence would hide later edits.
                self.verify_delivery(m)
                self.capture_delivery(m)
                m.update(status="accepted", acceptance={"kind": "verified", "at": self.clock()},
                         message="Produkt převzat po nezávislých kontrolách.")
            else:
                raise ValueError("Tato akce není v aktuálním stavu dostupná.")
            self.save(m)
            return m

    def require_active_product(self, m):
        """Parent pause is authoritative even for direct mission API calls."""
        company_id = (m.get("company_context") or {}).get("id")
        if company_id:
            company = self.studio.companies.get(company_id)
            if company["status"] != "active" or not company["deadline"] or self.clock() >= company["deadline"]:
                raise ValueError("Nejdřív obnov nadřazenou firmu a její časový horizont.")
        product_id = (m.get("product_context") or {}).get("id")
        if product_id:
            product = self.studio.products.get(product_id)
            if product["status"] != "active":
                raise ValueError("Nejdřív obnov nadřazený produkt; jeho realizace je pozastavená.")

    def ensure_workspace(self, m):
        if not m.get("isolated") or m.get("work_project"):
            return
        root = self.studio.project(m["project"])
        self.versions.recover(root)
        if not m.get("base_version"):
            m["base_version"] = self.versions.snapshot(root, label="Výchozí stav realizace " + m["id"])["id"]
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
                raise ValueError("Nedokončená příprava pracovního prostoru obsahuje změny; automatické přepsání odmítnuto.")
        project = self.studio.add_project(destination, name=m["title"][:70] + " · pracovní verze")
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
        common = f"""Pracuješ na dlouhodobém projektu AI Build Company: {m['title']}.
FÁZE TOHOTO BĚHU: {a['phase']}. Cíl produktu níže je kontext; proveď pouze instrukce této fáze.
Kořen projektu je {m['workspace']}. Souborové nástroje přijímají /workspace jako alias této složky.
Shell běží ve fyzické složce projektu: používej relativní cesty, nevytvářej systémový /workspace.
Cíl: {m['goal']}
Kritéria produktu: {json.dumps(m['criteria'], ensure_ascii=False)}
Omezení: {m['constraints']}
Příkazy kontrol schválené vlastníkem (řadič je spustí nezávisle po review): {json.dumps(m.get('verification_checks', []), ensure_ascii=False)}
Podklady a veřejné zdroje: {m['sources']}
Odpovědi vlastníka: {json.dumps([q for q in m['questions'] if q['answer'] is not None], ensure_ascii=False)}
Pracuj jen v rozsahu zadání a povolených nástrojů aktuální fáze. Nevymýšlej zdroje ani kontroly.
Neznámé zjistitelné průzkumem patří do realizačních úkolů; otázky vlastníkovi používej pro nezbytná rozhodnutí.
Po přerušení nejdřív prohlédni soubory a dřívější reporty v company/projects/{m['id']}/reports/;
neopakuj naslepo již provedené akce. Neprováděj platby, zprávy třetím stranám ani nasazení;
připrav tyto kroky jako podklady k rozhodnutí vlastníka. Nepřepisuj databázi ani konfiguraci Studia.
Existuje-li company/ai-build-company.json, přečti relevantní pravidla a instrukce rolí.
Výsledky ukládej přímo do dohodnutých cest projektu, ne pouze do dočasných /outputs.
Na závěr zavolej save_mission_report s pojmenovanými poli status, tasks, questions nebo poli výsledku podle fáze.
Aplikace ověří obsah a uloží JSON soubor {path} v projektu. Nevytvářej tento soubor ručně.
Nepředávej cestu, content, data ani serializovaný JSON text. Použij přímo pole nástroje.
Dodrž přesně zadaná jména polí a hodnotu status; nepřidávej vlastní formát reportu.
Chyby předchozích pokusů, které musíš napravit: {json.dumps([x.get('error') for x in m['attempts'][-4:] if x.get('error')], ensure_ascii=False)}
Report nesmí sám sebe uvést jako produktový artefakt. Cesty jsou relativní ke kořeni projektu.
Pokud potřebuješ rozhodnutí člověka, vrať {{"status":"blocked","questions":[{{"question":"...","reason":"..."}}]}}.
"""
        if m.get("company_context"):
            common += "\nKontext firmy a oddělení: " + json.dumps(m["company_context"], ensure_ascii=False) + "\n"
        if m.get("product_context"):
            common += "\nKontext trvale spravovaného produktu: " + json.dumps(m["product_context"], ensure_ascii=False) + "\n"
            common += ("Navazuj na existující soubory. Zachovej dosavadní funkce a data; "
                       "nepřepisuj celý produkt bez důvodu. Ověř regresní kritéria i nový požadavek. "
                       "Připrav návod ke spuštění, ověření a údržbě odpovídající typu produktu. "
                       "Fyzickou výrobu, nasazení nebo externí službu neoznačuj za hotové bez skutečného důkazu.\n")
        if a["phase"] == "plan":
            return common + """Jsi plánovač. TEĎ POUZE PLÁNUJ, NEVYTVÁŘEJ FINÁLNÍ PRODUKT.
Použij nejvýše tři čtecí volání pro místní podklady, potom rovnou ulož plán.
Buď stručný: obvykle stačí 2–5 realizačních úkolů, krátké instrukce a konkrétní kritéria.
Úkolem plánovače je sestavit kroky, nikoli získat výsledky těchto kroků.
V této fázi nejsou shell a web dostupné záměrně; realizátor je může mít. To není důkaz chybějícího přístupu.
Pokud zadání obsahuje server, URL nebo příkaz pro přístup, přenes jej přesně do realizačního úkolu a naplánuj jeho skutečné ověření.
Například existující SSH příkaz má realizátor nejprve vyzkoušet; neptej se znovu, jak se připojit nebo zda smí provést už zadané čtení.
O přístupových problémech se ptej až po konkrétním selhání v realizaci. Nikdy nevyžaduj vypsání tajných údajů.
Než položíš otázku, ověř, zda ji už neřeší zadání. Chybějící šablona Markdownu, neexistující výstupní soubor
nebo dosud neprovedený průzkum nejsou blokace plánu. Formát navrhni podle kritérií produktu.
Otázky polož jen tam, kde bez rozhodnutí vlastníka nelze ani sestavit bezpečný první úkol.
Realizační úkoly mají vytvářet požadovaný produkt. Vytvoření nebo kontrolu tohoto plánovacího JSONu
mezi ně nezařazuj: validaci plánu a oddělené review provádí řadič automaticky.
Kritéria úkolů nesmějí zpřísnit cíl vlastníka. U inventury, která dovoluje neznámé nebo chybějící
služby, je platným výsledkem také doložené nenalezení s uvedeným rozsahem průzkumu a omezeními.
Nevyžaduj jako podmínku úspěchu nalezení nebo funkčnost služby, jejíž existenci zadání teprve zjišťuje.
Přesně zachovej cílové cesty ze zadání. Složka company/projects/.../reports je pouze pro interní reporty,
není automaticky složkou produktu. Samotné názvy souborů v zadání znamenají cesty od kořene projektu.
Nevytvářej zatím produkt a nedělej změny mimo svůj report. Veřejně dohledatelné věci zařaď jako rešeršní úkol.
Report: {"status":"plan","questions":[{"question":"...","reason":"..."}],"tasks":[
{"id":"task-1","title":"...","instructions":"Konkrétní práce a cílové soubory",
"depends_on":[],"criteria":["Ověřitelná podmínka"]}]}. Otázky mohou být prázdné.
Úkoly musí mít jedinečná ID, žádné cykly a žádné závislosti na neexistujících úkolech. Nejvýše 40 úkolů.
"""
        if a["phase"] == "build":
            template = {"status": "done", "summary": "Doplň shrnutí provedené práce.",
                "artifacts": ["relativni/soubor"],
                "checks": [{"criterion": criterion, "passed": False, "evidence": "Doplň skutečný výsledek."}
                           for criterion in task["criteria"]], "sources": []}
            return common + f"""Jsi realizátor úkolu: {json.dumps(task, ensure_ascii=False)}
Splň tento úkol a zkontroluj výstupy. Report musí pokrýt kritéria tohoto úkolu;
obecná kritéria produktu je nenahrazují. Zachovej přesná znění criterion z této šablony:
{json.dumps(template, ensure_ascii=False)}
Doplň skutečné soubory, shrnutí a důkazy; passed změň na true pouze po ověření.
Neúspěch neoznačuj jako passed. Pokud jsi použil zdroje, sources má položky url a finding.
"""
        criteria = task["criteria"] if task else m["criteria"]
        evidence = task if task else m["tasks"]
        template = {"status": "changes", "summary": "Doplň nálezy nebo výsledek review.",
            "artifacts": ["ověřený/soubor"],
            "checks": [{"criterion": criterion, "passed": False, "evidence": "Doplň vlastní zjištění ze souborů."}
                       for criterion in criteria]}
        return common + f"""Jsi nezávislý reviewer v nové relaci. Prohlédni skutečné soubory a posuď kritéria podle jejich obsahu;
nespoléhej na tvrzení autora. Neupravuj produktové soubory, pouze svůj report. Nálezy vrať autorovi.
V této fázi nemáš shell. Nezávislé příkazy spustí řadič po závěrečném review; netvrď, že jsi je spustil sám.
Podklady: {json.dumps(evidence, ensure_ascii=False)}
Kritéria k ověření: {json.dumps(criteria, ensure_ascii=False)}
Zachovej přesná znění criterion z této šablony a doplň vlastní zjištění:
{json.dumps(template, ensure_ascii=False)}
Pro KAŽDÉ kritérium uveď kontrolu, passed true pouze po skutečném ověření. Status změň
na pass pouze při splnění všech kritérií; jinak použij changes.
Při changes uveď konkrétní reprodukovatelné vady v summary.
"""

    def artifacts(self, m, paths):
        paths = strings(paths, "výstupní soubory", 100)
        result = []
        for path in dict.fromkeys(paths):
            if path.startswith("company/projects/") or path.startswith(".apodex/"):
                raise ValueError("Report nebo dočasný výstup není finální produktový soubor.")
            revision = self.studio.artifact_revision(m.get("work_project", m["project"]), path)
            result.append({"path": path, "sha256": revision})
        return result

    def checks(self, report, criteria, passing=True):
        checks = report.get("checks")
        if not isinstance(checks, list) or len(checks) > 100:
            raise ValueError("Chybí doložené kontroly.")
        found = set()
        for item in checks:
            criterion = text(item.get("criterion"), "kritérium", 3000)
            text(item.get("evidence"), "důkaz kontroly", 6000)
            if not isinstance(item.get("passed"), bool) or (passing and not item["passed"]):
                raise ValueError("Některé kontroly neprošly.")
            found.add(criterion)
        if not set(criteria) <= found:
            missing = [criterion for criterion in criteria if criterion not in found]
            raise ValueError("Report nepokrývá všechna kritéria. Chybí přesné znění: " +
                             json.dumps(missing, ensure_ascii=False)[:1800])
        return checks

    def verify_delivery(self, m, require_checks=True):
        report = m["final_report"]
        if not report:
            raise ValueError("Chybí závěrečné review.")
        for item in report["verified_artifacts"]:
            project = m["project"] if m["status"] == "accepted" else m.get("work_project", m["project"])
            current = self.studio.artifact_revision(project, item["path"])
            if current != item["sha256"]:
                raise ValueError(f"Soubor {item['path']} se od review změnil. Je nutné nové ověření.")
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
            raise ValueError("Report musí být JSON objekt.")
        phase, task_id = a["phase"], a["task"]
        task = next((t for t in m["tasks"] if t["id"] == task_id), None)
        status = report.get("status")
        if status == "blocked":
            self.questions(m, report.get("questions"), task_id)
            if task:
                task["status"] = "waiting"
            else:
                m["status"] = "waiting"
            m["message"] = "Potřebuji odpověď; nezávislé úkoly mohou pokračovat."
        elif phase == "plan" and status == "plan":
            tasks = parse_plan(report)
            if any(a["id"] + ".json" in json.dumps(t, ensure_ascii=False) for t in tasks):
                raise ValueError("Realizační úkol nesmí vytvářet ani kontrolovat vlastní plánovací report. "
                                 "Odstraň tento interní krok; validaci plánu a review provádí řadič.")
            if report.get("questions"):
                self.questions(m, report["questions"])
            m.update(tasks=tasks, status="waiting" if any(q["answer"] is None for q in m["questions"]) else "awaiting_plan",
                     message="Zkontroluj vstupní otázky a navržený plán.")
        elif phase == "build" and status == "done":
            checked = self.artifacts(m, report.get("artifacts"))
            checks = self.checks(report, task["criteria"], passing=False)
            failed = [c for c in checks if not c["passed"]]
            if failed:
                question_start = len(m["questions"])
                self.questions(m, [{"question": "Nesplněné kritérium: " + c["criterion"][:2300] +
                    " — doplň podklady nebo uprav zadání úkolu.", "reason": c["evidence"][:3000]}
                    for c in failed[:20]], task_id)
                for q, check in zip(m["questions"][question_start:], failed):
                    q.update(kind="criterion", criterion=check["criterion"])
                task["status"] = "waiting"
                m["message"] = "Výsledek nesplňuje kritéria. Čekám na upřesnění bez opakování stejného pokusu."
            else:
                task.update(status="review", artifacts=checked, checks=checks,
                            summary=text(report.get("summary"), "shrnutí"), build_run=a["id"])
        elif phase in {"review", "final"} and status in {"pass", "changes"}:
            summary = text(report.get("summary"), "shrnutí review")
            if status == "changes":
                if task:
                    task["cycles"] += 1
                    task.update(status="pending", feedback=summary)
                    if task["cycles"] >= 3:
                        m.update(status="blocked", message="Tři kola oprav bez přijetí. Je potřeba upravit zadání.")
                else:
                    m["final_cycles"] += 1
                    m.update(status="blocked" if m["final_cycles"] >= 3 else "running",
                             message="Závěrečná kontrola vrátila vady: " + summary)
                    # Preserve completed work but reopen a delivery task with the findings.
                    m["tasks"][-1].update(status="pending", feedback=summary)
            else:
                criteria = task["criteria"] if task else m["criteria"]
                checks = self.checks(report, criteria)
                checked = self.artifacts(m, report.get("artifacts"))
                expected = task["artifacts"] if task else [x for t in m["tasks"] for x in t["artifacts"]]
                if not {x["path"] for x in expected} <= {x["path"] for x in checked}:
                    raise ValueError("Review nepokrývá všechny výstupní soubory.")
                if task:
                    # A reviewer may not quietly change what it was asked to review.
                    if any(x not in checked for x in expected):
                        raise ValueError("Výstupy se během review změnily; je potřeba nové zpracování.")
                    task.update(status="done", review_run=a["id"], review_summary=summary, review_checks=checks)
                else:
                    m.update(status="verifying", verification_id=None,
                             message="Review dokončeno. Následují nezávislé kontroly.",
                             final_report={**report, "verified_artifacts": checked, "run": a["id"]})
        else:
            raise ValueError(f"Neočekávaný report pro fázi {phase}: {status}.")
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
                ("časový limit" in str(reason) or str(reason) == "max_turns")):
            m["status"] = "blocked"
            m["message"] = "Běh zastaven bez automatického opakování. " + str(reason)[:1600]
            if not any(q["answer"] is None for q in m["questions"]):
                self.questions(m, [{"question": "Jak mám upravit postup, než úlohu znovu spustíš?", "reason": m["message"]}])
        elif m["failures"] >= 3:
            m["status"] = "blocked"
            m["message"] = "Tři neúspěšné pokusy. " + m["message"]
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
        m.update(status="waiting", message="Čekám na odpovědi k zablokovaným úkolům.")
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
            m.update(status="expired", message="Vypršel časový limit projektu; výsledky zůstávají uložené.")
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
                    a["error"] = "Běh překročil časový limit."
                    self.studio.stop(a["id"])
                directory = self.studio.run_dir(a["id"])
                size = sum(p.stat().st_size for p in directory.glob('*') if p.is_file())
                if size > 128_000_000 or size + sum(x.get("log_bytes", 0) for x in m["attempts"]) > 1_000_000_000:
                    a["error"] = "Projekt překročil limit logů (128 MB na běh, 1 GB na projekt)."
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
                    current_version = self.versions.snapshot(m["workspace"], label="Po běhu " + a["id"])
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
                        raise ValueError("Plánování nebo review změnilo produktové soubory; výstup nelze převzít.")
                    # Validate on a copy so rejected reports cannot partially advance the project.
                    copy = json.loads(json.dumps(m))
                    ca = next(x for x in copy["attempts"] if x["id"] == a["id"])
                    self.consume(copy, ca)
                    ca["outcome"] = "reported"
                    m = copy
                except (ValueError, TypeError, KeyError, OSError, RuntimeError) as exc:
                    self.fail(m, a, f"Neplatný výstup: {exc}")
                except Exception as exc:
                    # Studio's protected-file errors also invalidate a report.
                    self.fail(m, a, f"Výstup nelze ověřit: {exc}")
            else:
                self.fail(m, a, a.get("error") or (run or {}).get("reason") or "Přerušený běh; ověřím dosavadní soubory a navážu.")
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
            m.update(status="blocked", message="Příprava pracovního prostoru selhala: " + str(exc))
            self.save(m)
            return
        if len(m["attempts"]) >= m["max_attempts"]:
            m.update(status="blocked", message="Vyčerpán limit běhů; dosavadní výsledky jsou uložené.")
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
            a["source_version"] = self.versions.snapshot(m["workspace"], label="Před během " + a["id"])["id"]
        except Exception as exc:
            m.update(status="blocked", message="Nelze uložit výchozí stav běhu: " + str(exc))
            self.save(m)
            return
        m["attempts"].append(a)
        m["active_attempt"] = a["id"]
        m["message"] = {"plan": "Připravuji plán a otázky.", "build": "Pracuji na úkolu: ",
                        "review": "Modelové review úkolu: ", "final": "Závěrečné modelové review produktu."}[phase] + (task_id or "")
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
            m.update(status="awaiting_checks", message="Review je hotové. Zadej příkazy nezávislých kontrol, nebo výslovně převezmi výsledek ručně.")
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
                    m.update(status="ready", message="Nezávislé kontroly i review prošly. Připraveno k převzetí.")
                except Exception as exc:
                    m.update(status="blocked", resume_status="verifying", verification_id=None, message=str(exc))
            else:
                reason = record.get("error", "Kontrola neprošla.")
                m["final_cycles"] += 1
                log = "\n".join(c.get("log", "")[-6000:] for c in record["checks"][-2:])
                m["tasks"][-1].update(status="pending", feedback=f"Nezávislá kontrola {key}: {reason}\n{log}")
                m.update(status="blocked" if m["final_cycles"] >= 3 else "running", final_report=None,
                         verification_id=None, message="Nezávislé kontroly vrátily opravu: " + reason)
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
                "search_status": "Nastavené přihlašování Serper; dostupnost nebyla živě ověřena."
                if key.strip() else "Vyhledávání není nastavené (Serper). Dodej odkazy nebo nastav SERPER_API_KEY.",
                "direct_fetch": True, "controller": "Studio na tomto počítači; musí zůstat spuštěné."}

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=10)
        self.verifications.close()
        self.decisions.close()
