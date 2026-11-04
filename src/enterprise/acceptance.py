"""Offline production acceptance checks. Records are reviewed evidence, not signatures."""
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PACKAGES = tuple(f'P0-{n:02d}' for n in range(1, 8))


def confirmed(value):
    return isinstance(value, str) and bool(value.strip()) and not any(
        word in value.lower() for word in ('unconfirmed', 'replace_', 'placeholder', 'technical_contract', '待确认', '待填写'))


def governance_errors(catalog, enabled):
    errors = []
    governance = catalog.get('governance', {})
    if catalog.get('synthetic') is not False or governance.get('status') != 'approved' or not confirmed(governance.get('approval_id')):
        errors.append('catalog: real source and approved governance required')
    metrics = catalog.get('metrics', [])
    if not isinstance(metrics, list) or len({m.get('id') for m in metrics}) != len(metrics):
        return errors + ['catalog: unique metric IDs required']
    by_id = {m.get('id'): m for m in metrics}
    if not enabled or len(set(enabled)) != len(enabled):
        errors.append('catalog: nonempty unique enabled metrics required')
    for mid in enabled:
        metric = by_id.get(mid)
        if not metric:
            errors.append(f'{mid}: missing metric'); continue
        prefix = str(mid) + ': '
        if metric.get('approval_status') != 'approved': errors.append(prefix + 'Owner approval required')
        for field in ('owner', 'approval_id', 'definition', 'grain', 'version', 'unit', 'source_kind'):
            if not confirmed(metric.get(field)): errors.append(prefix + field + ' must be confirmed')
        if metric.get('source_kind') != 'enterprise': errors.append(prefix + 'enterprise source required')
        try: ZoneInfo(metric.get('timezone', ''))
        except (ZoneInfoNotFoundError, ValueError, TypeError): errors.append(prefix + 'IANA timezone required')
        currency = metric.get('currency')
        if currency is not None and (not isinstance(currency, str) or not re.fullmatch('[A-Z]{3}', currency)):
            errors.append(prefix + 'currency must be a confirmed three-letter code or null')
        if metric.get('unit') == 'CNY' and currency != 'CNY': errors.append(prefix + 'CNY monetary unit requires CNY currency')
        if not isinstance(metric.get('approved_dimensions'), list): errors.append(prefix + 'approved dimensions required')
        if type(metric.get('freshness_sla_seconds')) is not int or metric['freshness_sla_seconds'] <= 0:
            errors.append(prefix + 'positive freshness SLA required')
        policies = metric.get('source_policies', {})
        for policy in ('inclusion', 'deletion', 'time_attribution', 'nulls'):
            if not confirmed(policies.get(policy)): errors.append(prefix + 'source policy missing: ' + policy)
        mappings = metric.get('source_mapping', {})
        dependencies = metric.get('dependencies', [])
        if not dependencies: errors.append(prefix + 'metric dependencies required')
        for table in dependencies:
            mapping = mappings.get(table, {})
            columns = catalog.get('tables', {}).get(table, {}).get('columns', {})
            if not columns: errors.append(prefix + 'dependency absent from catalog: ' + table)
            if not confirmed(mapping.get('source_object')) or not confirmed(mapping.get('primary_key')):
                errors.append(prefix + 'source object/key missing: ' + table)
            fields = mapping.get('fields', {})
            if not isinstance(fields, dict) or set(columns) - set(fields) or any(not confirmed(v) for v in fields.values()):
                errors.append(prefix + 'complete field lineage required: ' + table)
    return errors


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(65536), b''): h.update(block)
    return h.hexdigest()


def evidence_errors(bundle, root, *, tenant, release, model_revision, embedding_revision):
    """Only evidence files under root are accepted. No URL fetch or command execution."""
    errors = []
    expected = dict(tenant=tenant, release=release, model_revision=model_revision, embedding_revision=embedding_revision)
    if bundle.get('schema_version') != 1: errors.append('unsupported evidence schema')
    if bundle.get('environment') != 'production': errors.append('production acceptance record required')
    for key, value in expected.items():
        if not confirmed(value) or bundle.get(key) != value: errors.append('evidence binding mismatch: ' + key)
    records = bundle.get('packages', {})
    root = Path(root).resolve()
    for package in PACKAGES:
        record = records.get(package, {})
        if record.get('status') != 'passed' or record.get('provenance') != 'real_environment':
            errors.append(package + ': real environment acceptance not passed')
        for field in ('reviewer', 'approval_reference'):
            if not confirmed(record.get(field)): errors.append(package + ': missing ' + field)
        try:
            when = datetime.fromisoformat(record.get('reviewed_at', '').replace('Z', '+00:00'))
            if not when.tzinfo or when > datetime.now(timezone.utc): raise ValueError()
        except (TypeError, ValueError): errors.append(package + ': invalid review timestamp')
        files = record.get('artifacts')
        if not isinstance(files, list) or not files:
            errors.append(package + ': evidence artifacts required'); continue
        for artifact in files:
            try:
                relative = Path(artifact['path'])
                target = (root / relative).resolve()
                if relative.is_absolute() or not target.is_relative_to(root): raise ValueError()
                if not re.fullmatch('[0-9a-f]{64}', artifact['sha256']): raise ValueError()
                if not target.is_file() or target.stat().st_size > 50_000_000 or sha256(target) != artifact['sha256']: raise ValueError()
            except (KeyError, TypeError, ValueError, OSError): errors.append(package + ': missing/changed/outside evidence artifact')
    return errors


def template():
    return dict(schema_version=1, environment='production', tenant='', release='', model_revision='', embedding_revision='',
        packages={p: dict(status='pending', provenance='not_connected', reviewer='', approval_reference='', reviewed_at='', artifacts=[]) for p in PACKAGES},
        note='Checks bind reviewed files and declared provenance; they do not authenticate reviewers or prove real infrastructure. Enterprise approval workflow remains authoritative.')


def load_json(path):
    path = Path(path)
    if path.stat().st_size > 5_000_000: raise ValueError('JSON exceeds 5MB')
    return json.loads(path.read_text(encoding='utf-8'))
