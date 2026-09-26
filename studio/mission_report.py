"""A small, run-scoped report tool: the model supplies facts, never a path.

`check_report` is the single semantic contract for phase reports. The in-run tool
and the controller both call it, so a report the tool accepts is never rejected
later for the same reason (the controller only adds hash and snapshot checks).
"""
import json
import re
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ENGLISH = ("Write all reports, questions, summaries, notes and generated documentation in English, "
           "regardless of the language of the input.")
IDENTIFIER = re.compile(r"[a-zA-Z0-9_-]{1,60}\Z")
READINESS_MISSING = ("Complex brief requires a readiness assessment before execution. Add "
                     "readiness={status: ready|clarify|blocked, reason: a short concrete explanation} "
                     "and save the report again.")
NO_REPORT = "No phase report was saved; call save_mission_report before finishing."


def text(value, name, limit=12000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"Fill in {name} (at most {limit} characters).")
    return value.strip()


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
        if not isinstance(item, dict):
            raise ValueError("Each plan task must be an object.")
        key = text(item.get("id"), "Task ID", 60)
        if not IDENTIFIER.fullmatch(key) or key in ids:
            raise ValueError("Task IDs must be unique, without spaces or slashes.")
        ids.add(key)
        deps = item.get("depends_on", [])
        if not isinstance(deps, list) or any(not isinstance(d, str) for d in deps):
            raise ValueError("Dependencies must be a list of IDs.")
        result.append({"id": key, "title": text(item.get("title"), "name", 200),
                       "instructions": text(item.get("instructions"), "instructions"),
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


def check_questions(values):
    if not isinstance(values, list) or not 1 <= len(values) <= 20:
        raise ValueError("The block must contain 1–20 questions.")
    for item in values:
        if not isinstance(item, dict):
            raise ValueError("Each question must be an object with question and reason.")
        text(item.get("question"), "question", 3000)
        text(item.get("reason"), "question reason", 3000)
    return values


def protected_part(name):
    name = name.casefold()
    return name in {".git", ".switch-agent"} or name.startswith(".env")


def artifact_path(path, workspace=None):
    """Normalize a reported output path to the project-relative form, or explain the rejection."""
    value = text(path, "output files", 3000)
    prefixes = ["/workspace/"]
    for root in [workspace, Path(workspace).resolve() if workspace else None]:
        if root:
            prefixes.append(str(root).rstrip("/") + "/")
    for prefix in prefixes:
        if value.startswith(prefix):
            value = value[len(prefix):]
            break
    while value.startswith("./"):
        value = value[2:]
    parts = PurePosixPath(value).parts
    if not value or value.startswith("/") or ".." in parts:
        raise ValueError(f"Output path must be relative to the project root: {path}")
    if value.startswith(("company/projects/", ".apodex/")):
        raise ValueError(f"Report or temporary output is not a final product file: {path}")
    if any(protected_part(p) for p in parts):
        raise ValueError(f"Output path is not available as a product file: {path}")
    return str(PurePosixPath(*parts))


def local_artifact(root):
    """In-run existence check with the same rules the controller's safe reader applies."""
    root = Path(root)

    def check(relative, reported):
        parts = PurePosixPath(relative).parts
        try:
            if any(root.joinpath(*parts[:i]).is_symlink() for i in range(1, len(parts) + 1)):
                raise ValueError(f"Output path uses a symbolic link: {reported}")
            target = root.joinpath(*parts)
            if not target.exists():
                raise ValueError(f"Output file not found: {reported}. Save it in the project and list its path relative to the project root.")
            if not target.is_file():
                raise ValueError(f"Output is not a regular file: {reported}")
        except OSError as exc:
            raise ValueError(f"Output file {reported} cannot be read: {exc.strerror or exc}") from None
    return check


def check_artifacts(paths, workspace=None, exists=None):
    result = []
    for path in dict.fromkeys(strings(paths, "output files", 100)):
        relative = artifact_path(path, workspace)
        if exists:
            exists(relative, path)
        result.append(relative)
    return list(dict.fromkeys(result))


def check_checks(report, criteria, passing=True, typed=False):
    checks = report.get("checks")
    if not isinstance(checks, list) or len(checks) > 100:
        raise ValueError("Missing documented checks.")
    found = set()
    for item in checks:
        if not isinstance(item, dict):
            raise ValueError("Each check must be an object.")
        criterion = text(item.get("criterion"), "criterion", 3000)
        text(item.get("evidence"), "proof of check", 6000)
        if not isinstance(item.get("passed"), bool) or (passing and not item["passed"]):
            raise ValueError("Some checks failed.")
        outcome, issue = item.get("outcome"), item.get("issue")
        if typed and (outcome is None or issue is None or type(item.get("needs_owner", False)) is not bool):
            raise ValueError("Bounded review requires typed outcome, issue and owner-decision fields.")
        if outcome is not None and outcome not in {"supported", "contradicted", "insufficient_evidence"}:
            raise ValueError("Unknown evidence outcome.")
        if issue is not None and issue not in {"none", "architecture", "functionality", "missing_evidence"}:
            raise ValueError("Unknown review issue category.")
        if item.get("passed") and (outcome not in {None, "supported"} or issue not in {None, "none"} or item.get("needs_owner")):
            raise ValueError("A check with missing evidence, defects or an owner decision cannot pass.")
        found.add(criterion)
    if criteria is not None and not set(criteria) <= found:
        missing = [criterion for criterion in criteria if criterion not in found]
        raise ValueError("Report does not cover all criteria. Missing precise wording: " +
                         json.dumps(missing, ensure_ascii=False)[:1800])
    return checks


def check_report(report, phase, *, attempt="", readiness_required=False, criteria=None,
                 expected_artifacts=None, typed=False, workspace=None, exists=None):
    """Validate one phase report; return it with normalized artifact paths.

    Everything the controller rejects on content is rejected here, so the in-run
    tool can answer 'Report NOT saved' while the model can still correct it.
    """
    if not isinstance(report, dict):
        raise ValueError("Report must be a JSON object.")
    status = report.get("status")
    if status == "blocked":
        check_questions(report.get("questions"))
        return report
    allowed = {"plan"} if phase == "plan" else {"done"} if phase == "build" else {"pass", "changes"}
    if status not in allowed:
        raise ValueError(f"Unexpected report for phase {phase}: {status}. Use a status from {sorted(allowed | {'blocked'})}.")
    if phase == "plan":
        if readiness_required:
            assessment = report.get("readiness")
            if not isinstance(assessment, dict) or assessment.get("status") not in {"ready", "clarify", "blocked"}:
                raise ValueError(READINESS_MISSING)
            text(assessment.get("reason"), "readiness reason", 2000)
            if assessment["status"] != "ready" and not report.get("questions"):
                raise ValueError("A non-ready brief requires grouped owner questions.")
        tasks = parse_plan(report)
        if attempt and any(attempt + ".json" in json.dumps(t, ensure_ascii=False) for t in tasks):
            raise ValueError("Execution task must not create or verify its own planning report. "
                             "Remove this internal step; plan validation and review are performed by the controller.")
        if report.get("questions"):
            check_questions(report["questions"])
        return report
    if phase == "build":
        report = {**report, "artifacts": check_artifacts(report.get("artifacts"), workspace, exists)}
        checks = check_checks(report, criteria, passing=False)
        if all(c["passed"] for c in checks):
            text(report.get("summary"), "summary")
        return report
    text(report.get("summary"), "review summary")
    if status == "changes":
        return report
    check_checks(report, criteria, typed=typed)
    report = {**report, "artifacts": check_artifacts(report.get("artifacts"), workspace, exists)}
    if expected_artifacts is not None:
        missing = sorted(set(expected_artifacts) - set(report["artifacts"]))
        if missing:
            raise ValueError("Review does not cover all output files. Missing: " + json.dumps(missing, ensure_ascii=False)[:1800])
    return report


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Question(Record):
    question: str = Field(min_length=1, max_length=3000)
    reason: str = Field(min_length=1, max_length=3000)


class Task(Record):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,60}$")
    title: str = Field(min_length=1, max_length=200)
    instructions: str = Field(min_length=1, max_length=12000)
    depends_on: list[str] = Field(default_factory=list)
    criteria: list[str] = Field(min_length=1, max_length=20)


class Readiness(Record):
    status: Literal["ready", "clarify", "blocked"]
    reason: str = Field(min_length=1, max_length=2000)


class Plan(Record):
    status: Literal["plan"]
    tasks: list[Task] = Field(min_length=1, max_length=40)
    readiness: Readiness | None = None
    questions: list[Question] = Field(default_factory=list, max_length=20)


class Blocked(Record):
    status: Literal["blocked"]
    questions: list[Question] = Field(min_length=1, max_length=20)


class Check(Record):
    criterion: str = Field(min_length=1)
    passed: bool
    evidence: str = Field(min_length=1)
    outcome: Literal["supported", "contradicted", "insufficient_evidence"] | None = None
    issue: Literal["none", "architecture", "functionality", "missing_evidence"] | None = None
    needs_owner: bool = False


class Source(Record):
    url: str
    finding: str


class Result(Record):
    status: Literal["done", "pass", "changes"]
    summary: str = Field(min_length=1, max_length=12000)
    # Required for done/pass by check_report; a 'changes' review may omit them.
    artifacts: list[str] = Field(default_factory=list, max_length=100)
    checks: list[Check] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)


