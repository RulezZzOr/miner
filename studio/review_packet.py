"""Bounded, immutable review evidence. File content is data, never authority."""
from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import PurePosixPath

PACKET_BYTES = 24000
INLINE_BYTES = 8000
READ_BYTES = 3000
READ_CALLS = 2
MAX_SOURCES = 32
# Everything except excerpts is shortened until it leaves this much room for file evidence.
BODY_BYTES = PACKET_BYTES - 1000 - 5000
MAX_ANSWERS = 8
MAX_MISSING = 10
TEXT_SUFFIXES = {'.md', '.txt', '.json', '.py', '.js', '.ts', '.tsx', '.jsx', '.html', '.css', '.toml', '.yaml', '.yml', '.csv', '.log'}


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8')


def clip(value, limit):
    raw = str(value).encode('utf-8')
    return raw[:limit].decode('utf-8', errors='ignore'), len(raw) > limit


def classify_path(path):
    """Deterministic routing hints, not a semantic assertion about the content."""
    name = PurePosixPath(path).name.lower()
    if name.startswith('test') or '/tests/' in '/' + path.lower() or name.endswith('.test.ts'):
        return 'test'
    if name in {'project.md', 'owner_directives.md', 'readme.md'} or 'architecture' in name:
        return 'instructions'
    if any(word in name for word in ['evidence', 'inventory', 'source', 'snapshot']):
        return 'evidence'
    return 'document' if PurePosixPath(path).suffix.lower() in {'.md', '.txt'} else 'source'


def answer_record(q, question_bytes, answer_bytes):
    question, cut_question = clip(q['question'], question_bytes)
    answer, cut_answer = clip(q['answer'], answer_bytes)
    return {'question': question, 'answer': answer, **({'truncated': True} if cut_question or cut_answer else {})}


