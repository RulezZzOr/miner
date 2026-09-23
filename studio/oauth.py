"""Optional provider login via official CLIs. Tokens never pass through the UI."""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
PROVIDERS = {"chatgpt", "claude_console"}


def login_url(value):
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname
        not in {"auth.openai.com", "chatgpt.com", "platform.claude.com", "console.anthropic.com"}
        or parsed.username
        or parsed.password
    ):
        raise RuntimeError("Poskytovatel vrátil neočekávanou přihlašovací adresu.")
    return value


def anthropic_env(directory):
    # No ambient API key, federation or endpoint override may shadow this login.
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC_")}
    env["ANTHROPIC_CONFIG_DIR"] = str(directory)
    return env


class CodexRPC:
    """Multiplexed JSONL app-server connection, including server requests."""

    def __init__(self, command=None):
        binary = shutil.which("codex")
        if command is None and not binary:
            raise RuntimeError("Chybí Codex CLI. Nainstaluj jej a znovu spusť Studio.")
        self.lock = threading.RLock()
        self.pending = {}
        self.events = queue.Queue()
        self.sequence = 0
        self.closed = False
        self.process = subprocess.Popen(
            command or [binary, "app-server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        try:
            self.request(
                "initialize",
                {
                    "clientInfo": {
                        "name": "switch_studio",
                        "title": "Switch Studio",
                        "version": "0.2.0",
                    }
                },
            )
            self.send({"method": "initialized", "params": {}})
        except BaseException:
            self.close()
            raise

    def send(self, message):
        with self.lock:
            if self.closed or self.process.poll() is not None:
                raise RuntimeError("Spojení s Codex se ukončilo.")
            self.process.stdin.write(json.dumps(message) + "\n")
            self.process.stdin.flush()

    def _read(self):
        try:
            for line in self.process.stdout:
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                with self.lock:
                    pending = (
                        self.pending.get(message.get("id")) if "method" not in message else None
                    )
                if pending:
                    pending.put(message)
                else:
                    self.events.put(message)
        finally:
            with self.lock:
                self.closed = True
                for pending in self.pending.values():
                    pending.put({"error": {"message": "Codex App Server skončil."}})
            self.events.put({"method": "studio/disconnected"})

    def request(self, method, params, timeout=30):
        with self.lock:
            self.sequence += 1
            key = self.sequence
            pending = self.pending[key] = queue.Queue()
        try:
            self.send({"id": key, "method": method, "params": params})
            response = pending.get(timeout=timeout)
            if "error" in response:
                # Avoid forwarding provider error payloads that might contain credentials.
                raise RuntimeError(
                    f"Codex odmítl požadavek {method}. Zkontroluj přihlášení a verzi CLI."
                )
            return response.get("result", {})
        except queue.Empty:
            raise RuntimeError(f"Codex neodpověděl na {method} včas.") from None
        finally:
            with self.lock:
                self.pending.pop(key, None)

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        if self.process.stdin:
            self.process.stdin.close()
        self.reader.join(timeout=2)
        if not self.reader.is_alive():
            self.process.stdout.close()


class OAuthConnections:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.lock = threading.RLock()
        self.jobs = {}
        self.codex = None

    def rpc(self):
        with self.lock:
            if self.codex is None or self.codex.closed:
                self.codex = CodexRPC()
            return self.codex

    def ant(self):
        binary = ROOT / ".switch-agent" / "tools" / "ant"
        found = str(binary) if binary.is_file() else shutil.which("ant")
        if not found:
            raise RuntimeError(
                "Chybí Anthropic CLI (ant). Instalace je popsaná ve studio/README.md."
            )
        return [found, "--profile", "switch-studio"]

    def status(self, provider):
        if provider not in PROVIDERS:
            raise ValueError("Neznámý poskytovatel OAuth.")
        with self.lock:
            job = self.jobs.get(provider)
            pending = bool(job and job["status"] == "waiting")
            login = (
                {k: job[k] for k in ("id", "status", "url", "message") if k in job} if job else None
            )
        models = []
        connected = False
        message = "Nepřipojeno"
        if provider == "chatgpt":
            rpc = self.rpc()
            account = rpc.request("account/read", {"refreshToken": False}).get("account") or {}
            connected = account.get("type") == "chatgpt"
            if connected:
                cursor = None
                for _ in range(10):
                    page = rpc.request("model/list", {"cursor": cursor} if cursor else {})
                    models.extend(
                        {"id": m["id"], "name": m.get("displayName", m["id"])}
                        for m in page.get("data", [])
                    )
                    cursor = page.get("nextCursor")
                    if not cursor:
                        break
                message = "Přihlášeno přes Codex · " + str(account.get("planType") or "ChatGPT")
        elif not pending:
            config = self.directory / "anthropic"
            if (config / "configs" / "switch-studio.json").exists():
                try:
                    result = subprocess.run(
                        self.ant() + ["--format", "json", "models", "list"],
                        env=anthropic_env(config),
                        capture_output=True,
                        text=True,
                        timeout=20,
                    )
                    if result.returncode == 0:
                        payload = json.loads(result.stdout)
                        rows = payload.get("data", []) if isinstance(payload, dict) else payload
                        models = [
                            {"id": m["id"], "name": m.get("display_name", m["id"])} for m in rows
                        ]
                        connected = True
                        message = "Přihlášeno · Claude Console · účtování API"
                    else:
                        message = "Přihlášení nebo ověření modelů selhalo. Zkus přihlášení znovu."
                except (subprocess.TimeoutExpired, ValueError):
                    message = "Claude Console nyní neodpovídá. Zkus obnovit stav."
        return {
            "provider": provider,
            "connected": connected,
            "models": models,
            "message": message,
            "login": login,
        }

    def start(self, provider):
        if provider not in PROVIDERS:
            raise ValueError("Neznámý poskytovatel OAuth.")
        with self.lock:
            old = self.jobs.get(provider)
            if old and old["status"] == "waiting":
                return {"id": old["id"], "status": "waiting", "url": old.get("url")}
            job = {"id": uuid.uuid4().hex, "status": "waiting", "started": time.monotonic()}
            self.jobs[provider] = job
            if provider == "chatgpt":
                try:
                    result = self.rpc().request("account/login/start", {"type": "chatgpt"})
                    job.update(login_id=result["loginId"], url=login_url(result["authUrl"]))
                except Exception:
                    job["status"] = "failed"
                    raise
                threading.Thread(target=self._codex_login, args=(job,), daemon=True).start()
            else:
                config = self.directory / "anthropic"
                config.mkdir(parents=True, exist_ok=True, mode=0o700)
                try:
                    job["process"] = subprocess.Popen(
                        # Explicit callback-port keeps the CLI's loopback flow
                        # while the GUI (rather than the CLI) opens the browser.
                        self.ant()
                        + [
                            "auth",
                            "login",
                            "--no-browser",
                            "--callback-port",
                            "0",
                            "--timeout",
                            "5m",
                        ],
                        env=anthropic_env(config),
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        stdin=subprocess.DEVNULL,
                        text=True,
                        bufsize=1,
                    )
                except Exception:
                    job["status"] = "failed"
                    raise
                threading.Thread(target=self._claude_login, args=(job,), daemon=True).start()
            return {"id": job["id"], "status": job["status"], "url": job.get("url")}

    def _codex_login(self, job):
        rpc = self.rpc()
        while job["status"] == "waiting" and time.monotonic() - job["started"] < 300:
            try:
                event = rpc.events.get(timeout=1)
            except queue.Empty:
                continue
            if event.get("method") == "account/login/completed" and event.get("params", {}).get(
                "loginId"
            ) == job.get("login_id"):
                job["status"] = "completed" if event["params"].get("success") else "failed"
            elif event.get("method") == "studio/disconnected":
                job["status"] = "failed"
            elif "id" in event and "method" in event:
                rpc.send(
                    {
                        "id": event["id"],
                        "error": {"code": -32601, "message": "Unsupported during login"},
                    }
                )
        if job["status"] == "waiting":
            self.cancel("chatgpt")
            job["message"] = "Přihlášení vypršelo. Spusť jej znovu."

    def _claude_login(self, job):
        process = job["process"]
        try:
            for line in process.stdout:
                for candidate in re.findall(r"https://[^\s\x1b]+", line):
                    try:
                        job["url"] = login_url(candidate)
                    except RuntimeError:
                        continue
            code = process.wait()
            if job["status"] == "waiting":
                job["status"] = "completed" if code == 0 else "failed"
        finally:
            process.stdout.close()

    def cancel(self, provider):
        with self.lock:
            job = self.jobs.get(provider)
            if not job or job["status"] != "waiting":
                return {"ok": True}
            job["status"] = "cancelled"
            if provider == "chatgpt":
                self.rpc().request("account/login/cancel", {"loginId": job["login_id"]})
            elif job.get("process") and job["process"].poll() is None:
                job["process"].terminate()
        return {"ok": True}

    def close(self):
        for provider in PROVIDERS:
            try:
                self.cancel(provider)
            except (OSError, RuntimeError):
                pass
        if self.codex:
            self.codex.close()
