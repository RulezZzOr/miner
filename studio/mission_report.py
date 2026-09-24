"""A small, run-scoped report tool: the model supplies facts, never a path."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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
    artifacts: list[str] = Field(min_length=1, max_length=100)
    checks: list[Check] = Field(min_length=1)
    sources: list[Source] = Field(default_factory=list)


class MissionReport:
    def __init__(self, request, path):
        self.phase = request["mission"]["phase"]
        self.attempt = request["mission"]["attempt"]
        self.path = path
        self.saved = False
        self.typed_review = bool(request["mission"].get("review_packet"))

    def validate(self, value):
        if not isinstance(value, dict):
            raise ValueError("Pass named tool fields, not a JSON string.")
        model = Blocked if value.get("status") == "blocked" else Plan if self.phase == "plan" else Result
        report = model.model_validate(value).model_dump()
        if self.phase == "plan" and report.get("readiness") is None:
            report.pop("readiness", None)  # retain the historical report shape for simple briefs
        if self.typed_review and report["status"] != "blocked":
            for check in report.get("checks", []):
                if check["outcome"] is None or check["issue"] is None:
                    raise ValueError("Every review check needs outcome and issue fields.")
                if check["passed"] and (check["outcome"] != "supported" or check["issue"] != "none" or check["needs_owner"]):
                    raise ValueError("A check with missing evidence, defects or an owner decision cannot pass.")
                if report["status"] == "pass" and not check["passed"]:
                    raise ValueError("A passing report requires every check to pass.")
        if report["status"] != "blocked":
            allowed = {"plan"} if self.phase == "plan" else {"done"} if self.phase == "build" else {"pass", "changes"}
            if report["status"] not in allowed:
                raise ValueError(f"For phase {self.phase} use a status from {sorted(allowed)}.")
        if report["status"] == "plan":
            try:
                from .missions import parse_plan
            except ImportError:
                from missions import parse_plan
            parse_plan(report)
            if any(self.attempt + ".json" in str(t) for t in report["tasks"]):
                raise ValueError("Internal report does not belong to execution tasks.")
        return report

    def tool(self):
        from frontier_agent.core.tool import Tool
        from plugins.tools.create_file import create_file
        import json

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

        return Tool(name="save_mission_report", description=(
            "Submit the result of this phase and terminate the run. Pass fields directly as arguments, "
            "not as JSON text. The application will verify the content itself and save the file to the correct path. "
            "For a plan, use status=plan, specific execution tasks and questions (usually []). "
            "Use status=blocked only for mandatory owner decisions."), parameters=schema, func=write)
