"""Versioned BGE-M3 schema vectors through the existing private gateway alias."""
import json,math
import httpx
from psycopg2.extras import Json
from .store import digest,Conflict

DDL='''CREATE TABLE IF NOT EXISTS analytics_schema_vectors(
 tenant TEXT NOT NULL,release TEXT NOT NULL,table_name TEXT NOT NULL,revision TEXT NOT NULL,
 content_hash TEXT NOT NULL,vector JSONB NOT NULL,PRIMARY KEY(tenant,release,table_name));'''


def embedding(settings,texts,transport=None):
    with httpx.Client(timeout=30,transport=transport,follow_redirects=False) as client:
        response=client.post(settings.gateway_url.rstrip('/')+'/embeddings',headers={'Authorization':'Bearer '+settings.gateway_key},
            json={'model':'embedding-default','input':texts})
    if response.status_code!=200 or len(response.content)>10000000:raise RuntimeError('EMBEDDING_UNAVAILABLE')
    records=response.json()['data']
    if len(records)!=len(texts) or {r.get('index') for r in records}!=set(range(len(texts))):raise ValueError('Embedding response index mismatch')
    vectors=[]
    for record in sorted(records,key=lambda r:r['index']):
        vector=record['embedding']
        if len(vector)!=1024 or any(type(v) not in (float,int) or not math.isfinite(v) for v in vector):raise ValueError('BGE-M3 requires finite 1024D vectors')
        norm=math.sqrt(sum(v*v for v in vector))
        if norm<1e-8:raise ValueError('Zero embedding')
        vectors.append([v/norm for v in vector])
    return vectors


def build(settings,store,release,revision,transport=None):
    if not revision:raise ValueError('Immutable embedding revision required')
    with store.db() as c:
        c.execute(DDL)
        c.execute('SELECT catalog,state FROM analytics_releases WHERE tenant=%s AND id=%s',(settings.tenant,release));r=c.fetchone()
    if not r or r['state']!='staged':raise Conflict('Build vectors before publishing release')
    names=list(r['catalog']['tables']);documents=[json.dumps(r['catalog']['tables'][n],ensure_ascii=False,sort_keys=True) for n in names]
    for start in range(0,len(names),16):
        vectors=embedding(settings,documents[start:start+16],transport)
        with store.db() as c:
            for n,text,vector in zip(names[start:start+16],documents[start:start+16],vectors):
                c.execute('''INSERT INTO analytics_schema_vectors VALUES(%s,%s,%s,%s,%s,%s)
                  ON CONFLICT(tenant,release,table_name) DO NOTHING''',(settings.tenant,release,n,revision,digest(text),Json(vector)))
    with store.db() as c:
        c.execute('SELECT count(*) AS n FROM analytics_schema_vectors WHERE tenant=%s AND release=%s AND revision=%s',(settings.tenant,release,revision))
        if c.fetchone()['n']!=len(names):raise Conflict('Incomplete schema embedding version')


def search(settings,store,release,question,allowed,revision,transport=None,*,limit=8):
    if type(limit) is not int or not 1<=limit<=32:raise ValueError('Invalid embedding candidate limit')
    if not allowed:return []
    q=embedding(settings,[question],transport)[0]
    with store.db() as c:
        c.execute('SELECT table_name,content_hash,vector FROM analytics_schema_vectors WHERE tenant=%s AND release=%s AND revision=%s AND table_name=ANY(%s)',
            (settings.tenant,release['id'],revision,list(allowed)))
        records=c.fetchall()
    if len(records)!=len(allowed) or {r['table_name'] for r in records}!=set(allowed):raise Conflict('Published schema embedding index incomplete')
    scored=[]
    for r in records:
        text=json.dumps(release['catalog']['tables'][r['table_name']],ensure_ascii=False,sort_keys=True)
        if digest(text)!=r['content_hash']:raise Conflict('Schema embedding content mismatch')
        vector=r['vector']
        if not isinstance(vector,list) or len(vector)!=1024 or any(type(v) not in (int,float) or not math.isfinite(v) for v in vector):
            raise Conflict('Schema embedding vector invalid')
        norm=math.sqrt(sum(v*v for v in vector))
        if not math.isfinite(norm) or abs(norm-1)>1e-3:raise Conflict('Schema embedding normalization mismatch')
        scored.append((sum(a*b for a,b in zip(q,r['vector'])),r['table_name']))
    return [name for _,name in sorted(scored,key=lambda row:(-row[0],row[1]))[:limit]]
