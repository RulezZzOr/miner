import asyncio
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


if __name__ == '__main__':
    unittest.main()
