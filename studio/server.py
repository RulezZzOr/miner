"""Switch Studio: local/LAN IDE and process bridge to the full Frontier fork."""

from __future__ import annotations

import argparse
import errno
import fcntl
import hashlib
import ipaddress
import json
import mimetypes
import os
import re
import secrets
import signal
import stat
import subprocess
import sys
import threading
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    from .browser import open_browser
    from .decision_lab import DecisionLab
    from .browser_pilot import BrowserPilot
    from .companies import Companies
    from .deployments import Deployments
    from .missions import Missions
    from .oauth import PROVIDERS, OAuthConnections
    from .preview import PreviewManager
    from .process_tree import ProcessTree
    from .products import KINDS, Products
    from .ssh_inventory import targets_for
except ImportError:
    from browser import open_browser
    from decision_lab import DecisionLab
    from browser_pilot import BrowserPilot
    from companies import Companies
    from deployments import Deployments
    from missions import Missions
    from oauth import PROVIDERS, OAuthConnections
    from preview import PreviewManager
    from process_tree import ProcessTree
    from products import KINDS, Products
    from ssh_inventory import targets_for

ROOT = Path(__file__).resolve().parents[1]
STATIC = Path(__file__).parent / "static"
COMPANY_SOURCE = Path(__file__).parent / "templates" / "ai-build-company.json"
COMPANY_PATH = "company/ai-build-company.json"
EXCLUDED = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".switch-agent",
    ".apodex",
    "dist",
}
MAX_FILE = 2_000_000
ACTIVE = {"running", "waiting", "stopping"}


