"""Regressions for the live planner research loop and noisy direct web results."""
import importlib
import json
import unittest
from studio.progress_guard import ProgressGuard, phase_limits, PLAN_DISCOVERY_CALLS, PLAN_DISCOVERY_TURNS, SAVE_PLAN_NOW, STRIKES
from studio.tests import test_missions


class GuardTests(unittest.TestCase):
    def test_report_encoding_is_normalized_before_write(self):
        from studio.runner import normalize_report_args
        report = {'status': 'plan', 'questions': [], 'tasks': []}
        for args in [{'data': report}, {'data': json.dumps(report)}, {'content': json.dumps(report)}]:
            normalized = normalize_report_args({'path': 'report.json', **args})
            self.assertEqual(normalized['data'], report)
            self.assertNotIn('content', normalized)
        for args in [{'content': '{"broken":'}, {'data': []}, {'data': 'not JSON'}]:
            with self.assertRaises(ValueError):
                normalize_report_args({'path': 'report.json', **args})

    def test_planning_refuses_research_but_can_write_report(self):
        g=ProgressGuard('plan')
        for i in range(3):
            self.assertIsNotNone(g.inspect('web_fetch', {'url':'https://example.com'}))
            g.end_turn()
        self.assertTrue(g.stop_reason.startswith('progress_guard:'))
        clean=ProgressGuard('plan')
        self.assertIsNone(clean.inspect('read_file', {'path':'PROJECT.md'}))
        self.assertIsNone(clean.inspect('create_file', {'path':'report.json','content':'{}'}))

    def test_focus_www_fragment_and_scheme_changes_do_not_evade_repeat_limit(self):
        g=ProgressGuard('build')
        self.assertIsNone(g.inspect('web_fetch',{'url':'https://example.com/','info_to_extract':'a'}))
        self.assertIsNone(g.inspect('web_fetch',{'url':'http://www.example.com/#intro','info_to_extract':'b'}))
        self.assertIsNotNone(g.inspect('web_fetch',{'url':'https://example.com','info_to_extract':'c'}))
        g.end_turn()
        self.assertIsNone(g.inspect('web_fetch',{'url':'https://example.com/docs'}))
        self.assertIsNone(g.inspect('web_fetch',{'url':'https://example.com/docs?version=2'}))
        for _ in range(2):
            g.inspect('web_fetch',{'url':['https://example.com','https://new.example.com']})
            g.end_turn()
        self.assertTrue(g.stop_reason)

    def test_plan_limits_do_not_expand_user_budget_or_restrict_build(self):
        self.assertEqual(phase_limits('plan',1200,40),(300,8))
        self.assertEqual(phase_limits('plan',60,3),(60,3))
        self.assertEqual(phase_limits('build',1200,40),(1200,40))

    def test_review_and_final_allow_twenty_minutes_without_expanding_shorter_budgets(self):
        for phase in ('review', 'final'):
            self.assertEqual(phase_limits(phase, 1200, 40), (1200, 6))
            self.assertEqual(phase_limits(phase, 1800, 40), (1200, 6))
            self.assertEqual(phase_limits(phase, 900, 24), (900, 6))
            self.assertEqual(phase_limits(phase, 60, 3), (60, 3))

    def test_plan_discovery_is_bounded_but_report_is_still_allowed(self):
        g = ProgressGuard('plan')
        for turn in range(PLAN_DISCOVERY_TURNS):
            self.assertIsNone(g.inspect('glob_search', {'pattern': '**/*.md'}))
            notice = g.end_turn()
        # The planner is told at turn level that only the report remains, before any refusal.
        self.assertEqual(notice, SAVE_PLAN_NOW)
        self.assertIn('save_mission_report', notice)
        self.assertIsNotNone(g.inspect('read_file', {'path': 'PROJECT.md'}))
        self.assertIsNone(g.inspect('create_file', {'path': 'report.json', 'data': {}}))
        self.assertEqual(g.end_turn(), '')
        self.assertFalse(g.stop_reason)

    def test_parallel_refusals_allow_model_to_read_feedback_before_stopping(self):
        g = ProgressGuard('plan')
        results = [g.inspect('glob_search', {'pattern': '**/*.md'}) for _ in range(PLAN_DISCOVERY_CALLS + 2)]
        self.assertEqual(sum(r is None for r in results), PLAN_DISCOVERY_CALLS)
        self.assertEqual(g.end_turn(), SAVE_PLAN_NOW)
        self.assertFalse(g.stop_reason)
        self.assertEqual(g.refusals, 1)
        self.assertIsNone(g.inspect('create_file', {'path': 'report.json'}))

    def test_parallel_planner_reads_count_as_one_discovery_step(self):
        # Production shape: five parallel reads, then one read in each of two more turns.
        g = ProgressGuard('plan')
        for count in (5, 1, 1):
            for i in range(count):
                self.assertIsNone(g.inspect('read_file', {'path': f'notes/{i}.md'}))
            notice = g.end_turn()
        self.assertEqual(notice, SAVE_PLAN_NOW)
        self.assertFalse(g.stop_reason)
        # Only consecutive refused turns stop the run; saving the plan is always possible.
        self.assertIsNotNone(g.inspect('read_file', {'path': 'more.md'}))
        self.assertEqual(g.end_turn(), '')
        self.assertIsNone(g.inspect('create_file', {'path': 'report.json', 'data': {}}))
        g.end_turn()
        self.assertEqual(g.refusals, 0)
        for _ in range(STRIKES):
            g.inspect('grep_search', {'pattern': 'x'})
            g.end_turn()
        self.assertTrue(g.stop_reason.startswith('progress_guard:'))
        self.assertEqual(g.failure_kind, 'progress_guard')

    def test_plan_recovery_can_start_with_discovery_exhausted(self):
        g = ProgressGuard('plan', save_only=True)
        self.assertIn('survey is complete', g.inspect('read_file', {'path': 'PROJECT.md'}))
        self.assertEqual(g.end_turn(), SAVE_PLAN_NOW)
        self.assertIsNone(g.inspect('create_file', {'path': 'report.json', 'data': {}}))
        self.assertFalse(ProgressGuard('build', save_only=True).save_only)

    def test_refusals_reset_after_a_productive_turn_and_polling_after_fixes_is_allowed(self):
        g = ProgressGuard('build')
        health = {'url': 'http://127.0.0.1:8080/health'}
        for _ in range(2):
            self.assertIsNone(g.inspect('web_fetch', health))
            g.end_turn()
        self.assertIsNotNone(g.inspect('web_fetch', health))
        g.end_turn()
        self.assertEqual(g.refusals, 1)
        # A fix changes files; the next health check is a new observation.
        self.assertIsNone(g.inspect('file_editor_str_replace', {'path': 'app.py'}))
        g.end_turn()
        self.assertEqual(g.refusals, 0)
        for _ in range(5):
            self.assertIsNone(g.inspect('web_fetch', health))
            g.end_turn()
            self.assertIsNone(g.inspect('create_file', {'path': 'app.py'}))
            g.end_turn()
        self.assertFalse(g.stop_reason)
        self.assertEqual(g.failure_kind, '')
        # Non-consecutive refused turns never add up to a stop.
        for _ in range(4):
            g.inspect('web_fetch', health); g.inspect('web_fetch', health); g.inspect('web_fetch', health)
            g.end_turn()
            g.inspect('read_file', {'path': 'app.py'})
            g.end_turn()
        self.assertFalse(g.stop_reason)

    def test_direct_html_extraction_preserves_content_without_scripts(self):
        fetch=importlib.import_module('plugins.tools.web_fetch_aligned')
        html='<html><head><style>.giant{color:red}</style><script>SECRET_SCRIPT_BLOB</script></head><body><main><h1>API docs</h1><p>The service exposes a read-only endpoint for listing all configured projects and their current status.</p><p>Use the documented authentication and pagination parameters to retrieve the complete list.</p></main></body></html>'
        text=fetch.readable_direct_content(html,'text/html; charset=utf-8')
        self.assertIn('read-only endpoint',text)
        self.assertNotIn('SECRET_SCRIPT_BLOB',text)
        self.assertNotIn('<html',text)
        self.assertEqual(fetch.readable_direct_content('{"ok":true}','application/json'),'{"ok":true}')
        self.assertFalse(fetch.readable_direct_content('<html><script>empty</script></html>','text/html'))


