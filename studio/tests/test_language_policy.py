"""English-only guard for shipped source, UI, prompts, templates, tests and documentation.

The owner requires English everywhere. This test fails when Czech-specific letters or common
Czech words (with or without accents) appear in shipped text files. Historical evidence
(analysis/), build output (dist/), runtime state and third-party vendor/benchmark data are out of
scope. The rejected words are stored as SHA-256 prefixes, shared with scripts/security_scan.py, so
the guard itself stays English.
"""
import functools
import hashlib
import json
import os
import re
import tempfile
import unicodedata
import unittest
from pathlib import Path

from scripts.security_scan import (
    CZECH_LETTERS,
    CZECH_WORD_DIGESTS,
    LANGUAGE_EXEMPT_PREFIXES,
    LANGUAGE_SUFFIXES,
)

ROOT = Path(__file__).resolve().parents[2]
DIRECTIVE = ("Write all reports, questions, summaries, notes and generated documentation in English, "
             "regardless of the language of the input.")
# The letters, words, file suffixes and exempt paths are shared with the publication scan in
# scripts/security_scan.py, so both English-only guards give the same verdict. The two supplements
# below (the letter y-acute and more high-frequency words, all checked for zero English or code hits
# in this tree) are pending adoption there; the union keeps this guard at least as strict meanwhile.
PENDING_LETTERS = re.compile('[\u00fd\u00dd]')
PENDING_WORD_DIGESTS = frozenset({
    '03357475c36c', '0c442ecc8f0b', '12c6c54fae28', '1d5071332265', '2743e3d563c8', '27dbadfc4cb9',
    '2c96f75b2822', '2e577c7b0888', '2ffe317df468', '302d78064d7f', '31f7a65e3155', '3f6daeb8bcbe',
    '4164e8dd0ac9', '416902672a96', '416cf2d81949', '487123b0dc9b', '5f219f352318', '68652da40a81',
    '73b5e7a83751', '7a04984f03bf', '7c8351cf0813', '7dbf60684260', '7ef455edf4ce', '80900cb01986',
    '827ca2234851', '8483f9f5b7b0', '8a0fedfd02f8', '8a6294b74f5a', '9c9910c2aa68', 'a2af72482315',
    'a4488dba99c4', 'a60849ff6997', 'a78b3492514f', 'affe552d7c20', 'b617c0f11259', 'b7277fd853f5',
    'b7cebae23b90', 'bc12b84bc41c', 'c97eb22fb71c', 'cb8930ba0c14', 'd14f69b3218c', 'd688cfff7e90',
    'd8305a064cd0', 'ddbd551dabcb', 'f628421129af', 'fa606d989abe',
})
WORD_DIGESTS = CZECH_WORD_DIGESTS | PENDING_WORD_DIGESTS
SKIP_DIRS = {'.git', '.venv', 'venv', 'node_modules', '__pycache__', '.switch-agent', '.apodex', 'analysis',
             'dist', 'vendor', 'benchmarks', '.pytest_cache', '.ruff_cache', '.mypy_cache'}
LOCAL_FILES = {'agent.toml', 'ssh-targets.json'}  # operator configuration, never shipped

def shipped_text_files():
    for dirpath, dirs, files in os.walk(ROOT):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.endswith('.egg-info')
                         and (not d.startswith('.') or d == '.github'))
        top = Path(dirpath) == ROOT
        for name in sorted(set(files) - LOCAL_FILES):
            path = Path(dirpath) / name
            suffix = os.path.splitext(name)[1].casefold()
            if (suffix in LANGUAGE_SUFFIXES and (suffix or top)  # extensionless files: root launchers only
                    and not path.relative_to(ROOT).as_posix().startswith(LANGUAGE_EXEMPT_PREFIXES)):
                yield path


@functools.lru_cache(maxsize=None)
def rejected(word):
    return hashlib.sha256(word.encode()).hexdigest()[:12] in WORD_DIGESTS


def czech_findings(path):
    try:
        text = path.read_text(encoding='utf-8')
    except (UnicodeDecodeError, OSError):
        return []
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        plain = ''.join(c for c in unicodedata.normalize('NFKD', line.lower()) if not unicodedata.combining(c))
        if CZECH_LETTERS.search(line) or PENDING_LETTERS.search(line) or any(map(rejected, re.findall(r'[a-z]+', plain))):
            found.append(f'{os.path.relpath(path, ROOT)}:{number}: {line.strip()[:120]}')
    return found


class LanguagePolicyTests(unittest.TestCase):
    def test_shipped_text_is_english(self):
        found = [hit for path in shipped_text_files() for hit in czech_findings(path)]
        self.assertEqual(found, [], 'Czech text found; translate it to English:\n' + '\n'.join(found))

    def test_guard_detects_accented_and_plain_czech(self):
        # Escapes keep the probe words out of this file's own source text.
        czech = ('Pro\u010d', 'Start selh\x61l.', 'odm\u00edtnut', 'Dobr\u00fd den, m\u00e1me hotov\x6fu pr\u00e1ci.',
                 '\x50ridej \x6eovy \x73oubor.', '\x54oto je test.', '\x43hybi \x6blic API.',
                 '\x53pust testy a over \x76ysledky.', '\x48otova \x70race.')
        english = ('All checks passed.', 'Take the protocol over to the testing den.', 'Caf\u00e9 menu in Z\u00fcrich.')
        with tempfile.TemporaryDirectory() as temp:
            sample = Path(temp) / 'probe.md'
            for line in czech + english:
                sample.write_text(line + '\n', encoding='utf-8')
                self.assertEqual(bool(czech_findings(sample)), line in czech, line)

    def test_role_template_requires_english_output(self):
        template = json.loads((ROOT / 'studio' / 'templates' / 'ai-build-company.json').read_text(encoding='utf-8'))
        self.assertIn(DIRECTIVE, template['operating_rules'])
        for role in template['roles']:
            self.assertIn(DIRECTIVE, role['instructions'], role['id'])

    # PROJECT.md is the development checkout's agent guide; release archives do not ship it.
    @unittest.skipUnless((ROOT / 'PROJECT.md').exists(), 'PROJECT.md exists only in the development checkout')
    def test_project_guide_requires_english_output(self):
        self.assertIn(DIRECTIVE, (ROOT / 'PROJECT.md').read_text(encoding='utf-8'))

    def test_docs_do_not_preserve_non_english_history(self):
        for doc in ('README.md', 'studio/docs/OFFICE.md', 'studio/docs/FEATURES.md'):
            self.assertNotIn('original language', (ROOT / doc).read_text(encoding='utf-8').lower(), doc)


if __name__ == '__main__':
    unittest.main()
