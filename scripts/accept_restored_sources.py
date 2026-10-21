"""Validate source lineage inside the independent restored instance."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from src.petkit.settings import DATA

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--data-dir'); args=parser.parse_args()
    backup=json.loads((ROOT/'reports/petkit500_backup_drill.json').read_text(encoding='utf-8'))
    restored=Path(args.data_dir or backup['restored_directory']).resolve()
    if restored==DATA.resolve(): raise ValueError('恢复验收必须使用独立副本目录')
    generation=json.loads((restored/'current.json').read_text(encoding='utf-8'))['generation']
    env=os.environ.copy(); env['PETKIT_DATA_DIR']=str(restored)
    result=subprocess.run([sys.executable,str(ROOT/'scripts/manage_releases.py'),'validate','--generation',generation],
        cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8',timeout=180)
    if result.returncode: raise RuntimeError('恢复副本来源校验失败：'+result.stderr[-1500:])
    receipt=json.loads(result.stdout)
    gate=receipt['gates']['source_reconciliation']
    assert gate['source_and_rows_verified'] and gate['events']>0
    assert all((restored/p).resolve().is_relative_to(restored/'ingestion/archive') for p in gate['source_files'])
    report=dict(generation=generation,restored_directory=str(restored),events_verified=gate['events'],
        source_batches=gate['source_batches'],archives_relocated_to_restored_instance=True,business_reference_passed=receipt['gates']['business_oracle']['passed'],real_llm_called=False)
    (ROOT/'reports/petkit500_restored_source_acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False),flush=True)

if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'): sys.stdout.reconfigure(encoding='utf-8')
    main()
