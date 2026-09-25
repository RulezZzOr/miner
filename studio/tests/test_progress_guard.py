"""Regressions for the live planner research loop and noisy direct web results."""
import importlib
import json
import unittest
from studio.progress_guard import ProgressGuard, phase_limits
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

    def test_review_and_final_allow_fifteen_minutes_without_expanding_shorter_budgets(self):
        for phase in ('review', 'final'):
            self.assertEqual(phase_limits(phase, 1200, 40), (900, 6))
            self.assertEqual(phase_limits(phase, 900, 24), (900, 6))
            self.assertEqual(phase_limits(phase, 60, 3), (60, 3))

    def test_plan_discovery_is_bounded_but_report_is_still_allowed(self):
        g = ProgressGuard('plan')
        for _ in range(3):
            self.assertIsNone(g.inspect('glob_search', {'pattern': '**/*.md'}))
        self.assertIsNotNone(g.inspect('read_file', {'path': 'PROJECT.md'}))
        self.assertIsNone(g.inspect('create_file', {'path': 'report.json', 'data': {}}))

    def test_parallel_refusals_allow_model_to_read_feedback_before_stopping(self):
        g = ProgressGuard('plan')
        for _ in range(10):
            g.inspect('glob_search', {'pattern': '**/*.md'})
        g.end_turn()
        self.assertFalse(g.stop_reason)
        self.assertEqual(g.refusals, 1)
        self.assertIsNone(g.inspect('create_file', {'path': 'report.json'}))

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
    def test_progress_guard_blocks_without_retry_and_survives_reload(self):
        m=self.create();a={'id':'guarded','phase':'build'}
        self.controller.fail(m,a,'progress_guard: Repeated URL without progress')
        self.controller.save(m)
        saved=self.controller.get(m['id'])
        self.assertEqual(saved['status'],'blocked')
        self.assertEqual(len(saved['questions']),1)
        self.controller.tick();self.assertFalse(self.launched)

    def test_plan_timeout_does_not_launch_same_failed_plan_again(self):
        m=self.create();self.controller.action({'id':m['id'],'action':'start'});self.controller.tick()
        saved=self.controller.get(m['id']);self.assertEqual(saved['attempts'][0]['budget_seconds'],300)
        self.assertEqual(self.launched[0][0]['max_turns'],8)
        self.now+=301;self.controller.tick();self.controller.tick()
        saved=self.controller.get(m['id']);self.assertEqual(saved['status'],'blocked')
        self.now+=3600;self.controller.tick();self.assertEqual(len(self.launched),1)

    def test_plan_turn_limit_blocks_without_retry(self):
        m = self.create()
        self.controller.fail(m, {'phase': 'plan'}, 'max_turns')
        self.assertEqual(m['status'], 'blocked')
        self.assertEqual(len(m['questions']), 1)
