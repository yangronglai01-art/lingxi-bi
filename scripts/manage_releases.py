"""Local trusted-operator data publication workflow."""
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from src.petkit import releases
from src.petkit.settings import DATA

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['stage','stage-catalog','validate','activate','rollback','status','reconcile'])
    parser.add_argument('--generation'); parser.add_argument('--orders',type=int,default=3200)
    parser.add_argument('--expected-current'); args=parser.parse_args()
    if args.action=='stage':
        from src.petkit.builder import build
        result=build(args.orders,publish=False); result={k:v for k,v in result.items() if k!='counts'}
    elif args.action=='stage-catalog':
        from src.petkit.catalog_release import stage
        result=stage()
    elif args.action=='status':
        result={'current':json.loads((DATA/'current.json').read_text(encoding='utf-8')),
            'validated':[p.stem for p in (DATA/'releases').glob('*.json') if json.loads(p.read_text(encoding='utf-8'))['status']=='validated']}
    elif args.action=='reconcile':
        with releases.guard(str(DATA)): result=releases.reconcile_locked(DATA)
    else:
        if not args.generation: parser.error('本操作需要--generation')
        result=(releases.validate(args.generation) if args.action=='validate' else
            releases.activate(args.generation,actor='local-operator/'+args.action,expected_current=args.expected_current,allow_historical=args.action=='rollback'))
    print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'): sys.stdout.reconfigure(encoding='utf-8')
    main()
