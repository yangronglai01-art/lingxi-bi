"""Catalog-only candidates: immutable business bytes and durable staging evidence."""
import hashlib,json,shutil,time,uuid
from . import releases,control
from .settings import DATA,VERSION
from .catalog import METRICS

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()

def structure(catalog):return {k:v for k,v in catalog.items() if k not in {'generation','metrics'}}
def summary_content(summary):return {k:v for k,v in summary.items() if k not in {'generation','verification'}}

def stage():
    from filelock import FileLock
    control.initialize()
    with FileLock(str(DATA/'ingestion.lock'),timeout=30),releases.guard(str(DATA)):
        releases.reconcile_locked(DATA)
        parent=json.loads((DATA/'current.json').read_text(encoding='utf-8'))['generation']
        folder=releases.folder_for(parent);before=releases.file_seal(folder)
        catalog=json.loads((folder/'catalog.json').read_text(encoding='utf-8'))
        summary=json.loads((folder/'summary.json').read_text(encoding='utf-8'))
        if catalog['metrics']==METRICS: raise ValueError('当前版本指标目录已是最新，无需生成候选')
        history=json.loads((folder/'lineage.json').read_text(encoding='utf-8')) if (folder/'lineage.json').exists() else None
        candidate=VERSION+'-'+uuid.uuid4().hex[:10];target=DATA/candidate
        shutil.copytree(folder,target)
        if before!=releases.file_seal(folder):raise ValueError('复制期间父快照发生变化')
        # Inherited catalog-only staging evidence belongs to its original candidate.
        (target/'metadata.json').unlink(missing_ok=True)
        metadata=dict(version=1,kind='catalog-only',generation=candidate,parent=parent,parent_files=before,
            parent_history=history,structure=digest(structure(catalog)),summary=digest(summary_content(summary)))
        catalog.update(generation=candidate,metrics=METRICS)
        summary.update(generation=candidate,verification='仅指标目录升级候选，尚未发布；业务数据未改变')
        releases.write(target/'catalog.json',catalog);releases.write(target/'summary.json',summary)
        if history is not None:
            releases.write(target/'lineage.json',dict(history,generation=candidate,parent_generation=parent,parent_files=before))
        releases.write(target/'metadata.json',metadata)
        with control.db() as c:
            c.execute('INSERT INTO audit(created,subject,event,payload) VALUES(?,?,?,?)',
                (time.time(),'local-operator','catalog_staged',json.dumps(dict(candidate=candidate,parent=parent,metadata_sha=releases.sha(target/'metadata.json')))))
        return dict(parent=parent,candidate=candidate,state='staged',business_bytes_unchanged=True,metric_count=len(METRICS))

def validate(folder):
    metadata=json.loads((folder/'metadata.json').read_text(encoding='utf-8'))
    if metadata.get('version')!=1 or metadata.get('kind')!='catalog-only' or metadata.get('generation')!=folder.name:raise ValueError('目录升级标记无效')
    seal=releases.sha(folder/'metadata.json');proven=False
    with control.db() as c:
        for row in c.execute("SELECT payload FROM audit WHERE event='catalog_staged'"):
            value=json.loads(row['payload'])
            if value.get('candidate')==folder.name and value.get('parent')==metadata['parent'] and value.get('metadata_sha')==seal:proven=True;break
    if not proven:raise ValueError('目录升级缺少可信候选记录')
    if releases.sha(folder/'warehouse.duckdb')!=metadata['parent_files']['warehouse.duckdb']:raise ValueError('目录升级不得改变业务数据库')
    catalog=json.loads((folder/'catalog.json').read_text(encoding='utf-8'));summary=json.loads((folder/'summary.json').read_text(encoding='utf-8'))
    if digest(structure(catalog))!=metadata['structure'] or digest(summary_content(summary))!=metadata['summary']:raise ValueError('目录升级改变了表结构或业务摘要')
    if catalog['metrics']!=METRICS:raise ValueError('候选指标目录与当前代码不一致')
    prior=metadata['parent_history'];path=folder/'lineage.json'
    if prior is not None:
        expected=dict(prior,generation=folder.name,parent_generation=metadata['parent'],parent_files=metadata['parent_files'])
        if not path.exists() or json.loads(path.read_text(encoding='utf-8'))!=expected:raise ValueError('目录升级改变了来源血缘')
    elif path.exists():raise ValueError('目录升级不得伪造来源血缘')
    return dict(parent=metadata['parent'],business_bytes_unchanged=True,source_events_preserved=len(prior['events']) if prior else 0,metric_count=len(METRICS))
