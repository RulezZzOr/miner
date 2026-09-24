"""Read-only decision experiments: original rubrics, saved evidence, no business actions."""
from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid

try:
    from .judgments import provider_config, request_typed
except ImportError:
    from judgments import provider_config, request_typed

RUBRIC_VERSION = 'miner-lab-1'
SCORE_LEVELS = ['Unknown or no supporting evidence', 'A specific but unvalidated hypothesis',
                'Some directly relevant evidence with material gaps', 'Concrete, reproducible supporting evidence']


def questions_for(kind):
    if kind == 'idea':
        axes = {'problem': 'A specific customer problem and identifiable target user are supported.',
                'demand': 'Observed customer behavior supports demand for this solution.',
                'feasibility': 'A bounded implementation and its dependencies are feasible with the stated resources.',
                'differentiation': 'A concrete advantage over identified alternatives is supported.',
                'revenue': 'A plausible payer, price hypothesis and distribution channel are stated and supported.'}
        return {**{k: {'type': 'score', 'instructions': v + ' Rate evidence strength, not enthusiasm.', 'criteria': SCORE_LEVELS} for k, v in axes.items()},
                'missing_evidence': {'type': 'noul', 'instructions': 'Is essential evidence missing for a launch decision? Treat unsupported claims as missing evidence.'}}
    if kind == 'document':
        return {'kind': {'type': 'choice', 'instructions': 'Classify the primary purpose of this text. Choose unknown when insufficient.',
                        'criteria': {'instructions': 'Desired behavior or requirements', 'architecture': 'Components and interfaces',
                                     'evidence': 'Dated observations or reproducible test results', 'research': 'External source findings',
                                     'unknown': 'Insufficient or ambiguous text'}},
                'support': {'type': 'score', 'instructions': 'How strongly does the text support the stated claim or question?', 'criteria': SCORE_LEVELS},
                'missing_evidence': {'type': 'noul', 'instructions': 'Does the claim require evidence absent from the supplied text?'}}
    if kind == 'crm':
        return {'department': {'type': 'choice', 'instructions': 'Choose the responsible department; ambiguous or insufficient input goes to unknown.',
                              'criteria': {'support': 'Existing customer technical problem', 'sales': 'Prospect or expansion interest',
                                           'billing': 'Invoice or payment question', 'unknown': 'Ambiguous request'}},
                'urgency': {'type': 'choice', 'instructions': 'Classify stated urgency; do not invent deadlines.',
                           'criteria': {'urgent': 'Explicit outage or stated immediate deadline', 'normal': 'Ordinary request', 'unknown': 'Not enough information'}},
                'needs_owner': {'type': 'noul', 'instructions': 'Does resolution require an owner decision, authorization or missing essential information?'}}
    raise ValueError('Choose idea, document or crm.')


def combine(kind, answers):
    if any(a.get('confidence', 1) < .8 for a in answers.values()):
        return {'recommendation': 'needs_review', 'reason': 'At least one axis has low confidence. No automatic action.'}
    if kind == 'idea':
        score = sum(answers[k]['score'] for k in ('problem', 'demand', 'feasibility', 'differentiation', 'revenue')) / 15 * 100
        recommendation = 'validate' if answers['missing_evidence']['noul'] >= .2 or min(answers[k]['score'] for k in ('demand', 'feasibility')) < 2 else 'prototype'
        return {'recommendation': recommendation, 'score': round(score, 1),
                'reason': 'Evidence score only; this does not authorize a launch, spending or predict business success.'}
    if kind == 'document':
        return {'recommendation': 'usable_reference' if answers['kind']['choice'] != 'unknown' and answers['support']['score'] >= 2 and answers['missing_evidence']['noul'] < .2 else 'needs_evidence',
                'reason': 'Triage only. Verify exact source passages before accepting claims.'}
    return {'recommendation': 'needs_owner' if answers['needs_owner']['noul'] >= .2 or answers['department']['choice'] == 'unknown' else 'proposed_route:' + answers['department']['choice'],
            'reason': 'Read-only proposal; no CRM changes or messages were sent.'}


