import asyncio
import tempfile
import unittest
from pathlib import Path

from studio.mission_report import MissionReport


class MissionReportTests(unittest.TestCase):
    def report(self, phase='plan'):
        return MissionReport({'mission': {'phase': phase, 'attempt': 'attempt'}}, Path('/fixed/report.json'))

    def plan(self):
        return {'status': 'plan', 'tasks': [{'id': 'one', 'title': 'Inventory',
            'instructions': 'Read server inventory; write infrastructure.md.',
            'criteria': ['Sources are recorded'], 'depends_on': []}], 'questions': []}

    def test_schema_has_no_path_or_encoded_content(self):
        schema = self.report().tool().parameters
        self.assertNotIn('path', schema['properties'])
        self.assertNotIn('data', schema['properties'])
        self.assertNotIn('content', schema['properties'])
        self.assertFalse(schema['additionalProperties'])

    def test_rejects_encoded_objects_extra_paths_and_invalid_graph(self):
        reporter = self.report()
        import json
        for value in [json.dumps(self.plan()), {**self.plan(), 'path': '/tmp/escape'},
                      {**self.plan(), 'tasks': []}, {**self.plan(), 'status': 'done'}]:
            with self.assertRaises(ValueError):
                reporter.validate(value)
        cycle = self.plan()
        cycle['tasks'][0]['depends_on'] = ['one']
        with self.assertRaises(ValueError):
            reporter.validate(cycle)
        self.assertFalse(reporter.saved)

    def test_valid_block_and_phase_specific_result(self):
        block = {'status': 'blocked', 'questions': [{'question': 'Which region?', 'reason': 'Required business decision'}]}
        self.assertEqual(self.report().validate(block), block)
        result = {'status': 'done', 'summary': 'Verified', 'artifacts': ['product.txt'],
                  'checks': [{'criterion': 'File exists', 'passed': True, 'evidence': 'Read product.txt'}]}
        self.assertEqual(self.report('build').validate(result)['status'], 'done')
        with self.assertRaises(ValueError):
            self.report('review').validate(result)
        result['status'] = 'pass'
        result['checks'][0]['passed'] = 'true'
        with self.assertRaises(ValueError):
            self.report('review').validate(result)

    def test_writer_rejects_wrong_destination(self):
        with self.assertRaises(ValueError):
            asyncio.run(self.report().tool().ainvoke({'data': self.plan(), 'path': '/tmp/escape'}))

    # The in-run tool enforces the controller's exact contract (shared check_report).

    def mission(self, phase, root=None, **extra):
        request = {'mission': {'phase': phase, 'attempt': 'attempt', **extra}}
        if root:
            request['cwd'] = str(root)
        return MissionReport(request, Path('/fixed/report.json'))

    def test_required_readiness_is_enforced_in_the_run(self):
        reporter = self.mission('plan', readiness_required=True)
        with self.assertRaisesRegex(ValueError, 'Complex brief requires a readiness assessment'):
            reporter.validate(self.plan())
        not_ready = {**self.plan(), 'readiness': {'status': 'clarify', 'reason': 'Scope conflicts.'}}
        with self.assertRaisesRegex(ValueError, 'grouped owner questions'):
            reporter.validate(not_ready)
        ready = {**self.plan(), 'readiness': {'status': 'ready', 'reason': 'Scope and criteria agree.'}}
        self.assertEqual(reporter.validate(ready)['readiness']['status'], 'ready')
        self.assertIn('requires the readiness field', reporter.tool().description)

    def test_exact_criteria_outputs_and_coverage_are_enforced_in_the_run(self):
        from studio.mission_report import ENGLISH
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            (root / 'docs').mkdir()
            (root / 'docs' / 'product.txt').write_text('OK')
            (root / 'escape').symlink_to(root / 'docs', target_is_directory=True)
            build = self.mission('build', root, criteria=['File contains OK.'])
            result = {'status': 'done', 'summary': 'Verified', 'artifacts': ['/workspace/docs/product.txt'],
                      'checks': [{'criterion': 'File contains OK.', 'passed': True, 'evidence': 'read_file: OK'}]}
            self.assertEqual(build.validate(result)['artifacts'], ['docs/product.txt'])
            self.assertEqual(build.validate({**result, 'artifacts': [str(root / 'docs/product.txt')]})['artifacts'], ['docs/product.txt'])
            for paths, message in [(['docs/missing.txt'], 'Output file not found: docs/missing.txt'),
                                   (['escape/product.txt'], 'symbolic link'),
                                   (['../outside.txt'], 'relative to the project root'),
                                   (['.env'], 'not available as a product file'),
                                   (['company/projects/x/reports/a.json'], 'not a final product file')]:
                with self.subTest(paths=paths), self.assertRaisesRegex(ValueError, message):
                    build.validate({**result, 'artifacts': paths})
            paraphrased = {**result, 'checks': [{**result['checks'][0], 'criterion': 'The file has OK.'}]}
            with self.assertRaisesRegex(ValueError, 'Missing precise wording'):
                build.validate(paraphrased)
            review = self.mission('review', root, criteria=['File contains OK.'], expected_artifacts=['docs/product.txt', 'docs/other.txt'],
                                  review_packet={'id': 'packet'})
            typed = {**result, 'status': 'pass', 'checks': [{**result['checks'][0], 'outcome': 'supported', 'issue': 'none'}]}
            with self.assertRaisesRegex(ValueError, 'does not cover all output files'):
                review.validate(typed)
            self.assertEqual(review.validate({'status': 'changes', 'summary': 'Fix the heading.'})['status'], 'changes')
            self.assertIn(ENGLISH, build.tool().description)


if __name__ == '__main__':
    unittest.main()
