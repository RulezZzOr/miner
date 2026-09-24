"""Bound repeated research and separate preparation from execution."""
from collections import Counter
from urllib.parse import urlsplit


def phase_limits(phase, seconds, turns):
    """Planning produces a small report; its budget is not the product budget."""
    if phase == 'plan':
        return min(seconds, 300), min(turns, 8)
    if phase in {'review', 'final'}:
        return min(seconds, 480), min(turns, 6)
    return seconds, turns


class ProgressGuard:
    def __init__(self, phase):
        self.phase = phase
        self.calls = Counter()
        self.refusals = 0
        self.discovery_calls = 0
        self.stop_reason = ''
        self.turn_refusal = ''

    def end_turn(self):
        # One model response can contain several parallel calls. Give the
        # model a chance to read the refusal before counting another strike.
        if self.turn_refusal:
            self.refusals += 1
            if self.refusals >= 3:
                self.stop_reason = "progress_guard: Repetition without progress. " + self.turn_refusal
        self.turn_refusal = ''

    def inspect(self, name, args):
        if self.stop_reason:
            return self.stop_reason
        reason = ''
        if self.phase == 'plan' and name in {'web_fetch', 'web_search'}:
            reason = "Planning must not perform web research. Add it to the plan as a later task."
        elif self.phase == 'plan' and name in {'read_file', 'glob_search', 'grep_search', 'recover_result'}:
            self.discovery_calls += 1
            if self.discovery_calls > 3:
                reason = "Local planner survey is complete. Now save the plan; further investigation and access verification should be scheduled as execution tasks."
        elif name == 'web_fetch':
            urls = args.get('url', [])
            if isinstance(urls, str):
                urls = [urls]
            keys = set()
            for url in urls if isinstance(urls, list) else []:
                if not isinstance(url, str):
                    continue
                try:
                    parsed = urlsplit(url)
                    host = (parsed.hostname or '').lower().removeprefix('www.')
                    keys.add((host, parsed.port, parsed.path.rstrip('/'), parsed.query))
                except ValueError:
                    keys.add((url,))
            if any(self.calls[key] >= 2 for key in keys):
                reason = "The same web page has already been requested twice. Use the obtained content or record unavailability; do not repeat the request with a differently worded question."
            else:
                self.calls.update(keys)
        if reason:
            self.turn_refusal = reason
            return reason + " Submit the report using save_mission_report from available materials."
        return None
