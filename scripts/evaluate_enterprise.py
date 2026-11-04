"""Frozen-set evaluator for the canonical enterprise API; never calls model providers directly."""
import argparse,json,os,time,uuid,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.enterprise.acceptance import sha256,load_json
from src.enterprise.quality import report
from decimal import Decimal,InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit
import httpx


def scalar(value):
    if value is None or isinstance(value,bool):return value
    try:return Decimal(str(value))
    except InvalidOperation:return str(value)


def judge(case,answer):
    if answer.get('status')!=case['expected_status']:return 'failed_status'
    if case.get('requires_model') and answer.get('generation_mode')!='local-chat':return 'failed_generation_path'
    if case.get('requires_model') is False and case['expected_status']=='ok' and answer.get('generation_mode')!='approved_template':return 'failed_generation_path'
    if case['expected_status']!='ok':return 'passed_behavior'
    if 'expected_rows' not in case:return 'unscored_missing_reference'
    result=answer.get('result') or {}
    if result.get('truncated'):return 'failed_truncated'
    if result.get('columns')!=case['expected_columns']:return 'failed_columns'
    expected=case['expected_rows'];actual=result.get('rows',[])
    if len(actual)!=len(expected):return 'failed_row_count'
    if not case.get('ordered',False):
        def row_key(row):
            normalized=[]
            for value in row:
                value=scalar(value)
                if isinstance(value,Decimal):normalized.append(('number',str(value.normalize())))
                else:normalized.append((type(value).__name__,str(value)))
            return tuple(normalized)
        expected=sorted(expected,key=row_key)
        actual=sorted(actual,key=row_key)
    tolerance=Decimal(case.get('decimal_tolerance','0'))
    if not tolerance.is_finite() or tolerance<0:raise ValueError('Invalid reference tolerance')
    for row,wanted in zip(actual,expected):
        if len(row)!=len(wanted):return 'failed_width'
        for a,b in zip(row,wanted):
            a,b=scalar(a),scalar(b)
            if isinstance(a,Decimal) and isinstance(b,Decimal):
                if not a.is_finite() or not b.is_finite() or abs(a-b)>tolerance:return 'failed_value'
            elif type(a)!=type(b) or a!=b:return 'failed_value'
    return 'passed_value'



