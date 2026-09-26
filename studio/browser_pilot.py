"""Typed suggestions for a fixed local browser fixture, never arbitrary selectors or URLs."""
import threading
import time
try:
    from .http_errors import Busy
    from .judgments import provider_config, request_choice
except ImportError:
    from http_errors import Busy
    from judgments import provider_config, request_choice


def fixture_actions(frame):
    if (not isinstance(frame, dict) or set(frame) != {'query', 'filtered', 'visibleItems'} or
            not isinstance(frame['query'], str) or len(frame['query']) > 100 or type(frame['filtered']) is not bool or
            not isinstance(frame['visibleItems'], list) or len(frame['visibleItems']) > 3 or
            any(v not in {'Blue desk', 'Green chair', 'White lamp'} for v in frame['visibleItems'])):
        raise ValueError('Invalid local fixture observation.')
    if frame['query'] != 'blue':
        return {'set_query_blue': 'Set the fixture search input to the literal blue.', 'wait': 'Take no action if evidence is uncertain.'}
    if not frame['filtered']:
        return {'apply_filter': 'Click the fixture Filter button.', 'wait': 'Take no action if evidence is uncertain.'}
    return {'finish': 'Propose completion; code independently verifies exactly one visible Blue desk.', 'wait': 'Take no action if evidence is uncertain.'}


class BrowserPilot:
    def __init__(self, studio):
        self.studio = studio
        self.lock = threading.Lock()

    def choose(self, body):
        frame = body.get('frame'); options = fixture_actions(frame)
        with self.studio.lock:
            if any(r['status'] in {'running', 'waiting', 'stopping'} for r in self.studio.runs.values()) or self.studio.decision_lab.active():
                raise Busy('Inference is busy with project work or a lab evaluation. The fixture can still run without a model.')
            if not self.lock.acquire(blocking=False):
                raise Busy('A pilot decision is already in flight.')
        try:
            profiles, default = self.studio.profiles()
            config = provider_config(profiles, body.get('profile') or default)
            started = time.monotonic()
            try:
                self.studio.check_credential_target(config)  # Older saved profiles are checked again.
                answer, usage, model = request_choice(config, frame,
                    'Select the next permitted operation toward showing only Blue desk. The operation includes its exact target. Never invent a selector or operation. '
                    'Write all reports, questions, summaries, notes and generated documentation in English, regardless of the language of the input.',
                    options, {'timeout_seconds': 5, 'max_output_tokens': 200, 'confidence_threshold': .8}, self.studio.config.parent)
                if time.monotonic() - started >= 5:
                    raise ValueError('Late proposal discarded.')
                return {'action': answer['action'], 'status': 'selected', 'reason': f"{model}: {answer['reason']}", 'usage': usage}
            except Exception as exc:
                return {'action': next(iter(options)), 'status': 'fallback', 'reason': 'Deterministic fallback: ' + str(exc)[:500]}
        finally:
            self.lock.release()
