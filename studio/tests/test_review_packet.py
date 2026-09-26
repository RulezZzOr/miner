"""Evidence budgets, immutable reads and fail-closed acceptance boundaries."""
import concurrent.futures
import hashlib
import json
import unittest

from studio.review_packet import (build_packet, encoded, ReviewEvidence, PACKET_BYTES, READ_BYTES, MAX_SOURCES,
                                  ArtifactGapError, artifact_gaps, check_artifact_count)
from studio.mission_report import MissionReport
# Import the module, not the TestCase class: a class imported here would run again in this module.
from studio.tests import test_missions
from pathlib import Path

report = test_missions.report


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
        self.assertNotIn('sha256', packet['source_index'][0])
        self.assertNotIn('checks', packet['worker_claims'][0])
        # Oversized essential input is shortened with explicit flags instead of blocking review.
        m['constraints'] = '\u20ac' * 10000
        packet, _ = build_packet(m, {'phase': 'review'}, task, version, objects.__getitem__, {})
        self.assertLessEqual(len(encoded(packet)), PACKET_BYTES)
        self.assertTrue(packet['constraints_truncated'])
        self.assertTrue(packet['constraints'].startswith('\u20ac'))
        self.assertEqual(packet['criteria'], task['criteria'])
        self.assertIn('constraints', packet['notices'][-1])
        self.assertEqual(packet['excerpts'][0]['path'], 'deliverable.md')

    def objects_for(self, bodies):
        objects = {hashlib.sha256(v).hexdigest(): v for v in bodies.values()}
        return {'id': 'v2', 'files': {p: hashlib.sha256(v).hexdigest() for p, v in bodies.items()}}, objects

    def test_final_review_lists_every_artifact_beyond_the_source_index(self):
        bodies = {f'part-{t}/file-{i}.md': f'Section {t}.{i}'.encode() for t in range(3) for i in range(12)}
        version, objects = self.objects_for(bodies)
        tasks = [{'id': f't{t}', 'title': f'Part {t}', 'criteria': ['Done'], 'status': 'done',
                  'artifacts': [{'path': f'part-{t}/file-{i}.md'} for i in range(12)]} for t in range(3)]
        m = {'title': 'Large delivery', 'goal': 'Deliver', 'constraints': '', 'questions': [], 'tasks': tasks, 'criteria': ['All parts exist']}
        packet, allowed = build_packet(m, {'phase': 'final'}, None, version, objects.__getitem__, {})
        self.assertEqual(len(packet['artifacts']), 36)
        self.assertEqual(len(packet['source_index']), MAX_SOURCES)
        self.assertEqual(len(allowed), MAX_SOURCES)
        self.assertEqual(packet['omitted_artifact_count'], 36 - MAX_SOURCES)
        self.assertIn('unreviewed', ' '.join(packet['notices']))
        self.assertLessEqual(len(encoded(packet)), PACKET_BYTES)

    def renamed(self):
        version, objects = self.objects_for({'report.md': b'Merged report'})
        tasks = [{'id': 't1', 'title': 'Draft', 'criteria': ['Draft'], 'status': 'done', 'build_run': 'a1',
                  'artifacts': [{'path': 'draft.md'}]},
                 {'id': 't2', 'title': 'Report', 'criteria': ['Report'], 'status': 'done', 'build_run': 'a2',
                  'artifacts': [{'path': 'report.md'}]}]
        attempts = [{'id': 'a1', 'phase': 'build', 'task': 't1', 'outcome': 'reported',
                     'file_changes': [{'path': 'draft.md', 'before': None, 'after': 'x'}]},
                    {'id': 'a2', 'phase': 'build', 'task': 't2', 'outcome': 'reported',
                     'file_changes': [{'path': 'draft.md', 'before': 'x', 'after': None},
                                      {'path': 'report.md', 'before': None, 'after': 'y'}]}]
        m = {'title': 'Report', 'goal': 'Write', 'constraints': '', 'questions': [], 'tasks': tasks,
             'criteria': ['Report exists'], 'attempts': attempts}
        return m, version, objects

    def test_absent_required_artifact_fails_fast_and_names_the_path(self):
        m, version, objects = self.renamed()
        # The controller still requires every recorded path: no model run may start.
        with self.assertRaisesRegex(ArtifactGapError, r'^Review packet: .*draft\.md \(task t1; removed by accepted attempt a2 of task t2\)'):
            build_packet(m, {'phase': 'final'}, None, version, objects.__getitem__, {})
        m['attempts'].pop()
        with self.assertRaises(ArtifactGapError) as raised:
            build_packet(m, {'phase': 'final'}, None, version, objects.__getitem__, {})
        self.assertEqual(raised.exception.gaps, [{'path': 'draft.md', 'task': 't1', 'state': 'missing'}])

    def test_accepted_rename_is_listed_when_the_controller_no_longer_requires_it(self):
        m, version, objects = self.renamed()
        packet, allowed = build_packet(m, {'phase': 'final'}, None, version, objects.__getitem__, {}, expected=['report.md'])
        self.assertEqual(packet['artifacts'], ['report.md'])
        self.assertEqual(packet['missing_artifacts'], [{'path': 'draft.md', 'task': 't1', 'state': 'removed_by_later_change',
                                                        'removed_by_attempt': 'a2', 'removed_in_task': 't2'}])
        self.assertIn('does not require them', ' '.join(packet['notices']))
        self.assertNotIn('draft.md', allowed)

    def test_deletion_by_an_unaccepted_attempt_is_a_lost_deliverable(self):
        for change in ({'outcome': 'interrupted'}, {'outcome': None, 'error': 'Run exceeded time limit.', 'failure_kind': 'time_limit'},
                       {'phase': 'review'}):
            with self.subTest(change=change):
                m, version, _ = self.renamed()
                m['attempts'][1].update(change)
                self.assertEqual(artifact_gaps(m, m['tasks'], version['files']),
                                 [{'path': 'draft.md', 'task': 't1', 'state': 'missing', 'deleted_by_attempt': 'a2'}])
        # An accepted build whose task has not passed review, or that recorded no replacement, is not a restructuring.
        for task_change in ({'status': 'review'}, {'status': 'pending'}, {'build_run': 'a9'}, {'artifacts': [{'path': 'other.md'}]}):
            with self.subTest(task_change=task_change):
                m, version, _ = self.renamed()
                m['tasks'][1].update(task_change)
                self.assertEqual(artifact_gaps(m, m['tasks'], version['files'])[0]['state'], 'missing')

    def test_task_review_keeps_the_source_bound_and_final_review_does_not(self):
        bodies = {f'out/file-{i}.md': f'Part {i}'.encode() for i in range(MAX_SOURCES + 1)}
        version, objects = self.objects_for(bodies)
        task = {'id': 't', 'title': 'Many', 'criteria': ['Done'], 'status': 'review', 'artifacts': [{'path': p} for p in bodies]}
        m = {'title': 'Many', 'goal': 'Deliver', 'constraints': '', 'questions': [], 'tasks': [task], 'criteria': ['Done']}
        with self.assertRaisesRegex(ValueError, f'^Review packet: .*at most {MAX_SOURCES}'):
            build_packet(m, {'phase': 'review'}, task, version, objects.__getitem__, {})
        with self.assertRaisesRegex(ValueError, 'save the report again'):
            check_artifact_count(list(bodies))
        check_artifact_count(list(bodies)[:MAX_SOURCES])
        packet, _ = build_packet(m, {'phase': 'final'}, None, version, objects.__getitem__, {})
        self.assertEqual(len(packet['artifacts']), MAX_SOURCES + 1)

    def test_long_missing_paths_are_clipped_and_the_packet_fits(self):
        long = lambda i: '/'.join([f'{i}' + 'd' * 241] * 9)
        version, objects = self.objects_for({'final.md': b'Final'})
        tasks = [{'id': f't{i}', 'title': 'T', 'criteria': ['c'], 'status': 'done', 'artifacts': [{'path': long(i)}]} for i in range(10)]
        tasks.append({'id': 'last', 'title': 'Final', 'criteria': ['c'], 'status': 'done', 'artifacts': [{'path': 'final.md'}]})
        m = {'title': 'Long', 'goal': 'G', 'constraints': '', 'questions': [], 'tasks': tasks, 'criteria': ['c'], 'attempts': []}
        with self.assertRaises(ArtifactGapError) as raised:
            build_packet(m, {'phase': 'final'}, None, version, objects.__getitem__, {})
        self.assertLess(len(str(raised.exception)), 2000)
        packet, _ = build_packet(m, {'phase': 'final'}, None, version, objects.__getitem__, {}, expected=['final.md'])
        self.assertLessEqual(len(encoded(packet)), PACKET_BYTES)
        self.assertEqual(packet['artifacts'], ['final.md'])
        self.assertTrue(all(g.get('path_truncated') and len(g['path'].encode()) <= 300 for g in packet['missing_artifacts']))
        self.assertEqual(len(packet['missing_artifacts']) + packet.get('omitted_missing_artifacts', 0), 10)

    def test_pathological_brief_answers_claims_and_checks_still_fit_with_evidence(self):
        version, objects = self.objects_for({'out.md': ('Evidence line. ' * 400).encode()})
        tasks = [{'id': f't{i}', 'title': 'T' * 200, 'criteria': ['c'], 'status': 'done', 'summary': 'S' * 5000,
                  'artifacts': [{'path': 'out.md'}]} for i in range(40)]
        questions = [{'question': 'Q' * 3000, 'answer': 'A' * 12000, 'task': None} for _ in range(30)]
        independent = {'id': 'v', 'status': 'passed', 'checks': [
            {'label': 'x', 'argv': ['python', '-c', 'p' * 4000] * 30, 'exit_code': 0, 'log_tail': 'L' * 1600}
            for _ in range(20)]}
        m = {'title': 'Big', 'goal': 'G' * 12000, 'constraints': 'C' * 12000, 'sources': 'S' * 12000,
             'questions': questions, 'tasks': tasks, 'criteria': ['K' * 3000 for _ in range(40)]}
        packet, allowed = build_packet(m, {'phase': 'final'}, None, version, objects.__getitem__, independent)
        self.assertLessEqual(len(encoded(packet)), PACKET_BYTES)
        for flag in ('goal_truncated', 'constraints_truncated', 'criteria_truncated'):
            self.assertTrue(packet[flag], flag)
        self.assertEqual(len(packet['criteria']), 40)
        self.assertEqual(packet['omitted_owner_answers'], 29)
        self.assertIn('out.md', allowed)
        self.assertEqual(packet['excerpts'][0]['path'], 'out.md')
        self.assertIn('shortened', packet['notices'][-1])
        # Caller data stays complete; only the packet copy is reduced.
        self.assertEqual(len(independent['checks'][0]['log_tail']), 1600)

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

    def test_bounded_reader_needs_no_write_approval(self):
        from apodex.agent_tools import assess_tool_risk, RISK_SAFE, RISK_CONFIRM
        self.assertEqual(assess_tool_risk('read_review_evidence', {'path': 'evidence.md'}, '/tmp').level, RISK_SAFE)
        # A similarly named unknown tool cannot inherit that permission.
        self.assertEqual(assess_tool_risk('read_review_evidence_and_write', {}, '/tmp').level, RISK_CONFIRM)


class ReviewControllerTests(unittest.TestCase):
    setUp = test_missions.MissionTests.setUp
    launch = test_missions.MissionTests.launch
    stop = test_missions.MissionTests.stop
    create = test_missions.MissionTests.create
    start = test_missions.MissionTests.start
    current = test_missions.MissionTests.current
    finish = test_missions.MissionTests.finish
    begin_build = test_missions.MissionTests.begin_build

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

    def test_large_essential_input_is_shortened_visibly_and_review_starts(self):
        self.begin_build()
        m = self.current(); m['constraints'] = 'x' * 30000
        self.controller.save(m)
        m = self.finish(report())
        self.assertNotEqual(m['status'], 'blocked')
        self.assertEqual(m['attempts'][-1]['phase'], 'review')
        self.assertIsNotNone(m['active_attempt'])
        prompt = self.launched[-1][0]['task']
        packet, _ = json.JSONDecoder().raw_decode(prompt[prompt.index('{"version"'):])
        self.assertTrue(packet['constraints_truncated'])
        self.assertLessEqual(len(encoded(packet)), PACKET_BYTES)
        self.assertIn('constraints', ' '.join(packet['notices']))