def validate_evaluation_profile(cases,manifest,criteria,profile):
    """Never mix synthetic targets with enterprise release evidence."""
    if profile not in {'enterprise','synthetic'}:raise ValueError('Unknown evaluation profile')
    expected='enterprise' if profile=='enterprise' else 'synthetic-benchmark'
    if any(c.get('source_kind')!=expected for c in cases):raise ValueError('Mixed or mismatched evaluation provenance')
    if profile=='synthetic':
        if not manifest or manifest.get('evaluation_profile')!='synthetic' or manifest.get('production_eligible') is not False:
            raise ValueError('Frozen explicitly synthetic manifest required')
        if not criteria or criteria.get('purpose')!='synthetic_engineering_only':raise ValueError('Synthetic criteria required')
    elif (manifest and manifest.get('evaluation_profile','enterprise')!='enterprise') or (criteria and criteria.get('purpose')=='synthetic_engineering_only'):
        raise ValueError('Synthetic inputs cannot approve enterprise evaluation')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--profile',choices=('enterprise','synthetic'),default='enterprise')
    p.add_argument('--cases',required=True);p.add_argument('--out',required=True)
    p.add_argument('--api',required=True,help='Internal application base URL; not model gateway')
    p.add_argument('--model-revision',required=True,help='Operator-confirmed deployed snapshot, never inferred from alias')
    p.add_argument('--timeout',type=int,default=180)
    p.add_argument('--frozen-manifest');p.add_argument('--criteria')
    args=p.parse_args();url=urlsplit(args.api)
    if url.username or url.password or (url.scheme!='https' and not (url.scheme=='http' and url.hostname in {'127.0.0.1','localhost'})):
        p.error('HTTPS or explicit localhost integration URL required; credentials cannot be in URL')
    token=os.environ.get('LINGXI_EVAL_TOKEN')
    if not token:p.error('Set LINGXI_EVAL_TOKEN securely in the local environment')
    path=Path(args.cases)
    if path.stat().st_size>5000000:p.error('Frozen case file exceeds 5MB')
    cases=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
    if not 1<=len(cases)<=500 or len({c['id'] for c in cases})!=len(cases):p.error('Need 1..500 unique frozen cases')
    for c in cases:
        if c['expected_status']=='ok' and ('expected_rows' not in c or 'expected_columns' not in c):p.error('Numeric cases need independent columns and rows before inference')
        if c['difficulty'] not in {'simple','join','complex','behavior'} or 'expected_status' not in c or not c.get('source_kind'):p.error('Case metadata/reference status missing')
    if not 1<=args.timeout<=600:p.error('Timeout must be 1..600 seconds')
    criteria=None;manifest=None
    if bool(args.frozen_manifest)!=bool(args.criteria):p.error('Frozen manifest and criteria must be supplied together')
    if any(c.get('source_kind')=='enterprise' for c in cases) and not args.frozen_manifest:p.error('Enterprise evaluation requires frozen manifest and preapproved criteria')
    if args.frozen_manifest:
        manifest=load_json(args.frozen_manifest);criteria=load_json(args.criteria)
        if manifest.get('cases_sha256')!=sha256(path) or manifest.get('criteria_sha256')!=sha256(args.criteria):p.error('Frozen evaluation inputs changed')
    try:validate_evaluation_profile(cases,manifest,criteria,args.profile)
    except ValueError as exc:p.error(str(exc))
    out=Path(args.out);out.mkdir(parents=True,exist_ok=False)
    prefix=args.api.rstrip('/')+'/api/v1/analytics';records=[]
    with httpx.Client(headers={'Authorization':'Bearer '+token},timeout=10,follow_redirects=False) as client:
        actor=client.get(prefix+'/me');actor.raise_for_status()
        for case in cases:
            started=time.monotonic();job=None;answer={'status':'system_error'}
            try:
                response=client.post(prefix+'/queries',headers={'Idempotency-Key':'eval-'+uuid.uuid4().hex},json={'question':case['question']})
                response.raise_for_status();job=response.json()['id']
                while time.monotonic()-started<args.timeout:
                    response=client.get(prefix+'/queries/'+job);response.raise_for_status();state=response.json()
                    if state['state']=='completed':answer=state['result'];break
                    if state['state'] in {'failed','cancelled','interrupted'}:
                        answer={'status':'denied' if state.get('error_code')=='AUTHORIZATION_CHANGED' else 'system_error','error_code':state.get('error_code')};break
                    time.sleep(.5)
                else:
                    client.post(prefix+'/queries/'+job+'/cancel');answer={'status':'timeout'}
            except httpx.HTTPStatusError as exc:
                answer={'status':'denied' if exc.response.status_code in {401,403} else 'request_error','http_status':exc.response.status_code}
            except httpx.HTTPError:
                if job:
                    try:client.post(prefix+'/queries/'+job+'/cancel')
                    except httpx.HTTPError:pass
                answer={'status':'transport_error'}
            record=dict(id=case['id'],difficulty=case['difficulty'],source_kind=case['source_kind'],job=job,
                model_revision=args.model_revision,model_revision_source='operator supplied; verify against deployment',
                attempt=1,first_result=answer,judgment=judge(case,answer),elapsed_seconds=round(time.monotonic()-started,3))
            records.append(record)
            with (out/'per-case-results.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(record,ensure_ascii=False)+'\n')
    summary={}
    for difficulty in ('simple','join','complex','behavior'):
        group=[r for r in records if r['difficulty']==difficulty];scored=[r for r in group if not r['judgment'].startswith('unscored')]
        passed=sum(r['judgment'].startswith('passed') for r in scored)
        summary[difficulty]=dict(total=len(group),scored=len(scored),passed=passed,unscored=len(group)-len(scored),rate=passed/len(scored) if scored else None)
    quality=report(records,cases,criteria)
    (out/'summary.json').write_text(json.dumps(dict(evaluation_profile=args.profile,production_eligible=(args.profile=='enterprise'),by_difficulty=summary,quality=quality,model_revision=args.model_revision,
        frozen_manifest_sha256=sha256(args.frozen_manifest) if manifest else None,cases_sha256=sha256(path),
        note='First attempts only. Model revision supplied by operator; verify real gateway deployment. No authenticated actor claims exported.'),ensure_ascii=False,indent=2),encoding='utf-8')
    if quality['gate_passed'] is False:raise SystemExit(2)
    print('Evaluation completed; local evidence written. Token omitted.')


if __name__=='__main__':main()
