"""Trusted operator entry for local mock upstream batches."""
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from src.petkit import ingestion,control

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('action',choices=['ingest','list'])
    parser.add_argument('--file'); args=parser.parse_args()
    if args.action=='ingest':
        if not args.file: parser.error('ingest需要--file')
        result=ingestion.ingest(args.file)
    else:
        ingestion.initialize()
        with control.db() as c:
            result=[dict(r) for r in c.execute('SELECT id,source,batch_id,parent,candidate,state,error,created FROM ingestion_attempts ORDER BY created DESC LIMIT 50').fetchall()]
    print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'): sys.stdout.reconfigure(encoding='utf-8')
    main()
