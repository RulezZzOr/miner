"""Real sandboxed runner: mission runs end with a saved report or an explicit failure kind."""
import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from studio.runner import LAST_TURN, NO_REPORT
from studio.server import Studio, write_config
from studio.tests.sandbox_support import requires_sandbox

PLAN = {'status': 'plan', 'questions': [], 'tasks': [{'id': 'one', 'title': 'Build', 'instructions': 'Write product.txt',
                                                       'depends_on': [], 'criteria': ['File exists']}]}


class ScriptedModel(BaseHTTPRequestHandler):
    """Answers request N with script[N] (the last entry repeats)."""

    def log_message(self, *args):
        pass

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        with self.server.lock:
            self.server.requests.append(data)
            step = self.server.script[min(len(self.server.requests), len(self.server.script)) - 1]
        if 'text' in step:
            message, finish = {'role': 'assistant', 'content': step['text']}, 'stop'
        else:
            message = {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': f'call_{len(self.server.requests)}',
                       'type': 'function', 'function': {'name': step['tool'], 'arguments': json.dumps(step['args'])}}]}
            finish = 'tool_calls'
        if data.get('stream'):
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            for delta, reason in ((message, None), ({}, finish)):
                chunk = {'id': 'chatcmpl-test', 'object': 'chat.completion.chunk', 'created': int(time.time()),
                         'model': 'test', 'choices': [{'index': 0, 'delta': delta, 'finish_reason': reason}]}
                self.wfile.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n')
            return
        body = {'id': 'chatcmpl-test', 'object': 'chat.completion', 'created': int(time.time()), 'model': 'test',
                'choices': [{'index': 0, 'message': message, 'finish_reason': finish}],
                'usage': {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120}}
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())


@requires_sandbox
class MissionRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / 'project'
        self.project.mkdir()
        self.model = ThreadingHTTPServer(('127.0.0.1', 0), ScriptedModel)
        self.model.requests, self.model.lock = [], threading.Lock()
        threading.Thread(target=self.model.serve_forever, daemon=True).start()
        self.addCleanup(self.model.server_close)
        self.addCleanup(self.model.shutdown)
        self.config = self.root / 'agent.toml'
        write_config(self.config, {'test': {'model': 'test', 'protocol': 'chat_completions', 'chat_dialect': 'openai',
            'base_url': f'http://127.0.0.1:{self.model.server_port}/v1', 'auth': 'none',
            'context_window': 32768, 'max_output_tokens': 4096}}, 'test')
        self.studio = Studio(self.project, self.root / 'state', self.config)
        self.addCleanup(self.studio.oauth.close)
        self.addCleanup(self.studio.missions.close)
        self.addCleanup(self.stop_runs)
        self.pid = next(iter(self.studio.projects))

    def stop_runs(self):
        for run_id, process in list(self.studio.processes.items()):
            self.studio.stop(run_id)
            self.studio.kill_later(process)

    def run_plan(self, script, max_turns=4):
        self.model.script = script
        attempt = f'{int(time.time() * 1000) % 10**16:016x}'
        run = self.studio.launch({'project': self.pid, 'task': 'Save the prescribed plan.', 'profile': 'test',
                                  'max_turns': max_turns, 'auto_approve': True},
                                 mission={'id': 'runner-test', 'attempt': attempt, 'phase': 'plan', 'attempt_seconds': 120})
        deadline = time.monotonic() + 90
        while self.studio.runs[run['id']]['status'] in {'running', 'waiting', 'stopping'}:
            if time.monotonic() > deadline:
                self.fail((self.studio.run_dir(run['id']) / 'console.log').read_text()[-6000:])
            time.sleep(0.1)
        result = json.loads((self.studio.run_dir(run['id']) / 'result.json').read_text())
        report = self.project / 'company' / 'projects' / 'runner-test' / 'reports' / f'{attempt}.json'
        return result, report

    def user_texts(self, request):
        return [m.get('content') for m in request['messages'] if m.get('role') == 'user' and isinstance(m.get('content'), str)]

    def test_chat_answer_is_answered_with_a_report_request(self):
        result, report = self.run_plan([{'text': 'Here is my plan in prose.'}, {'tool': 'save_mission_report', 'args': PLAN}])
        self.assertEqual((result['status'], result['failure_kind']), ('completed', ''), result)
        self.assertEqual(json.loads(report.read_text()), PLAN)
        self.assertEqual(len(self.model.requests), 2)
        self.assertIn(NO_REPORT, self.user_texts(self.model.requests[1]))

    def test_run_without_a_report_is_incomplete_not_completed(self):
        result, report = self.run_plan([{'text': 'Done, the plan is in this message.'}])
        self.assertEqual((result['status'], result['failure_kind']), ('incomplete', 'no_report'), result)
        self.assertIn('save_mission_report', result['reason'])
        self.assertFalse(report.exists())
        self.assertEqual(len(self.model.requests), 3)  # the answer plus two bounded report requests

    def test_final_turn_keeps_the_report_tool(self):
        invalid = {'status': 'plan', 'questions': [], 'tasks': []}
        result, report = self.run_plan([{'tool': 'save_mission_report', 'args': invalid},
                                         {'tool': 'save_mission_report', 'args': PLAN}], max_turns=2)
        self.assertEqual(result['status'], 'completed', result)
        self.assertTrue(report.exists())
        final = self.model.requests[-1]
        self.assertIn('save_mission_report', [t['function']['name'] for t in final.get('tools', [])])
        self.assertIn(LAST_TURN, self.user_texts(final))


if __name__ == '__main__':
    unittest.main()
