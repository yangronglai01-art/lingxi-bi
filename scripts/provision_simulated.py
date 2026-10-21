"""Create a persistent local simulated organization; never print raw credentials."""
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from src.petkit import control
from src.petkit.settings import DATA,artifacts

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--rotate',action='store_true')
    parser.add_argument('--disable'); parser.add_argument('--enable')
    parser.add_argument('--subject'); parser.add_argument('--department'); parser.add_argument('--role')
    parser.add_argument('--regions',default='')
    args=parser.parse_args()
    control.initialize()
    if args.disable or args.enable:
        subject=args.disable or args.enable
        with control.db() as c: row=c.execute('SELECT * FROM users WHERE subject=?',(subject,)).fetchone()
        if not row: raise ValueError('账号不存在')
        control.upsert_user(subject,row['department'],row['role'],json.loads(row['regions']),enabled=bool(args.enable))
        print('账号状态已更新：'+subject); return
    if args.subject:
        if not args.department or not args.role: parser.error('--subject同时需要--department和--role')
        regions=[r.strip() for r in args.regions.split(',') if r.strip()]
        control.upsert_user(args.subject,args.department,args.role,regions)
        target=DATA/'simulated_credentials.json'
        credentials=json.loads(target.read_text(encoding='utf-8')) if target.exists() else {}
        if args.subject not in credentials or args.rotate: credentials[args.subject]=control.issue_token(args.subject)
        temp=target.with_suffix('.tmp'); temp.write_text(json.dumps(credentials,indent=2),encoding='utf-8'); temp.replace(target)
        print('账号授权已更新：'+args.subject+'；凭据位于：'+str(target)); return
    folder,_=artifacts()
    import duckdb
    with duckdb.connect(str(folder/'warehouse.duckdb'),read_only=True) as c:
        regions=[r[0] for r in c.execute('SELECT region_id FROM dim_region ORDER BY region_id').fetchall()]
    users=[('global_analyst','经营分析','analyst',()),('regional_sales','区域销售','sales',(regions[0],)),
      ('regional_service','区域售后','service',(regions[1],)),('restricted_viewer','基础查看','viewer',()),('regional_supply','区域仓储','supply',(regions[0],))]
    target=DATA/'simulated_credentials.json'
    credentials=json.loads(target.read_text(encoding='utf-8')) if target.exists() else {}
    for subject,department,role,scope in users:
        with control.db() as c: exists=c.execute('SELECT enabled FROM users WHERE subject=?',(subject,)).fetchone()
        if exists and not exists['enabled']: continue
        if not exists: control.upsert_user(subject,department,role,scope)
        if subject not in credentials or args.rotate:
            credentials[subject]=control.issue_token(subject)
    temporary=target.with_suffix('.tmp'); temporary.write_text(json.dumps(credentials,indent=2),encoding='utf-8'); temporary.replace(target)
    print('持久模拟组织已就绪。账号：'+', '.join(credentials))
    print('本机凭据文件（请勿提交或外传）：'+str(target))

if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'): sys.stdout.reconfigure(encoding='utf-8')
    main()
