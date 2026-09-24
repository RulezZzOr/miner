import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from studio.judgments import request_typed, validate_answers, request_choice
from studio.decision_lab import DecisionLab, questions_for, combine
from studio.server import Studio, write_config


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_POST(self):
        self.server.payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        data = json.dumps(self.server.reply).encode()
        self.send_response(200); self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)


class LabTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.project = self.root / 'project'; self.project.mkdir()
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=self.http.serve_forever, daemon=True); thread.start()
        self.addCleanup(self.http.server_close); self.addCleanup(self.http.shutdown)
        self.config = {'model': 'fixture', 'protocol': 'chat_completions', 'auth': 'none', 'base_url': f'http://127.0.0.1:{self.http.server_port}/v1'}
        config = self.root / 'agent.toml'; write_config(config, {'test': self.config}, 'test')
        self.studio = Studio(self.project, self.root / 'state', config)
        self.addCleanup(self.studio.missions.close); self.addCleanup(self.studio.oauth.close)
        self.project_id = next(iter(self.studio.projects))

    def idea_answers(self):
        return {**{k: {'type': 'score', 'score': 2, 'confidence': .9} for k in ('problem', 'demand', 'feasibility', 'differentiation', 'revenue')},
                'missing_evidence': {'type': 'noul', 'noul': .8}}

    def test_actual_chat_batch_persists_and_exports_exact_evidence(self):
        answers = self.idea_answers()
        self.http.reply = {'choices': [{'message': {'content': json.dumps({'answers': answers})}}], 'usage': {'prompt_tokens': 50}, 'model': 'fixture-actual'}
        text = 'Hypothesis: a small scheduling product for repair shops. Customer demand is unknown.'
        r = self.studio.decision_lab.start({'project': self.project_id, 'kind': 'idea', 'profile': 'test', 'text': text})
        self.studio.decision_lab.thread.join(3)
        r = self.studio.decision_lab.get(r['id'])
        self.assertEqual(r['status'], 'completed')
        self.assertEqual(r['result']['recommendation'], 'validate')
        self.assertEqual(r['model'], 'fixture-actual')
        self.assertEqual(r['text'], text)
        exported = self.studio.decision_lab.export(r['id'])
        self.assertIn(text, (self.project / exported['path']).read_text())
        self.assertEqual(len(json.loads(self.http.payload['messages'][1]['content'])['questions']), 6)
        with self.assertRaises(Exception): self.studio.decision_lab.export(r['id'])  # no silent overwrite

    def test_typed_transport_and_distribution_are_validated(self):
        q = {'route': {'type': 'choice', 'instructions': 'Route', 'criteria': {'yes': 'Supported', 'unknown': 'Unknown'}}}
        self.http.reply = {'answers': {'route': {'type': 'choice', 'choice': 'unknown', 'confidence': .9,
                           'probabilities': {'yes': .1, 'unknown': .9}}}, 'model': 'typed-fixture', 'usage': {}}
        result, _, _ = request_typed({**self.config, 'provider': 'typesafe'}, {}, q,
            {'timeout_seconds': 1, 'max_output_tokens': 100}, self.root)
        self.assertEqual(result['route']['choice'], 'unknown')
        self.assertEqual(self.http.payload['questions'], q)
        self.http.reply['answers']['route']['probabilities']['unknown'] = float('nan')
        with self.assertRaises(ValueError):
            request_typed({**self.config, 'provider': 'typesafe'}, {}, q, {'timeout_seconds': 1, 'max_output_tokens': 100}, self.root)
        with self.assertRaisesRegex(ValueError, 'contradicts'):
            validate_answers({'route': {'type': 'choice', 'choice': 'yes', 'confidence': .9, 'probabilities': {'yes': .1, 'unknown': .9}}}, q)

    def test_bad_and_missing_answers_do_not_become_positive_scores(self):
        questions = questions_for('idea')
        answers = self.idea_answers()
        for invalid in [{}, {**answers, 'extra': {}}, {**answers, 'problem': {'type': 'score', 'score': True, 'confidence': .9}},
                        {**answers, 'demand': {'type': 'score', 'score': float('nan'), 'confidence': .9}}]:
            with self.assertRaises(ValueError): validate_answers(invalid, questions)
        answers['demand']['confidence'] = .2
        self.assertEqual(combine('idea', answers)['recommendation'], 'needs_review')

    def test_empty_or_oversize_text_is_rejected_and_restart_marks_unknown(self):
        for text in ['', 'x'*12001]:
            with self.assertRaises(ValueError):
                self.studio.decision_lab.start({'project': self.project_id, 'kind': 'document', 'text': text})
        self.studio.decision_lab.save({'id': 'interrupted', 'project': self.project_id, 'status': 'running', 'deadline': time.time()+50})
        restored = DecisionLab(self.studio)
        self.assertEqual(restored.get('interrupted')['status'], 'unknown')

    def test_live_worker_prevents_competing_lab_inference(self):
        self.studio.runs['busy'] = {'status': 'running'}
        with self.assertRaisesRegex(ValueError, 'worker is using inference'):
            self.studio.decision_lab.start({'project': self.project_id, 'kind': 'idea', 'text': 'A sufficiently long synthetic input for this boundary.'})
        self.assertIsNone(self.studio.decision_lab.thread)