def fit_body(packet, allowed, artifacts):
    """Shorten the packet body in a fixed order until it leaves room for excerpts.

    Returns the names of shortened fields. Every step keeps the field present and
    marks the cut, so the reviewer sees what was reduced.
    """
    shortened = []
    size = lambda: len(encoded(packet))

    def shorten(key, limit):
        value, cut = clip(packet[key], limit)
        if cut:
            packet[key] = value
            packet[key + '_truncated'] = True
        return cut

    def answers(count, question_bytes, answer_bytes):
        before = encoded(packet['owner_answers'])
        items = packet['owner_answers']
        if len(items) > count:
            packet['omitted_owner_answers'] = packet.get('omitted_owner_answers', 0) + len(items) - count
            items = items[len(items) - count:]
        packet['owner_answers'] = [answer_record(q, question_bytes, answer_bytes) | ({'truncated': True} if q.get('truncated') else {})
                                   for q in items]
        return encoded(packet['owner_answers']) != before

    def checks(log_bytes, arg_bytes):
        before = encoded(packet['independent'])
        items = packet['independent'].get('checks') if isinstance(packet['independent'], dict) else None
        for check in items if isinstance(items, list) else []:
            if not isinstance(check, dict):
                continue
            if isinstance(check.get('log_tail'), str) and len(check['log_tail'].encode()) > log_bytes:
                # Keep the end of a log: failures are reported last.
                check['log_tail'] = check['log_tail'].encode()[-log_bytes:].decode('utf-8', errors='ignore') if log_bytes else ''
                check['log_truncated'] = True
            if isinstance(check.get('argv'), list) and len(encoded(check['argv'])) > arg_bytes * 2:
                check['argv'] = [clip(a, arg_bytes)[0] for a in check['argv'][:8]] if arg_bytes else []
                check['argv_truncated'] = True
            if isinstance(check.get('label'), str) and len(check['label'].encode()) > 120:
                check['label'] = clip(check['label'], 120)[0]
        return encoded(packet['independent']) != before

    def claims(summary_bytes, count=None):
        before = encoded(packet['worker_claims'])
        for claim in packet['worker_claims']:
            title, cut = clip(claim['title'], 120)
            if cut:
                claim.update(title=title, title_truncated=True)
            if len(claim['summary'].encode()) > summary_bytes:
                claim.update(summary=clip(claim['summary'], summary_bytes)[0], summary_truncated=True)
        if count is not None and len(packet['worker_claims']) > count:
            packet['omitted_worker_claims'] = len(packet['worker_claims']) - count
            del packet['worker_claims'][count:]
        return encoded(packet['worker_claims']) != before

    def criteria():
        limit = max(120, 4000 // max(1, len(packet['criteria'])))
        cut = [clip(c, limit) for c in packet['criteria']]
        if not any(flag for _, flag in cut):
            return False
        packet['criteria'] = [c for c, _ in cut]
        packet['criteria_truncated'] = True
        return True

    def artifact_list():
        # Indexed artifacts stay listed; the rest is summarized by count.
        listed = [p for p in packet['artifacts'] if p in allowed]
        if len(listed) < len(packet['artifacts']):
            packet['omitted_artifact_paths'] = len(packet['artifacts']) - len(listed)
            packet['artifacts'] = listed
            return True
        return False

    steps = [('goal', lambda: shorten('goal', 3000)), ('constraints', lambda: shorten('constraints', 3000)),
             ('owner answers', lambda: answers(4, 600, 1200)),
             ('independent check logs', lambda: checks(600, 300)), ('worker claims', lambda: claims(160)),
             ('artifact list', artifact_list), ('goal', lambda: shorten('goal', 1200)),
             ('constraints', lambda: shorten('constraints', 1200)),
             ('sources note', lambda: shorten('sources_note', 400)),
             ('independent check logs', lambda: checks(0, 0)), ('criteria', criteria),
             ('owner answers', lambda: answers(1, 300, 600)), ('worker claims', lambda: claims(0, 12))]
    for name, step in steps:
        if size() <= BODY_BYTES:
            break
        if step() and name not in shortened:
            shortened.append(name)
    # Last resort for pathological inputs: fewer indexed documentation sources,
    # fewer independent check entries, fewer indexed artifacts, fewer listed paths.
    def drop_source(artifact):
        index = [i for i, item in enumerate(packet['source_index']) if (item['path'] in artifacts) == artifact]
        if not index:
            return False
        allowed.pop(packet['source_index'].pop(index[-1])['path'], None)
        packet['omitted_source_count'] += 1
        return True

    def drop_check():
        items = packet['independent'].get('checks') if isinstance(packet['independent'], dict) else None
        if not isinstance(items, list) or not items:
            return False
        items.pop()
        packet['independent']['omitted_checks'] = packet['independent'].get('omitted_checks', 0) + 1
        return True

    def drop_path():
        if not packet['artifacts']:
            return False
        packet['artifacts'].pop()
        packet['omitted_artifact_paths'] = packet.get('omitted_artifact_paths', 0) + 1
        return True

    def drop_missing():
        if not packet.get('missing_artifacts'):
            return False
        packet['missing_artifacts'].pop()
        packet['omitted_missing_artifacts'] = packet.get('omitted_missing_artifacts', 0) + 1
        return True

    for name, step in [('missing artifact list', drop_missing),
                       ('source index', lambda: drop_source(False)), ('independent check list', drop_check),
                       ('source index', lambda: drop_source(True)), ('artifact list', drop_path)]:
        while size() > BODY_BYTES and step():
            if name not in shortened:
                shortened.append(name)
    return shortened


def source_key(query, artifacts):
    words = set(re.findall(r'[\w-]{3,}', query.lower()))
    def key(path):
        matches = len(words & set(re.findall(r'[\w-]{3,}', path.lower())))
        return (path not in artifacts, -matches, classify_path(path) not in {'evidence', 'instructions'}, path)
    return key


def rank_sources(files, query, artifacts):
    return sorted((p for p in files if PurePosixPath(p).suffix.lower() in TEXT_SUFFIXES
                   and not p.startswith(('company/projects/', '.apodex/'))), key=source_key(query, artifacts))


def artifact_count_message(count):
    return (f'This task reports {count} output files, but its independent review can cover at most {MAX_SOURCES}. '
            'Combine closely related files (for example merge small documents or bundle generated data into one file) '
            f'so the task delivers at most {MAX_SOURCES} files, then save the report again.')


def check_artifact_count(paths):
    """A single task's review must index every artifact, so a task delivers at most MAX_SOURCES.

    The controller calls this when it consumes a build report, so the worker restructures
    its delivery before a review is prepared.
    """
    if len(set(paths)) > MAX_SOURCES:
        raise ValueError(artifact_count_message(len(set(paths))))


def accepted_build(mission, attempt):
    """The task of a build attempt that was accepted and then passed its independent review."""
    if attempt.get('phase') != 'build' or attempt.get('outcome') != 'reported':
        return None
    task = next((t for t in mission.get('tasks', []) if t.get('id') == attempt.get('task')), None)
    return task if task and task.get('status') == 'done' and task.get('build_run') == attempt.get('id') else None


def artifact_gaps(mission, tasks, files):
    """Recorded artifacts absent from a snapshot, each with the task that recorded it.

    State 'removed_by_later_change' needs an accepted build of a later task that passed
    review, deleted the path and recorded a replacement artifact of its own. A deletion
    by a failed, interrupted or unreviewed attempt leaves the path 'missing': the
    deliverable was lost, not restructured.
    """
    gaps = []
    attempts = mission.get('attempts', [])
    for t in tasks:
        built = next((i for i, a in enumerate(attempts) if a.get('id') == t.get('build_run')), -1)
        for path in sorted({a['path'] for a in t.get('artifacts', [])} - set(files)):
            deleted = [a for a in attempts[built + 1:]
                       if any(c.get('path') == path and c.get('after') is None for c in a.get('file_changes', []))]
            gap = {'path': path, 'task': t.get('id'), 'state': 'missing'}
            for a in reversed(deleted):
                owner = accepted_build(mission, a)
                written = {c.get('path') for c in a.get('file_changes', []) if c.get('after') is not None}
                if owner and owner.get('id') != t.get('id') and written & {x['path'] for x in owner.get('artifacts', [])}:
                    gap.update(state='removed_by_later_change', removed_by_attempt=a['id'], removed_in_task=owner['id'])
                    break
            else:
                if deleted:
                    gap['deleted_by_attempt'] = deleted[-1].get('id')
            gaps.append(gap)
    return gaps


class ArtifactGapError(ValueError):
    """Required artifacts are absent from the review snapshot; raised before any model run."""
    def __init__(self, gaps):
        self.gaps = gaps
        described = []
        for g in gaps[:5]:
            detail = (f"removed by accepted attempt {g['removed_by_attempt']} of task {g['removed_in_task']}"
                      if g.get('state') == 'removed_by_later_change' else
                      f"deleted by unaccepted attempt {g['deleted_by_attempt']}" if g.get('deleted_by_attempt') else 'missing')
            described.append(f"{clip(g['path'], 200)[0]} (task {g.get('task') or 'unknown'}; {detail})")
        more = f' and {len(gaps) - 5} more' if len(gaps) > 5 else ''
        super().__init__('Review packet: recorded artifacts are absent from the review snapshot: ' + ', '.join(described) + more +
                         '. A review cannot cover an absent file: restore it, or revise the task that recorded it.')


def gap_record(gap):
    path, cut = clip(gap['path'], 300)
    return {**gap, 'path': path, **({'path_truncated': True} if cut else {})}


def build_packet(mission, attempt, task, version, read_object, independent, expected=None):
    """Build a bounded packet. Oversized text is shortened with explicit notices.

    `expected` lists the artifact paths the reviewer must cover; by default every
    artifact recorded by the reviewed tasks. A required path absent from the snapshot
    raises ArtifactGapError, and a single-task review of more than MAX_SOURCES
    artifacts raises ValueError, both before any model run and with the 'Review packet'
    prefix. Recorded paths the controller no longer requires are listed as
    missing_artifacts, never as covered artifacts.
    """
    tasks = [task] if task else mission['tasks']
    criteria = task['criteria'] if task else mission['criteria']
    files = version['files']
    recorded = {a['path'] for t in tasks for a in t.get('artifacts', [])}
    required = set(recorded if expected is None else expected)
    gaps = artifact_gaps(mission, tasks, files)
    known = {g['path']: g for g in gaps}
    absent = [known.get(p, {'path': p, 'task': None, 'state': 'missing'}) for p in sorted(required - set(files))]
    if absent:
        raise ArtifactGapError(absent)
    if task and len(required) > MAX_SOURCES:
        # Only the final review may list more paths than it can index.
        raise ValueError('Review packet: ' + artifact_count_message(len(required)))
    excused = [g for g in gaps if g['path'] not in required]
    query = ' '.join(criteria) + ' ' + (task['title'] if task else mission['title'])
    artifacts = sorted(required)
    ranked = rank_sources(files, query, set(artifacts))
    # Artifacts come first; a final delivery with more than MAX_SOURCES outputs lists
    # every path but indexes only the best-matching ones.
    chosen = list(dict.fromkeys(sorted(artifacts, key=source_key(query, set())) + ranked))[:MAX_SOURCES]
    answers = [q for q in mission['questions']
               if q.get('answer') is not None and (not task or q.get('task') in {None, task['id']})]
    recent = answers[-MAX_ANSWERS:]
    packet = {'version': 1, 'snapshot': version['id'], 'phase': attempt['phase'],
              'criteria': list(criteria), 'constraints': mission['constraints'],
              'goal': mission['goal'], 'sources_note': clip(mission.get('sources', ''), 1200)[0],
              'sources_note_truncated': clip(mission.get('sources', ''), 1200)[1],
              'owner_answers': [answer_record(q, 1500, 4000) for q in recent],
              'worker_claims': [{'title': t['title'], 'summary': clip(t.get('summary', ''), 420 if task else 300)[0],
                                'summary_truncated': clip(t.get('summary', ''), 420 if task else 300)[1],
                                'review_status': t.get('status')} for t in tasks],
              'independent': json.loads(json.dumps(independent)), 'artifacts': list(artifacts),
              'source_index': [], 'excerpts': [],
              'omitted_source_count': len(files) - len(chosen),
              'limits': {'extra_reads': READ_CALLS, 'bytes_per_read': READ_BYTES}}
    notices = []
    if len(answers) > len(recent):
        packet['omitted_owner_answers'] = len(answers) - len(recent)
    if excused:
        packet['missing_artifacts'] = [gap_record(g) for g in excused[:MAX_MISSING]]
        if len(excused) > MAX_MISSING:
            packet['omitted_missing_artifacts'] = len(excused) - MAX_MISSING
        notices.append('Recorded artifacts listed under missing_artifacts are absent from this snapshot, and the controller '
                       'does not require them for this review (each entry states why). Judge whether the delivered product '
                       'still meets the criteria and name any file that must be restored. Do not list these paths as covered artifacts.')
    allowed = {}
    for p in chosen:
        raw = read_object(files[p])
        allowed[p] = {'sha256': files[p], 'bytes': len(raw), 'kind': classify_path(p)}
        # Exact hashes stay in controller metadata and validate every read/pass.
        # Repeating opaque digests in the model prompt adds tokens, not evidence.
        packet['source_index'].append({'path': p, 'bytes': len(raw), 'kind': allowed[p]['kind']})
    shortened = fit_body(packet, allowed, artifacts)
    unindexed = [p for p in artifacts if p not in allowed]
    if unindexed:
        packet['omitted_artifact_count'] = len(unindexed)
        notices.append(f'{len(unindexed)} delivered artifacts have no excerpt or read access because the packet indexes at most '
                       f'{MAX_SOURCES} sources. Treat them as unreviewed; do not claim they were checked.')
    if shortened:
        notices.append('This packet exceeded its fixed budget, so these fields were shortened: ' + ', '.join(shortened) +
                       '. The complete brief stays in the controller record. Shortened text is not evidence of absence and '
                       'does not relax any criterion; the exact criterion wording is in the report template.')
    if notices:
        packet['notices'] = notices
    if len(encoded(packet)) > PACKET_BYTES - 1000:
        # fit_body bounds every field, so this is a programming error, not a brief problem.
        raise RuntimeError('Review evidence could not be reduced to its fixed budget.')
    inline_budget = 4000 if mission.get("process", {}).get("effective") == "light" else INLINE_BYTES
    remaining = min(inline_budget, PACKET_BYTES - len(encoded(packet)) - 1000)
    # Artifacts first; only short selected documentation follows. Snapshot identity
    # and truncation remain visible. No claim of completeness is manufactured.
    for p in [p for p in chosen if p in allowed]:
        if remaining < 300 or (p not in artifacts and len(packet['excerpts']) >= len(artifacts) + 2):
            break
        raw = read_object(files[p])
        try:
            content = raw.decode('utf-8')
            if '\0' in content:
                continue
        except UnicodeDecodeError:
            continue
        excerpt, truncated = clip(content, min(remaining, 4500 if p in artifacts else 1600))
        record = {'path': p, 'text': excerpt, 'truncated': truncated, 'offset': 0}
        if len(encoded({**packet, 'excerpts': packet['excerpts'] + [record]})) > PACKET_BYTES - 100:
            break
        packet['excerpts'].append(record)
        remaining -= len(excerpt.encode('utf-8'))
    packet['id'] = hashlib.sha256(encoded(packet)).hexdigest()
    return packet, allowed


class ReviewEvidence:
    """Only snapshot files, two bounded reads, including failed attempts."""
    def __init__(self, allowed, read_object):
        self.allowed, self.read_object = allowed, read_object
        self.calls = 0
        self.lock = threading.Lock()

    def read(self, path, offset=0):
        with self.lock:
            if self.calls >= READ_CALLS:
                raise ValueError('Review evidence budget exhausted. Submit changes with the missing evidence; do not keep exploring.')
            self.calls += 1
        if not isinstance(path, str) or path not in self.allowed or type(offset) is not int or offset < 0:
            raise ValueError('Choose an exact path from the supplied source index and a non-negative byte offset.')
        item = self.allowed[path]
        raw = self.read_object(item['sha256'])
        if hashlib.sha256(raw).hexdigest() != item['sha256']:
            raise ValueError('Evidence snapshot checksum mismatch.')
        if offset > len(raw):
            raise ValueError('Offset is beyond this evidence file.')
        text = raw[offset:offset + READ_BYTES].decode('utf-8', errors='replace')
        if '\0' in text:
            raise ValueError('Binary evidence requires a separately supplied observable result.')
        return json.dumps({'path': path, 'sha256': item['sha256'], 'offset': offset,
                           'next_offset': min(offset + READ_BYTES, len(raw)),
                           'truncated': offset + READ_BYTES < len(raw), 'text': text,
                           'reads_remaining': max(0, READ_CALLS - self.calls)}, ensure_ascii=False)

    def tool(self):
        from frontier_agent.core.tool import Tool
        async def read(path, offset=0):
            try:
                return self.read(path, offset)
            except ValueError as exc:
                return 'Evidence unavailable: ' + str(exc)
        return Tool(name='read_review_evidence', description='Read a small excerpt from the immutable review snapshot. Only exact source-index paths. At most two reads; after that submit a verdict or missing-evidence correction.',
                    parameters={'type': 'object', 'properties': {'path': {'type': 'string'}, 'offset': {'type': 'integer', 'minimum': 0}}, 'required': ['path'], 'additionalProperties': False}, func=read)
