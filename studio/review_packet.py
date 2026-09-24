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


def rank_sources(files, query, artifacts):
    words = set(re.findall(r'[\w-]{3,}', query.lower()))
    def key(path):
        matches = len(words & set(re.findall(r'[\w-]{3,}', path.lower())))
        return (path not in artifacts, -matches, classify_path(path) not in {'evidence', 'instructions'}, path)
    return sorted((p for p in files if PurePosixPath(p).suffix.lower() in TEXT_SUFFIXES
                   and not p.startswith(('company/projects/', '.apodex/'))), key=key)


def build_packet(mission, attempt, task, version, read_object, independent):
    tasks = [task] if task else mission['tasks']
    criteria = task['criteria'] if task else mission['criteria']
    artifacts = sorted({a['path'] for t in tasks for a in t.get('artifacts', [])})
    files = version['files']
    if any(p not in files for p in artifacts):
        raise ValueError('Review packet: a delivered artifact is absent from the source snapshot.')
    query = ' '.join(criteria) + ' ' + (task['title'] if task else mission['title'])
    ranked = rank_sources(files, query, set(artifacts))
    chosen = list(dict.fromkeys(artifacts + ranked))[:MAX_SOURCES]
    if len(artifacts) > MAX_SOURCES:
        raise ValueError('Review packet: split delivery into tasks with at most 32 artifacts.')
    packet = {'version': 1, 'snapshot': version['id'], 'phase': attempt['phase'],
              'criteria': criteria, 'constraints': mission['constraints'],
              'goal': mission['goal'], 'sources_note': clip(mission.get('sources', ''), 1200)[0],
              'sources_note_truncated': clip(mission.get('sources', ''), 1200)[1],
              'owner_answers': [{'question': q['question'], 'answer': q['answer']} for q in mission['questions']
                                if q.get('answer') is not None and (not task or q.get('task') in {None, task['id']})],
              'worker_claims': [{'title': t['title'], 'summary': clip(t.get('summary', ''), 420 if task else 300)[0],
                                'summary_truncated': clip(t.get('summary', ''), 420 if task else 300)[1],
                                'review_status': t.get('status')} for t in tasks],
              'independent': independent, 'artifacts': artifacts, 'source_index': [], 'excerpts': [],
              'omitted_source_count': len(files) - len(chosen),
              'limits': {'extra_reads': READ_CALLS, 'bytes_per_read': READ_BYTES}}
    allowed = {}
    for p in chosen:
        raw = read_object(files[p])
        allowed[p] = {'sha256': files[p], 'bytes': len(raw), 'kind': classify_path(p)}
        # Exact hashes stay in controller metadata and validate every read/pass.
        # Repeating opaque digests in the model prompt adds tokens, not evidence.
        packet['source_index'].append({'path': p, 'bytes': len(raw), 'kind': allowed[p]['kind']})
    if len(encoded(packet)) > PACKET_BYTES - 1000:
        raise ValueError('Review packet exceeds its fixed budget. Split the task; required criteria and constraints were not silently truncated.')
    inline_budget = 4000 if mission.get("process", {}).get("effective") == "light" else INLINE_BYTES
    remaining = min(inline_budget, PACKET_BYTES - len(encoded(packet)) - 1000)
    # Artifacts first; only short selected documentation follows. Snapshot identity
    # and truncation remain visible. No claim of completeness is manufactured.
    for p in chosen:
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
