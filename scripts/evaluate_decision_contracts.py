"""Deterministic adversarial contract checks, explicitly not a model-quality benchmark."""
from __future__ import annotations
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from studio.judgments import validate_choice, validate_answers
from studio.decision_lab import questions_for, combine


def evaluate():
    results = []
    def case(name, fn, expected):
        try:
            actual = fn()
        except (ValueError, TypeError, KeyError):
            actual = 'rejected'
        results.append({'case': name, 'expected': expected, 'actual': actual, 'passed': actual == expected})
    for count in range(2, 12):
        options = [f'build:task-{i}' for i in range(count)]
        base = {'action': options[-1], 'confidence': .9, 'reason': 'A bounded proposal'}
        for label, mutation, expected in [
            ('eligible', {}, options[-1]), ('unknown-action', {'action': 'deploy:production'}, 'rejected'),
            ('low-confidence', {'confidence': .2}, 'rejected'), ('boolean-confidence', {'confidence': True}, 'rejected'),
            ('nan-confidence', {'confidence': float('nan')}, 'rejected'), ('missing-rationale', {'reason': ''}, 'rejected'),
            ('unhashable-action', {'action': ['build:task-1']}, 'rejected'),
        ]:
            case(f'{count}-options/{label}', lambda b={**base, **mutation}, o=options: validate_choice(b, o, .8), expected)
    question = {'supported': {'type': 'noul', 'instructions': 'Supported?'}}
    for value in [True, 'yes', None, -1, 2, float('nan'), float('inf')]:
        case('noul/reject-' + str(value), lambda v=value: validate_answers({'supported': {'type': 'noul', 'noul': v}}, question), 'rejected')
    answers = {**{k: {'type': 'score', 'score': 3, 'confidence': .99} for k in ('problem','demand','feasibility','differentiation','revenue')},
               'missing_evidence': {'type': 'noul', 'noul': .8}}
    case('idea/missing-evidence-veto', lambda: combine('idea', validate_answers(answers, questions_for('idea')))['recommendation'], 'validate')
    case('batch/missing-axis', lambda: validate_answers({}, questions_for('document')), 'rejected')
    return {'kind': 'deterministic_contract_evaluation', 'model_requests': 0,
            'claim': 'Validates code boundaries only; no measured model accuracy, quality, throughput or financial return.',
            'cases': results, 'passed': sum(c['passed'] for c in results), 'total': len(results)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--output', type=Path); args = parser.parse_args()
    started = time.monotonic(); report = evaluate(); report['elapsed_seconds'] = time.monotonic() - started
    if args.output: args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'cases'}, indent=2))
    raise SystemExit(0 if report['passed'] == report['total'] else 1)
