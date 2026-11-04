"""Finite live API smoke check, with two operator-provided identities. No raw output data."""
import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import httpx


def smoke(client, question, timeout=60):
    """Client must carry actor A's bearer token. Actor B is isolated per request."""
    checks = {}
    checks['anonymous_denied'] = client.get('/api/v1/analytics/me', headers={'Authorization': ''}).status_code == 401
    me = client.get('/api/v1/analytics/me'); me.raise_for_status()
    checks['authenticated'] = bool(me.json().get('managed'))
    ready = client.get('/health/ready'); checks['dependencies_ready'] = ready.status_code == 200
    response = client.post('/api/v1/analytics/queries', headers={'Idempotency-Key': 'smoke-' + uuid.uuid4().hex}, json={'question': question})
    response.raise_for_status(); job = response.json()['id']; path = '/api/v1/analytics/queries/' + job
    try:
        other = {'Authorization': 'Bearer ' + os.environ['LINGXI_SMOKE_OTHER_TOKEN']}
        other_me = client.get('/api/v1/analytics/me', headers=other); other_me.raise_for_status()
        different = other_me.json().get('subject') != me.json().get('subject')
        checks['other_identity_distinct'] = different
        checks['other_read_denied'] = different and client.get(path, headers=other).status_code == 403
        checks['other_cancel_denied'] = different and client.post(path+'/cancel', headers=other).status_code == 403
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            response = client.get(path); response.raise_for_status(); state = response.json()
            if state['state'] in {'completed', 'failed', 'cancelled', 'interrupted'}:
                result = state.get('result') or {}
                checks['query_completed'] = state['state'] == 'completed' and result.get('status') == 'ok'
                checks['real_source'] = result.get('source_kind') == 'enterprise'
                checks['no_mock'] = result.get('mock_used') is False
                break
            time.sleep(.5)
        else: checks['query_completed'] = False
    finally:
        # Always ask for cancellation, including timeout and failures after creation.
        client.post(path+'/cancel')
    return dict(passed=all(checks.values()), checks=checks,
        note='Live API checks only. Browser PKCE/logout, grant revocation and production approval require separate evidence.')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--api', required=True); p.add_argument('--question', required=True)
    p.add_argument('--out', required=True); p.add_argument('--timeout', type=int, default=60)
    args = p.parse_args(); url = urlsplit(args.api)
    if url.scheme != 'https' or url.username or url.password or url.query or url.fragment or url.path not in ('', '/'):
        p.error('Provide an HTTPS origin without credentials, path, query or fragment')
    if not 1 <= args.timeout <= 300: p.error('Timeout must be 1..300 seconds')
    if not all(os.environ.get(k) for k in ('LINGXI_SMOKE_TOKEN', 'LINGXI_SMOKE_OTHER_TOKEN')):
        p.error('Inject both smoke tokens via secure environment')
    try:
        with httpx.Client(base_url=args.api, headers={'Authorization': 'Bearer '+os.environ['LINGXI_SMOKE_TOKEN']},
                          timeout=10, follow_redirects=False) as client:
            result = smoke(client, args.question, args.timeout)
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        result = dict(passed=False, error='Live dependency or contract failure; credentials and response data omitted')
    with Path(args.out).open('x', encoding='utf-8') as f: json.dump(result, f, ensure_ascii=False, indent=2)
    print('Live smoke: '+('passed' if result['passed'] else 'failed'))
    if not result['passed']: raise SystemExit(2)


if __name__ == '__main__': main()
