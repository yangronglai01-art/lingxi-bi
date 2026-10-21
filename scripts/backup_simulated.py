"""Consistent local control backup plus immutable warehouse; fresh-folder restore drill."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import time
import uuid
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from src.petkit import control
from src.petkit.settings import DATA,artifacts

def digest(path):
    result=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''): result.update(block)
    return result.hexdigest()

def _backup_locked():
    folder,manifest=artifacts(); name=time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]
    destination=DATA/'backups'/name
    temporary=destination.with_name('.building-'+name)
    temporary.mkdir(parents=True,exist_ok=False)
    with control.db() as source:
        target=sqlite3.connect(temporary/'control.sqlite3')
        try: source.backup(target)
        finally: target.close()
    shutil.copytree(folder,temporary/manifest['generation'])
    ingestion=DATA/'ingestion'
    if ingestion.exists(): shutil.copytree(ingestion,temporary/'ingestion',ignore=shutil.ignore_patterns('*.tmp'))
    cache=DATA/'vector_cache'
    if cache.exists(): shutil.copytree(cache,temporary/'vector_cache',ignore=shutil.ignore_patterns('*.lock','*.tmp'))
    (temporary/'current.json').write_text(json.dumps(manifest,ensure_ascii=False),encoding='utf-8')
    if (DATA/'embedding_runtime.json').exists(): shutil.copy2(DATA/'embedding_runtime.json',temporary/'embedding_runtime.json')
    for name in ['release_journal.json']:
        if (DATA/name).exists(): shutil.copy2(DATA/name,temporary/name)
    receipt=DATA/'releases'/(manifest['generation']+'.json')
    if receipt.exists():
        (temporary/'releases').mkdir(exist_ok=True); shutil.copy2(receipt,temporary/'releases'/receipt.name)
    hashes={str(p.relative_to(temporary)):digest(p) for p in temporary.rglob('*') if p.is_file()}
    (temporary/'backup_manifest.json').write_text(json.dumps(dict(generation=manifest['generation'],sha256=hashes,plaintext_credentials_included=False,model_weights_included=False),ensure_ascii=False,indent=2),encoding='utf-8')
    temporary.rename(destination)
    control.audit(dict(subject='system',backup_id=name,generation=manifest['generation']),'backup_completed')
    return destination

def restore_drill(source):
    manifest=json.loads((source/'backup_manifest.json').read_text(encoding='utf-8'))
    for name,expected in manifest['sha256'].items():
        candidate=(source/name).resolve()
        if not candidate.is_relative_to(source.resolve()) or digest(candidate)!=expected: raise ValueError('备份文件校验失败')
    restored=DATA/'restore_drills'/uuid.uuid4().hex[:12]
    restored.parent.mkdir(parents=True,exist_ok=True)
    shutil.copytree(source,restored)
    with sqlite3.connect(restored/'control.sqlite3') as c:
        assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert not c.execute('PRAGMA foreign_key_check').fetchall()
        users=c.execute('SELECT count(*) FROM users').fetchone()[0]
        jobs=c.execute('SELECT count(*) FROM jobs').fetchone()[0]
    import duckdb
    with duckdb.connect(str(restored/manifest['generation']/'warehouse.duckdb'),read_only=True) as c:
        tables=c.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='main'").fetchone()[0]
        assert tables==500
        orders=c.execute('SELECT count(*) FROM fact_order').fetchone()[0]
    for name,expected in manifest['sha256'].items():
        assert digest(restored/name)==expected
    return dict(restored_directory=str(restored),users=users,jobs=jobs,physical_tables=tables,orders=orders,sqlite_integrity=True,file_hashes_verified=True,existing_instance_overwritten=False,scope='offline fresh-directory restore; no service cutover')

def backup():
    from src.petkit.releases import guard,reconcile_locked
    from filelock import FileLock
    with FileLock(str(DATA/'ingestion.lock'),timeout=30),guard(str(DATA)):
        reconcile_locked(DATA)
        return _backup_locked()

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--drill',action='store_true'); args=parser.parse_args()
    source=backup(); report=dict(backup_directory=str(source))
    if args.drill: report.update(restore_drill(source))
    (ROOT/'reports/petkit500_backup_drill.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False),flush=True)

if __name__=='__main__': main()