class MissionReport:
    def __init__(self, request, path):
        mission = request["mission"]
        self.phase = mission["phase"]
        self.attempt = mission["attempt"]
        self.path = path
        self.saved = False
        self.typed_review = bool(mission.get("review_packet"))
        self.readiness_required = mission.get("readiness_required") is True
        self.criteria = mission.get("criteria") if isinstance(mission.get("criteria"), list) else None
        expected = mission.get("expected_artifacts")
        self.expected_artifacts = expected if isinstance(expected, list) else None
        self.workspace = request.get("cwd")

    def validate(self, value):
        if not isinstance(value, dict):
            raise ValueError("Pass named tool fields, not a JSON string.")
        model = Blocked if value.get("status") == "blocked" else Plan if self.phase == "plan" else Result
        report = model.model_validate(value).model_dump()
        if self.phase == "plan" and report.get("readiness") is None:
            report.pop("readiness", None)  # retain the historical report shape for simple briefs
        return check_report(report, self.phase, attempt=self.attempt, readiness_required=self.readiness_required,
                            criteria=self.criteria, expected_artifacts=self.expected_artifacts,
                            typed=self.typed_review, workspace=self.workspace,
                            exists=local_artifact(self.workspace) if self.workspace else None)

    def tool(self):
        from frontier_agent.core.tool import Tool
        from plugins.tools.create_file import create_file

        model = Plan if self.phase == "plan" else Result
        schema = model.model_json_schema()
        statuses = ["plan", "blocked"] if self.phase == "plan" else ["done", "blocked"] if self.phase == "build" else ["pass", "changes", "blocked"]
        schema["properties"]["status"] = {"type": "string", "enum": statuses}
        schema["required"] = ["status"]  # Other fields depend on status; validate before approval.
        schema.setdefault("$defs", {})["Question"] = Question.model_json_schema()
        schema["properties"]["questions"] = {"type": "array", "items": {"$ref": "#/$defs/Question"}}

        async def write(*, data, path):
            # Only the observer can add these internal arguments, after validation
            # and the normal create_file approval. No caller-selected destination.
            if path != str(self.path):
                raise ValueError("Incorrect report target path.")
            report = self.validate(data)
            result = await create_file.ainvoke({"path": path, "data": report})
            if self.path.is_file() and json.loads(self.path.read_text()) == report:
                self.saved = True
                return "Report verified and saved. This run is complete."
            return result

        required = " This brief requires the readiness field." if self.readiness_required else ""
        return Tool(name="save_mission_report", description=(
            "Submit the result of this phase and terminate the run. Pass fields directly as arguments, "
            "not as JSON text. The application will verify the content itself and save the file to the correct path. "
            "For a plan, use status=plan, specific execution tasks and questions (usually [])." + required +
            " Use status=blocked only for mandatory owner decisions. Output paths are relative to the project root. " + ENGLISH),
            parameters=schema, func=write)