class Problem(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def atomic_json(path, data):
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with tmp.open("x", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        tmp.replace(path)
        if os.name != "nt":
            fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    finally:
        tmp.unlink(missing_ok=True)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_target(root, relative):
    relative = str(relative)
    parts = Path(relative).parts
    if not relative or Path(relative).is_absolute() or ".." in parts:
        raise Problem("Invalid file path.")
    if any(protected_name(p) for p in parts):
        raise Problem("This file is not available in the editor.", 403)
    path = root / relative
    if any(
        (root.joinpath(*parts[:i])).is_symlink() for i in range(1, len(parts) + 1)
    ) or not path.resolve().is_relative_to(root.resolve()):
        raise Problem("The path leads outside the project or is a symbolic link.", 403)
    return path


def protected_name(name):
    name = name.casefold()
    return name in {".git", ".switch-agent"} or name.startswith(".env")


@contextmanager
def file_parent(root, relative, *, create=False, directory=False):
    """Pin each directory with no-follow opens, including after path validation."""
    if relative or not directory:
        file_target(root, relative)
    parts = Path(relative).parts
    if not parts and not directory:
        raise Problem("Invalid file path.")
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts if directory else parts[:-1]:
            if create:
                try:
                    os.mkdir(part, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd, None if directory else parts[-1]
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise Problem("Symbolic links are not available in the editor.", 403) from None
        if exc.errno == errno.ENOENT:
            raise Problem("File or folder does not exist.", 404) from None
        raise
    finally:
        os.close(fd)


def read_project_file(root, relative, limit):
    with file_parent(root, relative) as (parent, name):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise Problem("The path is not a regular file.")
            data = stream.read(limit + 1)
    if len(data) > limit:
        raise Problem(f"File exceeds the limit of {limit // 1_000_000} MB.")
    return data


def toml_value(value):
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        return (
            "{ " + ", ".join(f"{json.dumps(k)} = {toml_value(v)}" for k, v in value.items()) + " }"
        )
    if isinstance(value, list):
        return "[" + ", ".join(map(toml_value, value)) + "]"
    raise Problem("Unsupported configuration value.")


def write_config(path, profiles, default):
    text = f"default_profile = {toml_value(default)}\n"
    for name, profile in profiles.items():
        text += f"\n[profiles.{toml_value(name)}]\n"
        text += "".join(f"{json.dumps(k)} = {toml_value(v)}\n" for k, v in profile.items())
    tomllib.loads(text)
    path.write_text(text, encoding="utf-8")


class Studio:
    def __init__(self, workspace=ROOT, state_dir=None, config=None):
        self.data = Path(state_dir or ROOT / ".switch-agent" / "studio")
        self.data.mkdir(parents=True, exist_ok=True)
        self.config = Path(config or ROOT / "agent.toml")
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.RLock()
        self.processes = {}
        self.oauth = OAuthConnections(self.data)
        self.projects = self.read_json(self.data / "projects.json", {})
        self.runs = {}
        for path in self.data.glob("runs/*/run.json"):
            run = self.read_json(path, {})
            if not run.get("id"):
                continue
            if run.get("status") in ACTIVE:
                identities = self.read_json(path.parent / "processes.json", [])
                if not ProcessTree.recover(identities):
                    raise RuntimeError("Previous workers are still running; restoration has been stopped.")
                run.update(status="interrupted", ended=time.time())
                atomic_json(path, run)
            self.runs[run["id"]] = run
        self.add_project(str(workspace))
        self.missions = Missions(self)
        self.decision_lab = DecisionLab(self)
        self.browser_pilot = BrowserPilot(self)
        self.products = Products(self)
        self.deployments = Deployments(self)
        self.companies = Companies(self)
        self.preview = PreviewManager(self, read_project_file, file_parent, Problem)

    @staticmethod
    def read_json(path, default):
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return default

    def add_project(self, path, *, name=None):
        root = Path(path).expanduser().resolve()
        if not root.is_dir():
            raise Problem("Project folder does not exist.")
        key = digest(str(root).encode())[:12]
        with self.lock:
            self.projects[key] = {"id": key, "name": name or root.name, "path": str(root)}
            atomic_json(self.data / "projects.json", self.projects)
        return self.projects[key]

    def project(self, key):
        if key not in self.projects:
            raise Problem("Unknown project.", 404)
        return Path(self.projects[key]["path"])

    def profiles(self):
        with self.config.open("rb") as stream:
            original = tomllib.load(stream)
        extra = self.read_json(self.data / "models.json", {})
        return {**original.get("profiles", {}), **extra}, original.get("default_profile")

    def public_state(self):
        profiles, default = self.profiles()
        allowed = {
            "model",
            "protocol",
            "chat_dialect",
            "base_url",
            "auth",
            "api_key_env",
            "context_window",
            "max_output_tokens",
            "backend",
            "oauth_provider",
        }
        with self.lock:
            runs = [
                dict(r)
                for r in sorted(self.runs.values(), key=lambda r: r["created"], reverse=True)
            ]
        return {
            "token": self.token,
            "projects": list(self.projects.values()),
            "profiles": [
                {
                    "id": k,
                    "auth": v.get("auth", "env"),
                    **{a: b for a, b in v.items() if a in allowed},
                }
                for k, v in profiles.items()
            ],
            "default_profile": default,
            "runs": runs[:100],
        }

    def tree(self, project, relative=""):
        root = self.project(project)
        entries = []
        with file_parent(root, relative, directory=True) as (folder, _):
            with os.scandir(folder) as children:
                for path in children:
                    if (
                        path.name.casefold() in EXCLUDED
                        or protected_name(path.name)
                        or path.is_symlink()
                    ):
                        continue
                    entries.append(
                        {
                            "name": path.name,
                            "path": str(Path(relative) / path.name),
                            "directory": path.is_dir(follow_symlinks=False),
                        }
                    )
        return sorted(entries, key=lambda p: (not p["directory"], p["name"].lower()))[:500]

    def read_file(self, project, relative):
        data = read_project_file(self.project(project), relative, MAX_FILE)
        try:
            text = data.decode("utf-8")
            if "\0" in text:
                raise UnicodeError()
        except UnicodeError:
            raise Problem(
                "This file is not UTF-8 text. Open it in an external application."
            ) from None
        return {"path": relative, "content": text, "revision": digest(data)}

    def artifact_revision(self, project, relative):
        # Product evidence can include images and other binary deliverables.
        return digest(read_project_file(self.project(project), relative, 50_000_000))

    def save_file(self, body):
        root = self.project(body["project"])
        content = str(body["content"]).encode("utf-8")
        if len(content) > MAX_FILE:
            raise Problem("File exceeds 2 MB.")
        with self.lock, file_parent(root, body["path"], create=True) as (parent, name):
            expected = body.get("revision")
            try:
                fd = os.open(name, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            except FileNotFoundError:
                if expected is not None:
                    raise Problem("File was removed meanwhile.", 409) from None
                fd = os.open(
                    name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o666, dir_fd=parent
                )
                created = True
            else:
                created = False
            with os.fdopen(fd, "r+b") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise Problem("The path is not a regular file.")
                if not created and expected != digest(stream.read(MAX_FILE + 1)):
                    raise Problem(
                        "File has changed meanwhile. Reload it to avoid overwriting external changes.",
                        409,
                    )
                # Write through the checked descriptor, preserving executable bits.
                stream.seek(0)
                stream.write(content)
                stream.truncate()
        return {"path": body["path"], "revision": digest(content)}

    def company_template(self, project):
        self.project(project)
        try:
            self.read_file(project, COMPANY_PATH)
        except Problem as exc:
            if exc.status != 404:
                raise
            installed = False
        else:
            installed = True
        return {
            "template": json.loads(COMPANY_SOURCE.read_text(encoding="utf-8")),
            "project": project,
            "path": COMPANY_PATH,
            "installed": installed,
        }

    def install_company_template(self, body):
        # Reuse the editor's secure, create-only write. Never accept content,
        # path, or an overwrite revision from the install request.
        try:
            result = self.save_file({
                "project": body["project"],
                "path": COMPANY_PATH,
                "content": COMPANY_SOURCE.read_text(encoding="utf-8"),
                "revision": None,
            })
        except Problem as exc:
            if exc.status == 409:
                raise Problem("Template already exists in the project. Your edits have been preserved.", 409) from None
            raise
        return {**result, "project": body["project"]}

    def save_model(self, body):
        name = str(body.get("id", "")).strip()
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", name):
            raise Problem("Profile name may contain letters, numbers, hyphens, and underscores.")
        profiles, _ = self.profiles()
        if profiles.get(name, {}).get("oauth_provider"):
            raise Problem("Manage the OAuth model using the provider connection.")
        model = dict(profiles.get(name, {}))
        for field in ("model", "protocol", "chat_dialect", "base_url", "auth", "api_key_env"):
            if field in body:
                model[field] = str(body[field]).strip()
        if model.get("protocol") not in {"chat_completions", "responses", "anthropic", "bedrock"}:
            raise Problem("Unsupported protocol.")
        url = urllib.parse.urlsplit(model.get("base_url", ""))
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
            raise Problem("Enter the HTTP(S) server address without password in the URL.")
        if not model.get("model"):
            raise Problem("Enter the model name.")
        for field, default in (("context_window", 32768), ("max_output_tokens", 4096)):
            model[field] = int(body.get(field, model.get(field, default)))
        from apodex.switch_cli import workflow_settings

        try:
            workflow_settings({}, model)
        except ValueError as exc:
            raise Problem(str(exc)) from None
        if model.get("auth") not in {"none", "env"}:
            raise Problem("Invalid login method.")
        if model.get("auth") == "env" and not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]*", model.get("api_key_env", "")
        ):
            raise Problem("Enter the environment variable name for the API key.")
        with self.lock:
            extra = self.read_json(self.data / "models.json", {})
            extra[name] = model
            atomic_json(self.data / "models.json", extra)
        return {"id": name}

    def connect_model(self, body):
        provider = body.get("provider")
        if provider not in PROVIDERS:
            raise Problem("Unknown OAuth provider.")
        status = self.oauth.status(provider)
        model_id = str(body.get("model", ""))
        if not status["connected"] or model_id not in {m["id"] for m in status["models"]}:
            raise Problem("First, log in and select an available model.")
        name = provider + "-" + digest(model_id.encode())[:12]
        model = {
            "model": model_id,
            "oauth_provider": provider,
            "auth": "oauth",
            "backend": "codex" if provider == "chatgpt" else "frontier",
            "protocol": "codex" if provider == "chatgpt" else "anthropic",
            "base_url": "https://api.openai.com"
            if provider == "chatgpt"
            else "https://api.anthropic.com",
            "context_window": 32768,
            "max_output_tokens": 4096,
        }
        if provider == "claude_console":
            model["auth_profile"] = "switch-studio"
            # Models returned by Console include older generations. Do not
            # force adaptive thinking on models that do not support it.
            model["thinking"] = {"type": "disabled"}
        with self.lock:
            extra = self.read_json(self.data / "models.json", {})
            extra[name] = model
            atomic_json(self.data / "models.json", extra)
        return {"id": name}

    def disconnect_model(self, body):
        name = body.get("profile")
        profiles, _ = self.profiles()
        if not profiles.get(name, {}).get("oauth_provider"):
            raise Problem("This is not an OAuth model.")
        with self.lock:
            if any(r["status"] in ACTIVE and r["profile"] == name for r in self.runs.values()):
                raise Problem("First, stop the task running with this model.", 409)
            extra = self.read_json(self.data / "models.json", {})
            extra.pop(name, None)
            atomic_json(self.data / "models.json", extra)
        return {"ok": True}

    def probe(self, profile):
        profiles, _ = self.profiles()
        if profile not in profiles:
            raise Problem("Unknown model profile.")
        model = profiles[profile]
        if model.get("protocol") not in {"chat_completions", "responses"}:
            raise Problem("The /models endpoint is available for compatible OpenAI and Ollama APIs.")
        from dotenv import load_dotenv

        load_dotenv(self.config.parent / ".env", override=False)
        load_dotenv(ROOT / "frontier" / ".env", override=False)
        from apodex.switch_cli import credential_for

        try:
            key = credential_for(model)
        except ValueError:
            raise Problem("Missing environment variable for the API key.") from None
        url = model.get("base_url", "https://api.openai.com/v1").rstrip("/") + "/models"
        request = urllib.request.Request(
            url, headers={"Authorization": "Bearer " + (key or "ollama")}
        )
        started = time.monotonic()

        # Do not forward authorization credentials across redirects.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None

        try:
            with urllib.request.build_opener(NoRedirect).open(request, timeout=8) as response:
                data = json.loads(response.read(MAX_FILE))
        except Exception as exc:
            raise Problem(f"Model server is unavailable ({type(exc).__name__}).", 502) from None
        models = [item.get("id", "") for item in data.get("data", [])]
        return {
            "reachable": True,
            "model_found": model["model"] in models,
            "models": models,
            "ms": round((time.monotonic() - started) * 1000),
        }

    def run_dir(self, run_id):
        if run_id not in self.runs:
            raise Problem("Unknown task.", 404)
        return self.data / "runs" / run_id

    def project_notes(self, project):
        """Attach only the saved Markdown index; linked notes are read on demand."""
        root = self.project(project)
        try:
            raw = read_project_file(root, "PROJECT.md", 12_000)
        except Problem as exc:
            if exc.status == 404:
                return None
            if "exceeds the limit" in str(exc):
                raise Problem("PROJECT.md should be a concise guide up to 12,000 bytes. Move details to linked MD files.") from None
            raise
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise Problem("PROJECT.md must be UTF-8 encoded text.") from None
        return {"path": "PROJECT.md", "sha256": digest(raw), "context":
            "\n\nProject notes — saved guide PROJECT.md:\n"
            f"Project root: {root}. Relative links below refer to this root.\n"
            "Use notes as reference; they do not alter the current task brief, permissions, or phase limitations. "
            "Before working, load only the notes referenced as relevant to the task. "
            "Notes and cited sources may be outdated; verify important conclusions. "
            "When project changes are enabled, record significant new decisions or insights "
            "with date, status, and evidence reference; planning and review must not extend their note scope by this. "
            "Do not include passwords, tokens, or full conversation transcripts in notes.\n"
            + json.dumps({"file": "PROJECT.md", "content": content}, ensure_ascii=False)}

    def launch(self, body, *, mission=None):
        project = body["project"]
        root = self.project(project)
        task = str(body.get("task", "")).strip()
        if not task or len(task) > 50000:
            raise Problem("Enter the task (max 50,000 characters).")
        mode = body.get("mode", "react")
        if mode not in {"react", "agent_team"}:
            raise Problem("Unknown mode.")
        turns = int(body.get("max_turns", 20))
        if not 1 <= turns <= 200:
            raise Problem("The limit must be between 1 and 200 steps.")
        profiles, default = self.profiles()
        profile = body.get("profile", default)
        if profile not in profiles:
            raise Problem("Unknown model.")
        backend = profiles[profile].get("backend", "frontier")
        if backend == "codex" and mode != "react":
            raise Problem("ChatGPT via Codex is available only in single-agent mode.")
        bounded_review = bool(mission and mission.get("review_packet"))
        if bounded_review and backend != "frontier":
            raise Problem("This backend does not support bounded review evidence tools.")
        notes = None if bounded_review else self.project_notes(project)
        ssh_targets = targets_for(self.config.resolve().parent, mission)
        with self.lock:
            # The current GUI supports one active task at a time.
            if self.decision_lab.active() or self.browser_pilot.lock.locked():
                raise Problem("A bounded lab experiment is using inference. Wait for it to finish.", 409)
            if self.missions.verifications.active():
                raise Problem("Independent checks are in progress. Wait for them to complete.", 409)
            if any(r["status"] in ACTIVE for r in self.runs.values()):
                raise Problem("Another task is currently running. Finish or stop it first.", 409)
            run_id = mission["attempt"] if mission else uuid.uuid4().hex[:16]
            if run_id in self.runs:
                raise Problem("This run has already been created.", 409)
            directory = self.data / "runs" / run_id
            directory.mkdir(parents=True)
            config = directory / "agent.toml"
            write_config(config, {profile: profiles[profile]}, profile)
            run = {
                "id": run_id,
                "project": project,
                "task": task,
                "mode": mode,
                "profile": profile,
                "model": profiles[profile]["model"],
                "max_turns": turns,
                "created": time.time(),
                "status": "running",
                "auto_approve": bool(body.get("auto_approve", False)),
                "backend": backend,
                "oauth_provider": profiles[profile].get("oauth_provider"),
                **({"project_notes": {k: notes[k] for k in ("path", "sha256")}} if notes else {}),
                **({"mission": mission} if mission else {}),
            }
            atomic_json(
                directory / "request.json",
                {
                    **run,
                    "task": task + (notes["context"] if notes else ""),
                    "cwd": str(root),
                    "config": str(config),
                    "env_file": str(self.config.parent / ".env"),
                    "oauth_config_dir": str(self.data / "anthropic"),
                    "ssh_targets": ssh_targets,
                    **({"review_objects": str(self.missions.versions.objects)} if bounded_review else {}),
                },
            )
            self.runs[run_id] = run
            atomic_json(directory / "run.json", run)
            log = (directory / "console.log").open("wb")
            try:
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-u",
                        str(ROOT / "studio" / "worker.py"),
                        str(directory),
                    ],
                    cwd=ROOT,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            except OSError:
                run.update(status="failed", ended=time.time())
                atomic_json(directory / "run.json", run)
                raise
            finally:
                log.close()
            self.processes[run_id] = process
            try:
                atomic_json(directory / "processes.json", self.process_tree(process).identities())
                (directory / "activated").touch()
            except Exception:
                self.process_tree(process).finish()
                process.wait(timeout=3)
                self.processes.pop(run_id, None)
                run.update(status="failed", ended=time.time(), reason="Cannot safely save worker identity.")
                atomic_json(directory / "run.json", run)
                raise
            process._studio_watcher = threading.Thread(
                target=self.watch, args=(run_id, process), daemon=True
            )
            process._studio_watcher.start()
        return dict(run)

    def events(self, run_id, offset=0):
        path = self.run_dir(run_id) / "events.jsonl"
        events = []
        next_offset = offset
        if path.exists():
            with path.open("rb") as stream:
                stream.seek(max(0, offset))
                for _ in range(600):
                    line = stream.readline()
                    if not line or not line.endswith(b"\n"):
                        break
                    try:
                        events.append(json.loads(line))
                    except ValueError:
                        pass
                    next_offset = stream.tell()
        return {
            "events": events,
            "offset": next_offset,
            "run": dict(self.runs[run_id]),
            "approvals": self.approvals(run_id),
        }

    def approvals(self, run_id):
        directory = self.run_dir(run_id)
        return (
            [
                self.read_json(p, {})
                for p in sorted(directory.glob("approval-*.json"))
                if not (directory / p.name.replace("approval-", "decision-")).exists()
            ]
            if self.runs[run_id]["status"] in ACTIVE
            else []
        )

    def approval_wait_seconds(self, run_id, now):
        """Exclude persisted human-wait intervals from the worker time budget."""
        directory = self.run_dir(run_id)
        intervals = []
        for path in directory.glob("approval-*.json"):
            decision = directory / path.name.replace("approval-", "decision-")
            start = max(self.runs[run_id]["created"], path.stat().st_mtime)
            end = min(now, decision.stat().st_mtime if decision.exists() else now)
            if end > start:
                intervals.append((start, end))
        total, previous_end = 0, 0
        for start, end in sorted(intervals):
            total += max(0, end - max(start, previous_end))
            previous_end = max(previous_end, end)
        return total

    def decide(self, body):
        directory = self.run_dir(body["run"])
        approval = body.get("approval", "")
        if not re.fullmatch(r"[a-f0-9]{32}", approval):
            raise Problem("Invalid approval.")
        path = directory / f"approval-{approval}.json"
        if not path.exists() or self.runs[body["run"]]["status"] not in ACTIVE:
            raise Problem("Approval is no longer active.", 409)
        request = self.read_json(path, {})
        allow = body.get("allow") is True
        if allow and request.get("dangerous") and body.get("confirmation") != "yes":
            raise Problem("Type yes to confirm this action.")
        with self.lock:
            decision = directory / f"decision-{approval}.json"
            if decision.exists():
                raise Problem("A decision on this action has already been made.", 409)
            atomic_json(
                decision, {"allow": allow, "feedback": str(body.get("feedback", ""))[:10000]}
            )
        return {"ok": True}

    def watch(self, run_id, process):
        tree = self.process_tree(process)
        last_checkpoint = 0
        checkpoint_error = ""
        while process.poll() is None:
            tree.refresh()
            if time.monotonic() - last_checkpoint >= 1:
                try:
                    atomic_json(self.run_dir(run_id) / "processes.json", tree.identities())
                except OSError as exc:
                    checkpoint_error = f"Cannot save process checkpoint: {exc}"
                    tree.finish()
                    break
                last_checkpoint = time.monotonic()
            time.sleep(0.05)
        code = process.wait()
        cleaned = tree.finish()
        with self.lock:
            run = self.runs[run_id]
            result = self.read_json(self.run_dir(run_id) / "result.json", {})
            status = (
                "cancelled"
                if run["status"] == "stopping"
                else (result.get("status", "failed") if code == 0 else "failed")
            )
            run.update(
                status=status,
                ended=time.time(),
                exit_code=code,
                **{k: result[k] for k in ("session_id", "reason", "usage", "review_reads") if k in result},
            )
            if not cleaned:
                run.update(status="failed", reason="Failed to terminate all subprocesses.")
            self.processes.pop(run_id, None)
            if checkpoint_error:
                run.update(status="failed", reason=checkpoint_error)
            atomic_json(self.run_dir(run_id) / "run.json", run)

    def stop(self, run_id):
        self.run_dir(run_id)
        with self.lock:
            process = self.processes.get(run_id)
            if process and self.runs[run_id]["status"] != "stopping":
                self.runs[run_id]["status"] = "stopping"
                atomic_json(self.run_dir(run_id) / "run.json", self.runs[run_id])
                self.process_tree(process).stop()
                threading.Thread(target=self.kill_later, args=(process,), daemon=True).start()
        return {"ok": True}

    def process_tree(self, process):
        with self.lock:
            if not hasattr(process, "_studio_tree"):
                process._studio_tree = ProcessTree(process.pid)
            return process._studio_tree

    def kill_later(self, process):
        self.process_tree(process).finish()
        watcher = getattr(process, "_studio_watcher", None)
        if watcher and watcher is not threading.current_thread():
            watcher.join(timeout=3)

    def artifacts(self, run_id):
        run = self.runs.get(run_id)
        if not run:
            raise Problem("Unknown task.", 404)
        if run.get("backend") == "codex":
            root = self.project(run["project"])
            paths = set()
            offset = 0
            event_file = self.run_dir(run_id) / "events.jsonl"
            end = event_file.stat().st_size if event_file.exists() else 0
            while offset < end:
                page = self.events(run_id, offset)
                for event in page["events"]:
                    if event.get("type") == "changed_files":
                        paths.update(p for p in event.get("paths", []) if isinstance(p, str))
                if page["offset"] == offset:
                    break
                offset = page["offset"]
            files = []
            for raw in sorted(paths):
                try:
                    path = Path(raw)
                    relative = str(path.relative_to(root)) if path.is_absolute() else str(path)
                    target = file_target(root, relative)
                    if target.is_file():
                        files.append(
                            {"path": relative, "name": relative, "size": target.stat().st_size}
                        )
                except (Problem, ValueError, OSError):
                    continue
            return files[:300]
        result = self.read_json(self.run_dir(run_id) / "result.json", {})
        session = result.get("session_id")
        if not session:
            # Session event arrives immediately, long before the final result.
            for event in self.events(run_id)["events"]:
                if event.get("type") == "session":
                    session = event.get("session_id")
                    break
        if not session or not re.fullmatch(r"[\w+.-]+", session):
            return []
        root = self.project(run["project"])
        folder = root / ".apodex" / "runs" / session / "outputs"
        return (
            [
                {
                    "path": str(p.relative_to(root)),
                    "name": str(p.relative_to(folder)),
                    "size": p.stat().st_size,
                }
                for p in folder.rglob("*")
                if p.is_file() and not p.is_symlink()
            ][:300]
            if folder.is_dir()
            else []
        )


class Handler(BaseHTTPRequestHandler):
    server_version = "SwitchStudio/0.1"

    @property
    def studio(self):
        return self.server.studio

    def log_message(self, *args):
        pass

    def send(
        self, value, status=200, content_type="application/json; charset=utf-8", filename=None
    ):
        data = (
            json.dumps(value, ensure_ascii=False).encode()
            if isinstance(value, (dict, list))
            else value
        )
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        if filename:
            self.send_header(
                "Content-Disposition",
                "attachment; filename*=UTF-8''" + urllib.parse.quote(filename),
            )
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
            f"frame-src http://127.0.0.1:* http://localhost:* http://{self.server.server_address[0]}:*; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        )
        self.end_headers()
        self.wfile.write(data)

    def check_origin(self, mutation=False):
        port = self.server.server_port
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        hosts.add(f"{self.server.server_address[0]}:{port}")
        if self.headers.get("Host") not in hosts:
            raise Problem("Unauthorized host.", 403)
        origin = self.headers.get("Origin")
        if origin and origin not in {"http://" + h for h in hosts}:
            raise Problem("Unauthorized request origin.", 403)
        if mutation and not secrets.compare_digest(
            self.headers.get("X-Studio-Token", ""), self.studio.token
        ):
            raise Problem("Refresh the page; session expired.", 403)

    def do_GET(self):
        self.handle_request(False)

    def do_POST(self):
        self.handle_request(True)

    def handle_request(self, mutation):
        try:
            self.check_origin(mutation)
            parsed = urllib.parse.urlsplit(self.path)
            q = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
            path = parsed.path
            if mutation:
                if not self.headers.get("Content-Type", "").startswith("application/json"):
                    raise Problem("JSON expected.", 415)
                size = int(self.headers.get("Content-Length", 0))
                if not 0 < size <= MAX_FILE * 2:
                    raise Problem("Request too large.", 413)
                body = json.loads(self.rfile.read(size))
                result = self.post(path, body)
            elif path == "/api/state":
                result = self.studio.public_state()
            elif path == "/api/preview":
                result = self.studio.preview.status()
            elif path == "/api/templates/company":
                result = self.studio.company_template(q["project"])
            elif path == "/api/missions":
                result = {"missions": self.studio.missions.list(q.get("project")),
                          "controller_error": self.studio.missions.last_error,
                          "capabilities": self.studio.missions.capabilities()}
            elif path == "/api/verification":
                result = self.studio.missions.verifications.get(q["id"])
            elif path == "/api/mission-trace":
                result = {"events": self.studio.missions.trace(q["id"])}
            elif path == "/api/decision-lab":
                result = {"evaluations": self.studio.decision_lab.list(q["project"])}
            elif path == "/api/decision-metrics":
                result = self.studio.missions.decisions.metrics(q["id"])
            elif path == "/api/deployments":
                result = {"deployments": self.studio.deployments.list(q.get("product"))}
            elif path == "/api/companies":
                result = self.studio.companies.snapshot()
            elif path == "/api/companies/report":
                result = self.studio.companies.report(q["id"])
            elif path == "/api/products":
                result = {"products": self.studio.products.list(q.get("project")),
                          "kinds": KINDS, "controller_error": self.studio.products.last_error}
            elif path == "/api/tree":
                result = self.studio.tree(q["project"], q.get("path", ""))
            elif path == "/api/file":
                result = self.studio.read_file(q["project"], q["path"])
            elif path == "/api/download":
                return self.send(
                    read_project_file(self.studio.project(q["project"]), q["path"], 50_000_000),
                    content_type="application/octet-stream",
                    filename=Path(q["path"]).name,
                )
            elif path == "/api/events":
                result = self.studio.events(q["run"], int(q.get("offset", 0)))
            elif path == "/api/artifacts":
                result = self.studio.artifacts(q["run"])
            elif path == "/api/log":
                logfile = self.studio.run_dir(q["run"]) / "console.log"
                text = ""
                if logfile.exists():
                    with logfile.open("rb") as stream:
                        stream.seek(max(0, logfile.stat().st_size - 100000))
                        text = stream.read(100000).decode("utf-8", errors="replace")
                result = {"text": text}
            elif path in {"/", "/app.js", "/missions.js", "/products.js", "/preview.js", "/companies.js", "/help.js", "/office.js", "/decision-lab.js", "/browser-pilot.js", "/office.css", "/style.css", "/dashboard.js", "/dashboard.css"}:
                target = STATIC / ("index.html" if path == "/" else path.lstrip("/"))
                return self.send(
                    target.read_bytes(),
                    content_type=(mimetypes.guess_type(target)[0] or "text/plain")
                    + "; charset=utf-8",
                )
            else:
                raise Problem("Not found.", 404)
            self.send(result)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Problem as exc:
            self.send({"error": str(exc)}, exc.status)
        except (KeyError, ValueError, TypeError) as exc:
            self.send({"error": f"Invalid request: {exc}"}, 400)
        except (OSError, RuntimeError) as exc:
            self.send({"error": str(exc)}, 500)

    def post(self, path, body):
        if path == "/api/preview/start":
            return self.studio.preview.start(body, self.server.server_port, self.server.server_address[0])
        if path == "/api/preview/stop":
            if not isinstance(body.get("id"), str) or not body["id"]:
                raise Problem("Preview identity missing.")
            return self.studio.preview.stop(body["id"])
        if path == "/api/preview/starter":
            return self.studio.preview.install(body)
        if path == "/api/oauth/status":
            return self.studio.oauth.status(body["provider"])
        if path == "/api/oauth/start":
            return self.studio.oauth.start(body["provider"])
        if path == "/api/oauth/cancel":
            return self.studio.oauth.cancel(body["provider"])
        if path == "/api/oauth/models":
            return self.studio.connect_model(body)
        if path == "/api/oauth/disconnect":
            return self.studio.disconnect_model(body)
        if path == "/api/projects":
            return self.studio.add_project(body["path"])
        if path == "/api/file":
            return self.studio.save_file(body)
        if path == "/api/templates/company/install":
            return self.studio.install_company_template(body)
        if path == "/api/missions":
            return self.studio.missions.create(body)
        if path == "/api/missions/action":
            return self.studio.missions.action(body)
        if path == "/api/decision-lab":
            return self.studio.decision_lab.start(body)
        if path == "/api/decision-lab/export":
            return self.studio.decision_lab.export(body["id"])
        if path == "/api/browser-pilot":
            return self.studio.browser_pilot.choose(body)
        if path == "/api/companies":
            return self.studio.companies.create(body)
        if path == "/api/companies/action":
            return self.studio.companies.action(body)
        if path == "/api/products":
            return self.studio.products.create(body)
        if path == "/api/products/action":
            return self.studio.products.action(body)
        if path == "/api/models":
            return self.studio.save_model(body)
        if path == "/api/probe":
            return self.studio.probe(body["profile"])
        if path == "/api/runs":
            return self.studio.launch(body)
        if path == "/api/stop":
            return self.studio.stop(body["run"])
        if path == "/api/decision":
            return self.studio.decide(body)
        raise Problem("Not found.", 404)


def main():
    parser = argparse.ArgumentParser(description="Switch Studio — local agent IDE")
    parser.add_argument("--port", type=int, default=4317)
    parser.add_argument("--host", type=ipaddress.IPv4Address, default="127.0.0.1",
                        help="IPv4 interface to listen on (use the server LAN IP for remote access)")
    parser.add_argument("--cwd", type=Path, default=ROOT)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()
    host = str(args.host)
    if args.host.is_unspecified or args.host.is_multicast:
        parser.error("--host requires a concrete interface IPv4 address")
    state_dir = ROOT / ".switch-agent" / "studio"
    state_dir.mkdir(parents=True, exist_ok=True)
    instance_lock = (state_dir / "server.lock").open("a")
    try:
        fcntl.flock(instance_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        saved = Studio.read_json(state_dir / "server.json", {})
        port = saved.get("port", args.port)
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise SystemExit("Switch Studio is already running.") from None
        url = f"http://{saved.get('host', '127.0.0.1')}:{port}"
        print(f"Switch Studio is already running: {url}")
        if not args.no_open:
            open_browser(url)
        return
    try:
        server = ThreadingHTTPServer((host, args.port), Handler)
    except OSError as exc:
        raise SystemExit(f"Port {args.port} is unavailable: {exc}. Try --port 4318.") from None
    server.studio = Studio(args.cwd)
    server.studio.missions.start()
    atomic_json(state_dir / "server.json", {"host": host, "port": server.server_port, "pid": os.getpid()})
    url = f"http://{host}:{server.server_port}"
    print(f"Switch Studio: {url}", flush=True)
    if not args.no_open:
        open_browser(url)
    def terminate(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, terminate)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.studio.preview.stop()
        server.studio.missions.close()
        server.studio.deployments.close()
        server.studio.oauth.close()
        for run_id in list(server.studio.processes):
            server.studio.stop(run_id)
        for process in list(server.studio.processes.values()):
            server.studio.kill_later(process)
        server.server_close()


if __name__ == "__main__":
    main()
