"""Bound repeated research and separate preparation from execution."""
from collections import Counter
from urllib.parse import urlsplit

DISCOVERY_TOOLS = {'read_file', 'glob_search', 'grep_search', 'recover_result'}
# Calls that change files end a polling cycle: fetching the same page again after
# a fix is a new observation, not a repeated question.
WRITE_TOOLS = {'create_file', 'download_file', 'write_file', 'file_editor_create',
               'file_editor_str_replace', 'delete_file'}
# Parallel reads in one model response count as one discovery step; the call
# ceiling still bounds a single response that requests a very large batch.
PLAN_DISCOVERY_TURNS = 3
PLAN_DISCOVERY_CALLS = 12
STRIKES = 3
SURVEY_COMPLETE = ("Local planner survey is complete. Now save the plan; further investigation and access "
                   "verification should be scheduled as execution tasks.")
ENGLISH_OUTPUT = 'Write all reports, questions, summaries, notes and generated documentation in English, regardless of the language of the input.'
SAVE_PLAN_NOW = ("Discovery budget used. Your only valid next action is save_mission_report with status plan, "
                 "tasks (and readiness when the brief asks for it), built from the information already in this "
                 "prompt and the files you have read. Schedule any further investigation as an execution task. "
                 "Further read, glob or grep calls will be refused and will end this run. " + ENGLISH_OUTPUT)


def phase_limits(phase, seconds, turns):
    """Planning produces a small report; its budget is not the product budget."""
    if phase == 'plan':
        return min(seconds, 300), min(turns, 8)
    if phase in {'review', 'final'}:
        return min(seconds, 1200), min(turns, 6)
    return seconds, turns


class ProgressGuard:
    def __init__(self, phase, save_only=False):
        self.phase = phase
        self.calls = Counter()
        self.refusals = 0
        self.discovery_calls = 0
        self.discovery_turns = 0
        self.turn_discovery = False
        # A recovery attempt may start with discovery already exhausted.
        self.save_only = bool(save_only) and phase == 'plan'
        self.notice_sent = False
        self.stop_reason = ''
        self.turn_refusal = ''

    @property
    def failure_kind(self):
        return 'progress_guard' if self.stop_reason else ''

    def end_turn(self):
        """Close one model response; return a message to inject before the next turn, or ''.

        One response can contain several parallel calls, so strikes are counted
        per turn. Only consecutive refused turns stop the run: a productive turn
        in between resets the count.
        """
        notice = ''
        if self.turn_refusal:
            self.refusals += 1
            if self.refusals >= STRIKES:
                self.stop_reason = "progress_guard: Repetition without progress. " + self.turn_refusal
        else:
            self.refusals = 0
        if self.phase == 'plan' and self.discovery_turns >= PLAN_DISCOVERY_TURNS:
            self.save_only = True
        if self.save_only and not self.notice_sent and not self.stop_reason:
            # Push the planner to save instead of stopping it: say once, at turn
            # level, that the report is the only remaining action.
            self.notice_sent = True
            notice = SAVE_PLAN_NOW
        self.turn_refusal = ''
        self.turn_discovery = False
        return notice

    def inspect(self, name, args):
        if self.stop_reason:
            return self.stop_reason
        reason = ''
        if self.phase == 'plan' and name in {'web_fetch', 'web_search'}:
            reason = "Planning must not perform web research. Add it to the plan as a later task."
        elif self.phase == 'plan' and name in DISCOVERY_TOOLS:
            exhausted = self.save_only or self.discovery_calls >= PLAN_DISCOVERY_CALLS or (
                not self.turn_discovery and self.discovery_turns >= PLAN_DISCOVERY_TURNS)
            if exhausted:
                self.save_only = True
                reason = SURVEY_COMPLETE
            else:
                if not self.turn_discovery:
                    self.discovery_turns += 1
                    self.turn_discovery = True
                self.discovery_calls += 1
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
        elif name in WRITE_TOOLS:
            self.calls.clear()
        if reason:
            self.turn_refusal = reason
            return reason + " Submit the report using save_mission_report from available materials."
        return None
