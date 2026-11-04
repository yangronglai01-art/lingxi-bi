"""Offline P0 preparation/validation. Never prints credentials or deploys resources."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.enterprise.acceptance import governance_errors, evidence_errors, load_json, template


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='action', required=True)
    init = sub.add_parser('init'); init.add_argument('--out', required=True)
    check = sub.add_parser('check')
    for name in ('catalog', 'bundle', 'tenant', 'release', 'model-revision', 'embedding-revision', 'enabled-metrics', 'out'):
        check.add_argument('--' + name, required=True)
    args = p.parse_args()
    if args.action == 'init':
        with Path(args.out).open('x', encoding='utf-8') as f: json.dump(template(), f, ensure_ascii=False, indent=2)
        print('Created pending acceptance record; no approval inferred.'); return
    try:
        catalog = load_json(args.catalog); bundle = load_json(args.bundle)
        errors = governance_errors(catalog, args.enabled_metrics.split(','))
        errors += evidence_errors(bundle, Path(args.bundle).parent, tenant=args.tenant, release=args.release,
            model_revision=args.model_revision, embedding_revision=args.embedding_revision)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        errors = ['Acceptance inputs missing or malformed; values omitted']
    result = dict(passed=not errors, errors=errors, note='Offline evidence validation; not an infrastructure probe or approval signature.')
    with Path(args.out).open('x', encoding='utf-8') as f: json.dump(result, f, ensure_ascii=False, indent=2)
    print('P0 evidence check: ' + ('passed' if not errors else 'blocked') + '; details in output file')
    if errors: raise SystemExit(2)


if __name__ == '__main__': main()
