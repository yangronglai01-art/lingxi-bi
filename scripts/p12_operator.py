"""Operator-only feedback adjudication, snapshot recovery and tuning decision reports."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from src.enterprise.settings import Settings
from src.enterprise.store import Store
from src.enterprise.acceptance import load_json


def tuning_assessment(records):
    """Aggregate only reviewed issues; never exports user comments, SQL or answers."""
    counts={}
    for record in records:
        if record.get('state')=='reviewed' and record.get('decision')=='confirmed_issue':
            category=record['category'];counts[category]=counts.get(category,0)+int(record.get('n',1))
    total=sum(counts.values())
    return dict(confirmed_issues=total,by_category=counts,training_authorized=False,
        recommendation='先修复数据/口径/权限/检索问题；对剩余生成错误做基线与治理/提示/检索消融，再评估微调。' if total else '尚无经审核的真实失败样本，不应启动微调。',
        note='Counts are prioritization evidence, not proof of fine-tuning benefit. No dataset export, training or cloud upload is performed.')



def snapshot_operator_connection(settings):
    import os
    import pymysql
    from src.enterprise.settings import name
    config=settings.scopes['global']
    user=os.environ.get('LINGXI_SNAPSHOT_OPERATOR_USER','')
    password=os.environ.get('LINGXI_SNAPSHOT_OPERATOR_PASSWORD','')
    name(user)
    if not password or user==config['user'] or (settings.environment in {'staging','production'} and user.lower() in {'root','admin'}):
        raise ValueError('Separate least-privilege snapshot operator credentials required')
    tls={'ca':config['ssl_ca'],'check_hostname':True} if config.get('ssl_ca') else None
    return pymysql.connect(host=config['host'],port=int(config.get('port',9030)),database=config['database'],
        user=user,password=password,autocommit=True,charset='utf8mb4',connect_timeout=5,read_timeout=60,write_timeout=5,ssl=tls)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['review-feedback','tuning-assessment','inspect-load','quarantine-load','reconcile-load'])
    p.add_argument('--id');p.add_argument('--reviewer');p.add_argument('--approval');p.add_argument('--decision')
    p.add_argument('--manifest');p.add_argument('--file');p.add_argument('--out',required=True)
    a=p.parse_args();s=Settings.from_env();store=Store(s.dsn);result={}
    if a.action=='review-feedback':
        from src.enterprise.features import adjudicate
        adjudicate(store,s.tenant,a.id,a.reviewer,a.approval,a.decision);result={'reviewed':True}
    elif a.action=='tuning-assessment':
        with store.db() as c:
            c.execute('SELECT f.state,f.decision,f.category,count(*) AS n FROM analytics_feedback f JOIN analytics_releases r ON r.id=f.release AND r.tenant=f.tenant WHERE f.tenant=%s AND r.watermark->>%s=%s AND r.catalog->>%s=%s GROUP BY f.state,f.decision,f.category',(s.tenant,'source_kind','enterprise','synthetic','false'))
            result=tuning_assessment(c.fetchall())
    elif a.action=='inspect-load':
        with store.db() as c:
            c.execute('SELECT release,manifest_hash,state,error_code,created FROM analytics_loads WHERE tenant=%s AND release=%s',(s.tenant,a.id))
            result=dict(c.fetchone() or {})
    elif a.action=='quarantine-load':
        from src.enterprise.recovery import quarantine
        quarantine(store,s.tenant,a.id,a.manifest,a.approval);result={'quarantined':True,'tables_deleted':False}
    else:
        from src.enterprise.recovery import reconcile
        payload=load_json(a.file)
        # Keep settings.scopes bound to runtime readers. Operator credentials
        # are separate so recovered GRANTs never target the operator itself.
        with snapshot_operator_connection(s) as admin:
            result=reconcile(s,store,admin,a.id,payload['catalog'],payload['rows'],payload['keys'],payload['watermark'],a.approval)
    with Path(a.out).open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2,default=str)
    print('Operator action recorded; credentials and raw feedback omitted.')


if __name__=='__main__':main()
