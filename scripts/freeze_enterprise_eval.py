"""Validate and freeze independent evaluation sets before inference; no model calls."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.enterprise.acceptance import sha256, confirmed


def normalized(question): return ''.join(question.casefold().split())


def read_cases(path, profile='enterprise'):
    if profile not in {'enterprise','synthetic'}: raise ValueError('Unknown evaluation profile')
    path = Path(path)
    if path.stat().st_size > 5_000_000: raise ValueError('Case file exceeds 5MB')
    cases = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
    if not 1 <= len(cases) <= 500: raise ValueError('Need 1..500 cases')
    if len({c['id'] for c in cases}) != len(cases): raise ValueError('Duplicate case IDs')
    if len({normalized(c['question']) for c in cases}) != len(cases): raise ValueError('Duplicate normalized questions')
    for c in cases:
        if c.get('difficulty') not in {'simple', 'join', 'complex', 'behavior'}: raise ValueError('Unknown difficulty')
        if c.get('expected_status') not in {'ok', 'clarification', 'denied'}: raise ValueError('Unknown reference status')
        for field in ('id', 'question', 'group_id', 'reference_owner', 'reference_provenance', 'source_kind'):
            if not confirmed(c.get(field)): raise ValueError('Case reference metadata missing')
        if type(c.get('requires_model')) is not bool: raise ValueError('Generation path must be explicit')
        if c['expected_status'] == 'ok':
            columns, rows = c.get('expected_columns'), c.get('expected_rows')
            if not isinstance(columns, list) or not columns or len(set(columns)) != len(columns): raise ValueError('Reference columns required')
            if not isinstance(rows, list) or any(not isinstance(r, list) or len(r) != len(columns) for r in rows): raise ValueError('Independent reference rows required')
        if c['source_kind'] != ('enterprise' if profile=='enterprise' else 'synthetic-benchmark'): raise ValueError('Case provenance does not match explicit evaluation profile')
        if c['expected_status']=='denied' and c.get('zero_tolerance') is not True: raise ValueError('Security denial cases must be zero tolerance')
    return cases


def freeze(cases_path, dev_path, criteria_path, out, profile='enterprise'):
    cases, dev = read_cases(cases_path, profile), read_cases(dev_path, profile)
    for field in ('id', 'group_id'):
        if {c[field] for c in cases} & {c[field] for c in dev}: raise ValueError('Development/holdout overlap: ' + field)
    if {normalized(c['question']) for c in cases} & {normalized(c['question']) for c in dev}: raise ValueError('Development/holdout question overlap')
    criteria = json.loads(Path(criteria_path).read_text(encoding='utf-8'))
    if not confirmed(criteria.get('owner')) or not confirmed(criteria.get('approval_reference')): raise ValueError('Preapproved criteria required')
    if profile=='synthetic' and criteria.get('purpose')!='synthetic_engineering_only': raise ValueError('Synthetic criteria must explicitly forbid production acceptance')
    if profile=='enterprise' and criteria.get('purpose')=='synthetic_engineering_only': raise ValueError('Synthetic criteria cannot approve enterprise acceptance')
    for difficulty in ('simple', 'join', 'complex', 'behavior'):
        rule = criteria['by_difficulty'][difficulty]
        for field in ('min_correct_rate', 'max_wrong_answer_rate'):
            if type(rule.get(field)) not in (int, float) or not 0 <= rule[field] <= 1: raise ValueError('Invalid quality threshold')
        if type(rule.get('min_cases')) is not int or rule['min_cases'] <= 0: raise ValueError('Positive sample target required')
        if type(rule.get('max_p95_seconds')) not in (int,float) or not 0 < rule['max_p95_seconds'] <= 3600: raise ValueError('Approved finite latency budget required')
        if sum(c['difficulty'] == difficulty for c in cases) < rule['min_cases']: raise ValueError('Insufficient frozen samples')
    manifest = dict(schema_version=1, evaluation_profile=profile, production_eligible=(profile=='enterprise'), cases_sha256=sha256(cases_path), dev_sha256=sha256(dev_path), criteria_sha256=sha256(criteria_path),
        total=len(cases), by_difficulty={d:sum(c['difficulty']==d for c in cases) for d in ('simple','join','complex','behavior')},
        note='Freeze before model inference. Human review must verify independent answers and semantic group assignments; hashes do not prove independence.')
    with Path(out).open('x', encoding='utf-8') as f: json.dump(manifest, f, ensure_ascii=False, indent=2)
    return manifest


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('cases', 'dev-cases', 'criteria', 'out'): p.add_argument('--'+name, required=True)
    p.add_argument('--profile', choices=('enterprise','synthetic'), default='enterprise')
    a = p.parse_args()
    try: freeze(a.cases, a.dev_cases, a.criteria, a.out, a.profile)
    except (ValueError, OSError, KeyError, TypeError): raise SystemExit('Freeze rejected: invalid inputs, overlap or incomplete approval; values omitted') from None
    print('Evaluation manifest frozen; no inference performed.')


if __name__ == '__main__': main()
