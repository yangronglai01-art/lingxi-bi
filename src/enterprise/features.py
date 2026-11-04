"""Governed feedback, stage observations and release-bound follow-ups (PG only)."""
import math
import uuid
from psycopg2.extras import Json
from .identity import Denied

DDL = '''
CREATE TABLE IF NOT EXISTS analytics_feedback(
 id TEXT PRIMARY KEY,tenant TEXT NOT NULL,subject TEXT NOT NULL,job TEXT NOT NULL,
 policy_version INTEGER NOT NULL,scope TEXT NOT NULL,release TEXT NOT NULL,
 vote TEXT NOT NULL CHECK(vote IN ('positive','negative')),category TEXT NOT NULL,comment TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'pending',reviewer TEXT,approval TEXT,decision TEXT,
 created TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),reviewed TIMESTAMPTZ,
 UNIQUE(tenant,subject,job));
CREATE TABLE IF NOT EXISTS analytics_stage_observations(
 tenant TEXT NOT NULL,stage TEXT NOT NULL,outcome TEXT NOT NULL,bucket TEXT NOT NULL,
 observations BIGINT NOT NULL,total DOUBLE PRECISION NOT NULL,
 PRIMARY KEY(tenant,stage,outcome,bucket));
INSERT INTO analytics_schema VALUES(3) ON CONFLICT DO NOTHING;
'''
CATEGORIES = {'correct','metric','retrieval','generation','data','permission','performance','other'}
STAGES = {'intent','retrieval','model','semantic','review','sql','total'}


def observe(store,tenant,stage,outcome,seconds):
    return observe_batch(store,tenant,[(stage,outcome,seconds)])


def observe_batch(store,tenant,observations):
    from psycopg2.extras import execute_values
    if not 1<=len(observations)<=16:raise ValueError('Observation batch exceeds budget')
    cells={}
    for stage,outcome,seconds in observations:
        if stage not in STAGES or outcome not in {'ok','error','clarification'} or not math.isfinite(seconds) or seconds<0:
            raise ValueError('Invalid observation')
        for bound in (.1,.5,1,5,15,30,60,float('inf')):
            bucket='+Inf' if math.isinf(bound) else str(bound)
            key=(tenant,stage,outcome,bucket)
            cell=cells.setdefault(key,[0,0.0]);cell[0]+=int(seconds<=bound);cell[1]+=seconds if math.isinf(bound) else 0
    with store.db() as c:
        execute_values(c,'''INSERT INTO analytics_stage_observations VALUES %s
             ON CONFLICT(tenant,stage,outcome,bucket) DO UPDATE SET
             observations=analytics_stage_observations.observations+excluded.observations,total=analytics_stage_observations.total+excluded.total''',
             [(*key,*value) for key,value in cells.items()],page_size=256)


def feedback(store,p,job,vote,category,comment):
    if vote not in {'positive','negative'} or category not in CATEGORIES or not isinstance(comment,str) or len(comment)>2000:
        raise ValueError('Invalid feedback')
    with store.db() as c:
        store.verify(p,cursor=c)
        c.execute('SELECT state,scope,release,result FROM analytics_jobs WHERE id=%s AND tenant=%s AND subject=%s AND policy_version=%s',
                  (job,p.tenant,p.subject,p.policy_version));row=c.fetchone()
        if not row or row['state']!='completed' or row['scope']!=p.scope:raise Denied('Completed owned query required')
        c.execute('''INSERT INTO analytics_feedback(id,tenant,subject,job,policy_version,scope,release,vote,category,comment)
             VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(tenant,subject,job) DO NOTHING''',
             (uuid.uuid4().hex,p.tenant,p.subject,job,p.policy_version,p.scope,row['release'],vote,category,comment))
        c.execute('SELECT id,state,vote,category,comment FROM analytics_feedback WHERE tenant=%s AND subject=%s AND job=%s',(p.tenant,p.subject,job));existing=c.fetchone()
        if (existing['vote'],existing['category'],existing['comment'])!=(vote,category,comment):
            from .store import Conflict
            raise Conflict('Feedback already submitted with different content')
        store.event(c,p.tenant,p.subject,job,'feedback_submitted',{'feedback_id':existing['id'],'category':category})
        return dict(id=existing['id'],state=existing['state'])


def adjudicate(store,tenant,feedback_id,reviewer,approval,decision):
    from .acceptance import confirmed
    if not confirmed(reviewer) or not confirmed(approval) or decision not in {'confirmed_issue','not_issue','needs_more_evidence'}:
        raise ValueError('Reviewed decision and approval required')
    with store.db() as c:
        c.execute("UPDATE analytics_feedback SET state='reviewed',reviewer=%s,approval=%s,decision=%s,reviewed=clock_timestamp() WHERE tenant=%s AND id=%s AND state='pending'",(reviewer,approval,decision,tenant,feedback_id))
        if c.rowcount!=1:
            from .store import Conflict
            raise Conflict('Feedback is no longer pending')
        store.event(c,tenant,reviewer,feedback_id,'feedback_reviewed',{'decision':decision,'approval':approval})


def followup_question(store,c,p,parent,question,release):
    from .intent import followup,Clarify
    c.execute('''SELECT result,release,state,updated,clock_timestamp()-updated<interval '1 hour' AS fresh
      FROM analytics_jobs WHERE id=%s AND tenant=%s AND subject=%s AND policy_version=%s AND scope=%s''',
      (parent,p.tenant,p.subject,p.policy_version,p.scope));previous=c.fetchone()
    if not previous:raise Denied('Prior query unavailable')
    if previous['state']!='completed' or not previous['fresh'] or previous['release']!=release:
        raise Clarify('上次任务已过期或发行发生变化，请完整提问。')
    intent=(previous['result'] or {}).get('intent')
    if not intent:raise Clarify('上次任务没有可复用的结构化意图，请完整提问。')
    c.execute('SELECT catalog FROM analytics_releases WHERE tenant=%s AND id=%s',(p.tenant,release))
    return followup(question,intent,c.fetchone()['catalog'])
