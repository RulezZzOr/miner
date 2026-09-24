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


try:
    from .judgments import provider_config, request_choice, validate_choice
except ImportError:
    from judgments import provider_config, request_choice, validate_choice

choose_action = validate_choice


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


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

    def metrics(self, mission):
        self.missions.get(mission)
        with self.missions.connect() as db:
            records = [json.loads(r[0]) for r in db.execute('SELECT data FROM decisions')]
        records = [r for r in records if r['mission'] == mission]
        completed = [r for r in records if r['status'] in {'selected', 'consumed', 'shadow'}]
        compared = [r for r in completed if r.get('baseline')]
        elapsed = sorted(r['elapsed_seconds'] for r in records if isinstance(r.get('elapsed_seconds'), (int, float)))
        return {'requests': len(records), 'valid_proposals': len(completed),
                'fallbacks': sum(r['status'] == 'fallback' for r in records),
                'superseded': sum(r['status'] == 'superseded' for r in records),
                'comparisons': len(compared), 'disagreements': sum(r['choice'] != r['baseline'] for r in compared),
                'median_seconds': elapsed[len(elapsed)//2] if elapsed else None,
                'note': 'Proposal agreement and latency only. No ground-truth model accuracy is measured.'}

    def summary(self, record):
        return {k: record.get(k) for k in ('id', 'status', 'mode', 'provider', 'model', 'reason', 'usage',
            'elapsed_seconds', 'choice', 'baseline', 'confidence', 'policy_sha256', 'signature', 'alternatives')}

    def select(self, m, candidates):
        """A reply proposes priority only. Current deterministic gates own execution."""
        profile = m.get('decision_profile')
        mode = m.get('decision_mode', 'select' if profile else 'off')
        key = m.get('decision_request')
        record = self.get(key) if key else None
        if mode == 'off' or not profile or len(candidates) < 2:
            if record and record['status'] == 'running':
                record.update(status='superseded', reason='Decision no longer needed.', ended=time.time())
                self.save(record)
            return candidates[0]
        actions = {f'{phase}:{task}': (phase, task) for phase, task in candidates}
        frame = {'goal': m['goal'][:1500], 'goal_truncated': len(m['goal']) > 1500,
                 'attempts': len(m['attempts']), 'actions': [
            {'action': action, 'title': next(t['title'] for t in m['tasks'] if t['id'] == task),
             'criteria': next(t['criteria'] for t in m['tasks'] if t['id'] == task)[:2]}
            for action, (_, task) in actions.items()], 'completed': [t['id'] for t in m['tasks'] if t['status'] == 'done']}
        try:
            profiles, _ = self.studio.profiles()
            config = provider_config(profiles, profile)
        except (ValueError, KeyError) as exc:
            m['decision'] = {'status': 'fallback', 'mode': mode, 'reason': str(exc)}
            return candidates[0]
        # Full authoritative task data binds the compact model frame. Truncation
        # affects only the input size, never freshness validation.
        signature = fingerprint({'profile': config, 'policy': self.policy['sha256'], 'mode': mode,
            'goal': m['goal'], 'constraints': m['constraints'], 'criteria': m['criteria'],
            'tasks': m['tasks'], 'questions': m['questions'], 'deadline': m.get('deadline'),
            'attempts': [a['id'] for a in m['attempts']], 'actions': list(actions)})
        if len(json.dumps(frame, ensure_ascii=False)) > self.policy['frame_chars']:
            m['decision'] = {'status': 'fallback', 'mode': mode, 'reason': 'Decision frame exceeded the input budget.'}
            return candidates[0]
        if record and record['signature'] == signature:
            if record['status'] == 'running':
                if time.time() < record['expires']:
                    m['decision'] = self.summary(record)
                    return candidates[0] if mode == 'shadow' else None
                record.update(status='fallback', reason='Decision deadline expired.', ended=time.time())
                self.save(record)
            m['decision'] = self.summary(record)
            if mode == 'shadow' or record['status'] != 'selected':
                return candidates[0]
            # One consumption; if launch fails a fresh attempt changes the state.
            choice = actions.get(record.get('choice'), candidates[0])
            record.update(status='consumed', consumed_at=time.time())
            self.save(record)
            return choice
        if record and record['status'] == 'running':
            record.update(status='superseded', reason='State changed before the reply; deterministic order used.', ended=time.time())
            self.save(record)
        self.threads = [t for t in self.threads if t.is_alive()]
        if self.threads:
            m['decision'] = {'status': 'fallback', 'mode': mode, 'reason': 'A previous request is still finishing; no overlapping inference.'}
            return candidates[0]
        record = {'id': uuid.uuid4().hex[:16], 'mission': m['id'], 'profile': profile,
                  'mode': mode, 'provider': config['provider'], 'model': config['model'],
                  'config_sha256': fingerprint(config), 'signature': signature, 'frame': frame,
                  'policy_sha256': self.policy['sha256'], 'started': time.time(),
                  'expires': time.time() + self.policy['timeout_seconds'], 'status': 'running',
                  'baseline': next(iter(actions)), 'choice': next(iter(actions)), 'alternatives': list(actions),
                  'reason': 'Shadow comparison; plan order continues.' if mode == 'shadow' else 'Waiting for bounded task selection.'}
        self.save(record)
        m['decision_request'] = record['id']
        m['decision'] = self.summary(record)
        m['message'] = record['reason']
        self.missions.save(m)
        thread = threading.Thread(target=self.request, args=(record,), daemon=True, name='studio-decision')
        self.threads.append(thread)
        thread.start()
        return candidates[0] if mode == 'shadow' else None

    def request(self, record):
        started = time.monotonic()
        try:
            profiles, _ = self.studio.profiles()
            config = provider_config(profiles, record['profile'])
            if fingerprint(config) != record['config_sha256']:
                raise ValueError('Decision profile changed before request.')
            options = {a['action']: a['title'] for a in record['frame']['actions']}
            answer, usage, model = request_choice(config, record['frame'], self.policy['question'], options,
                                                  self.policy, self.studio.config.parent)
            record.update(status='shadow' if record['mode'] == 'shadow' else 'selected', choice=answer['action'],
                          reason=answer['reason'][:1500], confidence=answer['confidence'], usage=usage, model=model)
            if 'probabilities' in answer:
                record['probabilities'] = answer['probabilities']
        except Exception as exc:
            record.update(status='fallback', reason=str(exc)[:1500])
        record.update(ended=time.time(), elapsed_seconds=time.monotonic() - started)
        with self.missions.lock:
            if self.get(record['id'])['status'] != 'running':
                return
            current = self.missions.get(record['mission'])
            if current['status'] != 'running' or current.get('decision_request') != record['id']:
                record.update(status='superseded', choice=record['baseline'], reason='Execution was paused, stopped or replaced before the reply.')
            if time.time() >= record['expires']:
                record.update(status='fallback', choice=record['baseline'], reason='Late reply discarded; deterministic order used.')
            self.save(record)
            if current.get('decision_request') == record['id']:
                current['decision'] = self.summary(record)
                self.missions.save(current)

    def close(self):
        for thread in self.threads:
            thread.join(timeout=self.policy["timeout_seconds"] + 1)
