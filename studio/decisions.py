"""Optional bounded task selection, always inside deterministic eligibility gates."""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import urllib.request
import uuid
from pathlib import Path


def load_policy(path):
    document = Path(path).read_text(encoding="utf-8")
    blocks = re.findall(r"```json\s*\n(.*?)\n```", document, re.S)
    if len(blocks) != 1:
        raise ValueError("Decision policy must contain exactly one JSON block.")
    policy = json.loads(blocks[0])
    if set(policy) != {"version", "question", "confidence_threshold", "timeout_seconds", "max_output_tokens", "frame_chars"}:
        raise ValueError("Invalid decision policy items.")
    if (policy["version"] != 1 or not isinstance(policy["question"], str) or not 1 <= len(policy["question"]) <= 2000
            or not 0 <= policy["confidence_threshold"] <= 1 or not 1 <= policy["timeout_seconds"] <= 10
            or not 64 <= policy["max_output_tokens"] <= 1000 or not 1000 <= policy["frame_chars"] <= 20000):
        raise ValueError("Decision policy contains values outside the allowed range.")
    policy["sha256"] = hashlib.sha256(document.encode()).hexdigest()
    return policy


def choose_action(answer, candidates, threshold):
    if not isinstance(answer, dict) or answer.get("action") not in candidates:
        raise ValueError("Model zvolil nepovolenou akci.")
    confidence = answer.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not threshold <= confidence <= 1:
        raise ValueError("Low or invalid confidence of the decision model.")
    if not isinstance(answer.get("reason"), str) or not answer["reason"].strip():
        raise ValueError("Model did not provide a reason for selection.")
    return answer["action"]


class Decisions:
    def __init__(self, missions):
        self.missions = missions
        self.studio = missions.studio
        self.policy = load_policy(Path(__file__).parent / "templates" / "decision-policy.md")
        self.threads = []
        with missions.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS decisions (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
            records = [json.loads(r[0]) for r in db.execute("SELECT data FROM decisions")]
        for record in records:
            if record["status"] == "running":
                record.update(status="fallback", reason="Decision was interrupted by restart.", ended=time.time())
                self.save(record)

    def save(self, record):
        with self.missions.connect() as db:
            db.execute("INSERT INTO decisions VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                       (record["id"], json.dumps(record, ensure_ascii=False)))

    def get(self, key):
        with self.missions.connect() as db:
            row = db.execute("SELECT data FROM decisions WHERE id=?", (key,)).fetchone()
        if not row:
            raise ValueError("Decision does not exist.")
        return json.loads(row[0])

    def select(self, m, candidates):
        """Return an eligible tuple, or None while the optional request is pending."""
        profile = m.get("decision_profile")
        if not profile or len(candidates) < 2:
            return candidates[0]
        actions = {f"{phase}:{task}": (phase, task) for phase, task in candidates}
        frame = {"goal": m["goal"][:1500], "attempts": len(m["attempts"]), "actions": [
            {"action": action, "title": next(t["title"] for t in m["tasks"] if t["id"] == task),
             "criteria": next(t["criteria"] for t in m["tasks"] if t["id"] == task)[:2]}
            for action, (_, task) in actions.items()], "completed": [t["id"] for t in m["tasks"] if t["status"] == "done"]}
        wire = json.dumps(frame, ensure_ascii=False)
        signature = hashlib.sha256((profile + self.policy["sha256"] + wire).encode()).hexdigest()
        if len(wire) > self.policy["frame_chars"]:
            m["decision"] = {"status": "fallback", "reason": "Decision framework exceeded allowed length."}
            return candidates[0]
        key = m.get("decision_request")
        record = self.get(key) if key else None
        if record and record["signature"] == signature:
            if record["status"] == "running":
                if time.time() - record["started"] < self.policy["timeout_seconds"] + 2:
                    return None
                record.update(status="fallback", reason="Decision limit expired.", ended=time.time())
                self.save(record)
            m["decision"] = {k: record.get(k) for k in ("id", "status", "reason", "usage", "elapsed_seconds", "choice", "policy_sha256")}
            # Revalidate against CURRENT eligibility; a cached reply grants no permissions.
            return actions.get(record.get("choice"), candidates[0])
        record = {"id": uuid.uuid4().hex[:16], "mission": m["id"], "profile": profile,
                  "signature": signature, "frame": frame, "policy_sha256": self.policy["sha256"],
                  "started": time.time(), "status": "running", "choice": next(iter(actions))}
        self.save(record)
        m["decision_request"] = record["id"]
        m["message"] = "Optional model selects the next ready task."
        self.missions.save(m)
        thread = threading.Thread(target=self.request, args=(record,), daemon=True, name="studio-decision")
        self.threads.append(thread)
        thread.start()
        return None

    def request(self, record):
        started = time.monotonic()
        try:
            from dotenv import dotenv_values
            profiles, _ = self.studio.profiles()
            profile = profiles[record["profile"]]
            if profile.get("protocol") != "chat_completions" or profile.get("oauth_provider"):
                raise ValueError("Decision pilot requires a profile compatible with chat API without OAuth.")
            endpoint = profile["base_url"].rstrip("/") + "/chat/completions"
            env = {**dotenv_values(self.studio.config.parent / "frontier" / ".env"),
                   **dotenv_values(self.studio.config.parent / ".env"), **os.environ}
            headers = {"Content-Type": "application/json"}
            if profile.get("auth") == "env":
                key = env.get(profile.get("api_key_env", ""))
                if not key:
                    raise ValueError("Missing decision model authentication.")
                headers["Authorization"] = "Bearer " + key
            payload = {"model": profile["model"], "messages": [
                {"role": "system", "content": self.policy["question"]},
                {"role": "user", "content": json.dumps(record["frame"], ensure_ascii=False)}],
                "temperature": 0, "max_tokens": self.policy["max_output_tokens"], "stream": False}
            request = urllib.request.Request(endpoint, data=json.dumps(payload).encode(), headers=headers, method="POST")
            with urllib.request.urlopen(request, timeout=self.policy["timeout_seconds"]) as response:
                raw = response.read(65537)
            if len(raw) > 65536:
                raise ValueError("Decision model response exceeded the limit.")
            result = json.loads(raw)
            record["usage"] = result.get("usage")
            answer = json.loads(result["choices"][0]["message"]["content"])
            choice = choose_action(answer, [a["action"] for a in record["frame"]["actions"]], self.policy["confidence_threshold"])
            record.update(status="selected", choice=choice, reason=answer["reason"][:1500],
                          confidence=answer["confidence"], usage=result.get("usage"))
        except Exception as exc:
            record.update(status="fallback", reason=str(exc)[:1500])
        record.update(ended=time.time(), elapsed_seconds=time.monotonic() - started)
        # A timed-out reply may finish late. Keep the controller's fallback decision.
        with self.missions.lock:
            if self.get(record["id"])["status"] == "running":
                self.save(record)

    def close(self):
        for thread in self.threads:
            thread.join(timeout=self.policy["timeout_seconds"] + 1)
