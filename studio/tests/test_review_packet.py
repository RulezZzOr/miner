"""Evidence budgets, immutable reads and fail-closed acceptance boundaries."""
import concurrent.futures
import hashlib
import json
import unittest

from studio.review_packet import build_packet, encoded, ReviewEvidence, PACKET_BYTES, READ_BYTES
from studio.mission_report import MissionReport
from studio.tests.test_missions import MissionTests, report
from pathlib import Path


class PacketTests(unittest.TestCase):
    def fixture(self):
        bodies = {'deliverable.md': ('Observed service, not inferred. ' * 2000).encode(),
                  'PROJECT.md': ('Reference ' * 8000).encode(), 'log.txt': b'private details' * 9000}
        objects = {hashlib.sha256(v).hexdigest(): v for v in bodies.values()}
        version = {'id': 'v1', 'files': {p: hashlib.sha256(v).hexdigest() for p, v in bodies.items()}}
        task = {'id': 'one', 'title': 'Inventory', 'criteria': ['Explicit unknowns'], 'artifacts': [{'path': 'deliverable.md'}]}
        m = {'title': 'Inventory', 'goal': 'Inspect', 'constraints': 'Read only', 'questions': [], 'tasks': [task], 'criteria': task['criteria']}
        return m, task, version, objects

    def test_packet_is_bounded_and_preserves_criteria_and_truncation(self):
        m, task, version, objects = self.fixture()
        packet, allowed = build_packet(m, {'phase': 'review'}, task, version, objects.__getitem__, {'status': 'not_run'})
        self.assertLessEqual(len(encoded(packet)), PACKET_BYTES)
        self.assertEqual(packet['criteria'], task['criteria'])
        self.assertTrue(packet['excerpts'][0]['truncated'])
        self.assertEqual(packet['excerpts'][0]['path'], 'deliverable.md')
        self.assertEqual(allowed['deliverable.md']['sha256'], version['files']['deliverable.md'])
        m['constraints'] = '不可裁剪' * 10000
        with self.assertRaisesRegex(ValueError, 'required criteria and constraints'):
            build_packet(m, {'phase': 'review'}, task, version, objects.__getitem__, {})

    def test_exact_paths_checksums_offsets_and_concurrent_budget(self):
        m, task, version, objects = self.fixture()
        _, allowed = build_packet(m, {'phase': 'review'}, task, version, objects.__getitem__, {})
        reader = ReviewEvidence(allowed, objects.__getitem__)
        with self.assertRaises(ValueError):
            reader.read('../PROJECT.md')
        part = json.loads(reader.read('deliverable.md', 3000))
        self.assertEqual(part['next_offset'], 6000)
        self.assertLessEqual(len(part['text'].encode()), READ_BYTES)
        with self.assertRaisesRegex(ValueError, 'budget exhausted'):
            reader.read('deliverable.md')
        broken = ReviewEvidence(allowed, lambda _: b'changed')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            broken.read('deliverable.md')
        reader = ReviewEvidence(allowed, objects.__getitem__)
        def read(_):
            try:
                reader.read('deliverable.md'); return True
            except ValueError:
                return False
        with concurrent.futures.ThreadPoolExecutor(8) as pool:
            self.assertEqual(sum(pool.map(read, range(20))), 2)

    def test_typed_review_cannot_pass_missing_evidence(self):
        reporter = MissionReport({'mission': {'phase': 'review', 'attempt': 'a', 'review_packet': {'id': 'packet'}}}, Path('/fixed'))
        value = report('pass')
        del value['checks'][0]['outcome']
        with self.assertRaisesRegex(ValueError, 'outcome'):
            reporter.validate(value)
        check = value['checks'][0]
        check.update(outcome='insufficient_evidence', issue='missing_evidence')
        with self.assertRaisesRegex(ValueError, 'cannot pass'):
            reporter.validate(value)
        check.update(outcome='supported', issue='none')
        self.assertEqual(reporter.validate(value)['status'], 'pass')
        check['needs_owner'] = True
        with self.assertRaises(ValueError):
            reporter.validate(value)


class ReviewControllerTests(unittest.TestCase):
    setUp = MissionTests.setUp
    launch = MissionTests.launch
    stop = MissionTests.stop
    create = MissionTests.create
    start = MissionTests.start
    current = MissionTests.current
    finish = MissionTests.finish
    begin_build = MissionTests.begin_build

    def test_reference_changed_during_review_rejects_verdict(self):
        (self.project / 'PROJECT.md').write_text('Authoritative reference')
        self.begin_build()
        self.finish(report())
        a = self.current()['attempts'][-1]
        self.assertIn('PROJECT.md', a['review_packet']['sources'])
        (self.project / 'PROJECT.md').write_text('New instructions')
        m = self.finish(report('pass'))
        self.assertNotEqual(m['tasks'][0]['status'], 'done')
        self.assertIn('cannot be accepted', m['message'])

    def test_large_essential_input_blocks_without_identical_retry(self):
        self.begin_build()
        m = self.current(); m['constraints'] = 'x' * 30000
        self.controller.save(m)
        m = self.finish(report())
        self.assertEqual(m['status'], 'blocked')
        self.assertIsNone(m['active_attempt'])
        self.assertIn('fixed budget', m['message'])