class DecisionLab:
    def __init__(self, studio):
        self.studio = studio
        self.lock = threading.RLock()
        self.thread = None
        with studio.missions.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS decision_lab (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
            records = [json.loads(row[0]) for row in db.execute('SELECT data FROM decision_lab')]
        for record in records:
            if record['status'] == 'running':
                record.update(status='unknown', error='Interrupted by restart. No result or business action.', ended=time.time())
                self.save(record)

    def active(self):
        return bool(self.thread and self.thread.is_alive())

    def save(self, record):
        with self.studio.missions.connect() as db:
            db.execute('INSERT INTO decision_lab VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET data=excluded.data',
                       (record['id'], json.dumps(record, ensure_ascii=False)))

    def get(self, key):
        with self.studio.missions.connect() as db:
            row = db.execute('SELECT data FROM decision_lab WHERE id=?', (key,)).fetchone()
        if not row:
            raise ValueError('Evaluation does not exist.')
        return self.expire(json.loads(row[0]))

    def expire(self, record):
        with self.lock:
            if record['status'] == 'running' and time.time() > record['deadline']:
                # A poll may have read this row just before the worker completed.
                with self.studio.missions.connect() as db:
                    row = db.execute('SELECT data FROM decision_lab WHERE id=?', (record['id'],)).fetchone()
                current = json.loads(row[0]) if row else record
                if current['status'] != 'running':
                    return current
                record.update(status='unknown', error='Evaluation deadline expired; no business action.', ended=time.time())
                self.save(record)
        return record

    def list(self, project):
        self.studio.project(project)
        with self.studio.missions.connect() as db:
            records = [json.loads(row[0]) for row in db.execute('SELECT data FROM decision_lab ORDER BY rowid DESC LIMIT 200')]
        return [self.expire(r) for r in records if r['project'] == project][:30]

    def start(self, body):
        self.studio.project(body['project'])
        text = body.get('text', '')
        claim = body.get('claim', '')
        if not isinstance(text, str) or not 30 <= len(text.encode('utf-8')) <= 12000:
            raise ValueError('Provide 30–12,000 bytes of evidence text. Empty scans need OCR before classification; input is never silently truncated.')
        if not isinstance(claim, str) or len(claim) > 1000:
            raise ValueError('Keep the claim or question within 1,000 characters.')
        kind = body.get('kind'); questions = questions_for(kind)
        profiles, default = self.studio.profiles()
        profile = body.get('profile') or default
        config = provider_config(profiles, profile)
        with self.studio.lock, self.lock:
            if any(r['status'] in {'running', 'waiting', 'stopping'} for r in self.studio.runs.values()):
                raise ValueError('A project worker is using inference. Wait for it to finish before starting a lab experiment.')
            if getattr(self.studio, 'browser_pilot', None) and self.studio.browser_pilot.lock.locked():
                raise ValueError('A browser pilot proposal is in flight.')
            if self.active():
                raise ValueError('One evaluation is already running. Wait for its bounded result.')
            record = {'id': uuid.uuid4().hex[:16], 'project': body['project'], 'kind': kind, 'profile': profile,
                      'provider': config['provider'], 'model': config['model'], 'rubric': RUBRIC_VERSION,
                      'questions': questions, 'text': text, 'claim': claim, 'input_bytes': len(text.encode()),
                      'sha256': hashlib.sha256(text.encode()).hexdigest(), 'started': time.time(),
                      'status': 'running', 'deadline': time.time() + 120, 'read_only': True}
            self.save(record)
            self.thread = threading.Thread(target=self.evaluate, args=(record, config), daemon=True, name='studio-decision-lab')
            self.thread.start()
            return record

    def evaluate(self, record, config):
        try:
            answers, usage, model = request_typed(config, {'text': record['text'], 'claim': record['claim']}, record['questions'],
                {'timeout_seconds': 120, 'max_output_tokens': 1000}, self.studio.config.parent)
            if time.time() > record['deadline']:
                raise ValueError('Late evaluation discarded.')
            record.update(status='completed', answers=answers, result=combine(record['kind'], answers), usage=usage, model=model)
        except Exception as exc:
            record.update(status='unknown', error=str(exc)[:1500], result={'recommendation': 'needs_review', 'reason': 'No validated model result.'})
        record.update(ended=time.time(), elapsed_seconds=time.time() - record['started'])
        with self.lock:
            if self.get(record['id'])['status'] == 'running':
                self.save(record)

    def export(self, key):
        record = self.get(key)
        if record['status'] == 'running':
            raise ValueError('Wait for the evaluation to finish before exporting.')
        lines = ['# Decision evaluation', '', f"Rubric: {record['rubric']} · Status: {record['status']}",
                 f"Provider: {record['provider']} · Model: {record['model']}", f"Input SHA-256: {record['sha256']}",
                 '', 'This is a read-only model judgment, not a verified business outcome.', '', '## Result', '',
                 '```json', json.dumps({k: record.get(k) for k in ('result', 'answers', 'error', 'usage')}, ensure_ascii=False, indent=2),
                 '```', '', '## Evaluated question', '', record['claim'], '', '## Supplied evidence', '', record['text']]
        path = 'analysis/decision-lab/' + key + '.md'
        self.studio.save_file({'project': record['project'], 'path': path, 'content': '\n'.join(lines) + '\n', 'revision': None})
        return {'path': path, 'project': record['project']}