class ControllerGuardTests(unittest.TestCase):
    # Reuse the persisted controller fixture without rerunning unrelated cases.
    setUp = test_missions.MissionTests.setUp
    launch = test_missions.MissionTests.launch
    stop = test_missions.MissionTests.stop
    create = test_missions.MissionTests.create
    # Run failure contract (K5): a progress_guard, time_limit or max_turns stop is recoverable with
    # bounded, visible automatic recovery. It is never an immediate block, and it never retries forever.
    def fail_until_blocked(self, m, phase, reason, limit=12):
        for n in range(1, limit + 1):
            self.controller.fail(m, {'id': f'{phase}-{n}', 'phase': phase}, reason)
            if n == 1:
                self.assertNotEqual(m['status'], 'blocked', 'first recoverable stop must not block')
                self.assertTrue(m['message'])
            if m['status'] == 'blocked':
                return n
        self.fail('recovery is not bounded')

    def test_progress_guard_stop_recovers_boundedly_and_block_survives_reload(self):
        m = self.create(); m['status'] = 'running'
        self.assertGreater(self.fail_until_blocked(m, 'build', 'progress_guard: Repeated URL without progress'), 1)
        self.controller.save(m)
        saved = self.controller.get(m['id'])
        self.assertEqual(saved['status'], 'blocked')
        self.assertTrue(saved['message'])
        self.controller.tick(); self.assertFalse(self.launched)

    def test_plan_progress_guard_and_turn_limit_are_recoverable(self):
        for reason in ('progress_guard: Repetition without progress. Local planner survey is complete.', 'max_turns'):
            with self.subTest(reason=reason):
                m = self.create(); m['status'] = 'running'
                self.assertGreater(self.fail_until_blocked(m, 'plan', reason), 1)

    def test_plan_timeout_relaunches_a_bounded_number_of_times(self):
        m=self.create();self.controller.action({'id':m['id'],'action':'start'});self.controller.tick()
        self.assertEqual(self.launched[0][1]['phase'],'plan')
        for _ in range(12):
            a=self.controller.get(m['id'])['attempts'][-1]
            self.now=max(self.now,a['started']+a['budget_seconds']+600);self.controller.tick();self.controller.tick()
            if self.controller.get(m['id'])['status']=='blocked':
                break
            self.now+=3600;self.controller.tick()
        saved=self.controller.get(m['id'])
        self.assertEqual(saved['status'],'blocked')
        self.assertTrue(saved['message'])
        launches=len(self.launched)
        self.assertGreater(launches,1)
        self.assertLess(launches,12)
        self.now+=3600;self.controller.tick();self.assertEqual(len(self.launched),launches)
