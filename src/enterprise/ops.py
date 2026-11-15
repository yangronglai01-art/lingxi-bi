"""Explicit migration/publication CLI; never invoked during API startup."""
import argparse,json
from pathlib import Path
from .settings import Settings
from .store import Store,digest
from .execution import Executor
from .ingestion import Batches


def verify_release(settings,store,release,counts):
    with store.db() as c:
        c.execute('SELECT * FROM analytics_releases WHERE tenant=%s AND id=%s',(settings.tenant,release));r=c.fetchone()
    if not r:raise ValueError('Unknown release')
    executor=Executor(settings)
    evidence={}
    for scope,mapping in r['views'].items():
        if scope not in settings.scopes:raise ValueError('Unconfigured release scope')
        with executor.connect(scope) as connection:
            with connection.cursor() as c:
                for logical,physical in mapping.items():
                    from .settings import name
                    c.execute('SELECT COUNT(*) FROM `'+name(physical)+'`');actual=c.fetchone()[0]
                    if counts[scope][logical]!=actual:raise ValueError('Source/table count reconciliation failed')
                    evidence[scope+'.'+logical]=actual
    return {'passed':True,'manifest_hash':digest(dict(catalog=r['catalog'],views=r['views'],watermark=r['watermark'],counts=evidence))}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['migrate','stage','index','publish','withdraw','grant','inspect-job'])
    parser.add_argument('--job')
    parser.add_argument('--replacement');parser.add_argument('--reason')
    parser.add_argument('--file');parser.add_argument('--release');parser.add_argument('--expected');parser.add_argument('--approval');parser.add_argument('--acceptance',help='Production reviewed evidence bundle JSON')
    args=parser.parse_args();s=Settings.from_env();store=Store(s.dsn)
    if args.action=='inspect-job':
        if not args.job:parser.error('inspect-job requires --job')
        job=store.inspect_job(s.tenant,args.job)
        scope=s.scopes.get(job['scope'])
        job['configured_endpoint']={key:scope[key] for key in ('host','port','database') if key in scope} if scope else None
        print(json.dumps(job,default=str,ensure_ascii=False))
        return
    if args.action=='migrate':store.migrate();Batches(store).migrate()
    elif args.action=='stage':
        payload=json.loads(Path(args.file).read_text(encoding='utf-8'))
        store.stage_release(s.tenant,payload['id'],payload['catalog'],payload['views'],payload['watermark'])
    elif args.action=='index':
        from .vectors import build
        build(s,store,args.release,s.embedding_revision)
    elif args.action=='publish':
        payload=json.loads(Path(args.file).read_text(encoding='utf-8'))
        proof=verify_release(s,store,args.release,payload['source_counts'])
        with store.db() as c:
            c.execute('SELECT catalog FROM analytics_releases WHERE tenant=%s AND id=%s',(s.tenant,args.release));catalog=c.fetchone()['catalog']
            if s.environment in {'staging','production'}:
                from .acceptance import governance_errors,evidence_errors,load_json
                if governance_errors(catalog,s.enabled_metrics):raise ValueError('Confirmed business metric contracts and field lineage required')
                if s.environment=='production':
                    if not args.acceptance:raise ValueError('Production reviewed acceptance bundle required')
                    bundle=load_json(args.acceptance)
                    if evidence_errors(bundle,Path(args.acceptance).parent,tenant=s.tenant,release=args.release,model_revision=s.model_revision,embedding_revision=s.embedding_revision):
                        raise ValueError('Production acceptance evidence incomplete, changed or mismatched')
            if s.embedding_revision:
                c.execute('SELECT count(*) AS n FROM analytics_schema_vectors WHERE tenant=%s AND release=%s AND revision=%s',(s.tenant,args.release,s.embedding_revision))
                if c.fetchone()['n']!=len(catalog['tables']):raise ValueError('Complete pinned embedding index required')
        if not payload.get('independent_totals_passed'):raise ValueError('Independent metric totals must be confirmed')
        store.publish(s.tenant,args.release,args.expected,args.approval+':'+proof['manifest_hash'])
    elif args.action=='withdraw':
        if not all((args.release,args.replacement,args.reason)):
            parser.error('withdraw requires --release, --replacement and --reason')
        store.withdraw_active_release(s.tenant,args.release,args.replacement,args.reason)
    else:
        p=json.loads(Path(args.file).read_text(encoding='utf-8'))
        if p['scope'] not in s.scopes or tuple(s.scopes[p['scope']].get('regions',[]))!=tuple(p['regions']):raise ValueError('Unconfigured scope')
        store.grant(s.tenant,p['subject'],p['role'],p['regions'],p['scope'],p['enabled'])
    print('Operation completed; credentials omitted')


if __name__=='__main__':main()
