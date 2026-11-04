"""Build versioned synthetic data; no model calls or production publication."""
import argparse,copy,hashlib,json,sys
from collections import Counter
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.benchmarks.synthetic import make,invariants
from src.benchmarks.oracle import Oracle
from src.benchmarks.cases import build_cases
from src.enterprise.loader import source_quality
from scripts.freeze_enterprise_eval import freeze


def dump(path,value):
    with path.open('x',encoding='utf-8',newline='\n') as f:json.dump(value,f,ensure_ascii=False,indent=2)


def lines(path,records):
    with path.open('x',encoding='utf-8',newline='\n') as f:
        for r in records:f.write(json.dumps(r,ensure_ascii=False,separators=(',',':'))+'\n')


def negative_cases(data):
    """Mutations isolated from valid snapshots, each checked against real controls."""
    r=data['rows'];out=[]
    mutations=[
      ('duplicate_primary_key','dim_product','append',r['dim_product'][0],'structural'),
      ('orphan_foreign_key','fact_order_item','sku_id','DOES_NOT_EXIST','structural'),
      ('floating_point_money','fact_order_item','amount',1.23,'structural'),
      ('fractional_cent','fact_order_item','amount','1.001','structural'),
      ('decimal_overflow','fact_order_item','amount','1000000000000000000.00','structural'),
      ('invalid_date','fact_order','paid_date','2026-02-30','structural'),
      ('schema_drift','fact_order','unexpected_column',1,'structural'),
      ('null_primary_key','dim_product','product_id',None,'structural'),
      ('header_payment_mismatch','fact_order','paid_amount','0.01','business'),
      ('over_refund','fact_refund','amount','99999999.00','business'),
      ('stock_reconciliation','fact_inventory_day','quantity',99999999,'business'),
      ('over_receipt','fact_receipt','quantity',99999999,'business'),
      ('future_effective_record','fact_payment','paid_date','2027-01-01','business'),
    ]
    # Date column name is catalog-owned, not assumed.
    mutations=[m if m[0]!='future_effective_record' else (m[0],'fact_order','paid_date',m[3],m[4]) for m in mutations]
    for ident,table,field,value,control in mutations:
        changed=dict(r);changed[table]=copy.deepcopy(r[table])
        if field=='append':changed[table].append(copy.deepcopy(value))
        else:changed[table][0][field]=value
        try:
            if control=='structural':source_quality(data['catalog'],changed,data['keys']);rejected=False
            else:rejected=not invariants(dict(data,rows=changed))['passed']
        except (ValueError,ArithmeticError):rejected=True
        if not rejected:raise AssertionError('Negative fixture did not fail: '+ident)
        out.append(dict(id=ident,table=table,row_index=0,operation='append' if field=='append' else 'set',field=field,value=value,expected='rejected',control=control,verified=True))
    return out


def build(out,seed=20261023):
    out=Path(out);out.mkdir(parents=True,exist_ok=False);overview={}
    criteria=dict(purpose='synthetic_engineering_only',owner='project-synthetic-spec',approval_reference='synthetic-targets-only-no-enterprise-approval',
      by_difficulty={d:dict(min_correct_rate=.9,max_wrong_answer_rate=.05,min_cases=50,max_p95_seconds=60) for d in ('simple','join','complex','behavior')})
    dump(out/'synthetic_criteria.json',criteria)
    for profile,count,offset in [('development',1200,0),('holdout',1800,10007),('shift',2400,20011)]:
        data=make(seed+offset,count,profile);check=invariants(data)
        if not check['passed']:raise AssertionError(check)
        source_quality(data['catalog'],data['rows'],data['keys'])
        folder=out/profile;folder.mkdir();(folder/'tables').mkdir()
        for n,rows in data['rows'].items():lines(folder/'tables'/(n+'.jsonl'),rows)
        for name in ('catalog','keys','watermark'):dump(folder/(name+'.json'),data[name])
        cases=build_cases(data,Oracle(data),profile=='development')
        lines(folder/'answers.jsonl',cases)
        lines(folder/'questions.jsonl',[{k:c[k] for k in ('id','question','difficulty','group_id','dataset_profile')} for c in cases])
        summary=dict(check,source_quality_passed=True,seed=data['seed'],profile=profile,orders=count,
          question_count=len(cases),by_difficulty=dict(Counter(c['difficulty'] for c in cases)),
          literal_normalized_query_groups=len({c['group_id'] for c in cases}),
          core_rows=sum(len(data['rows'][n]) for n,m in data['catalog']['tables'].items() if m['core']),
          edge_cases=data['edge_cases'],paid_orders=sum(o['status']=='已支付' for o in data['rows']['fact_order']),
          refund_rows=len(data['rows']['fact_refund']),ticket_rows=len(data['rows']['fact_service_ticket']),
          channel_distribution=dict(Counter(o['channel_id'] for o in data['rows']['fact_order'])))
        dump(folder/'summary.json',summary);overview[profile]=summary
        if profile=='development':lines(out/'invalid_mutations.jsonl',negative_cases(data))
    for profile in ('holdout','shift'):
        freeze(out/profile/'answers.jsonl',out/'development'/'answers.jsonl',out/'synthetic_criteria.json',out/profile/'frozen_manifest.json',profile='synthetic')
    dump(out/'summary.json',dict(source_kind='synthetic-benchmark',production_eligible=False,model_called=False,profiles=overview,
        limitations=['Not PETKIT internal data','Author-generated holdout; no external independent review','Shift repeats holdout query structures on changed distribution','No model accuracy measured','Complex metric definitions are project assumptions, not owner-approved']))
    (out/'README.md').write_text('''# 灵犀问数合成数据 v2

本数据由脚本生成，用于工程验证与离线选型，不是小佩内部经营数据，不代表真实生产准确率。
development 为开发集；holdout 为冻结留出集；shift 复用留出集结构，测试渠道集中、高退款和高故障等分布变化，不应与留出集叠加声称独立样本。
每套包含40张有关联的核心表、460张检索干扰主题表。干扰表为独立合成系列，仅有12个采样时点，不可用来代替核心明细对账。
tables/*.jsonl 为逐行UTF-8 JSON，金额使用两位小数字符串，日期ISO格式，空值使用null。catalog.json 为字段字典、关系和指标，keys.json 为业务主键。
questions.jsonl 不含答案；answers.jsonl 含Python原始记录计算的参考答案与独立SQL。请勿把留出集加入提示样例、训练或检索知识库。
同一SQL结构按常量归一化聚类；聚类不等于完备的语义等价判定。作者同时编写数据与参考实现，因此不属于外部审查的真正盲测。
数值挑战题的标准答案为ok，即使应用当前不支持也不得改成拒答来提高分数。行为题仅验证澄清合同；文本注入题不替代真实身份、跨租户和撤权测试。
30天队列包含第0与第30天，排除未成熟分母；净销售按原支付月归属，扣截至快照日的行退款；库存挑战题按月末逐SKU逐仓向前取末次快照；金额Decimal精确比较，百分比四舍五入两位，零分母null。
同比按日历同月，累计自当年1月起，仅返回有记录月份；排名并列按产品编码升序；采购先汇总分次收货再关联，避免扇出。
invalid_mutations.jsonl 是独立故障注入说明，不可混入有效源数据。synthetic_criteria.json 中门槛是项目测试目标，没有企业Owner审批，也不代表已达到。
verification.json 仅在真实StarRocks核对后生成，记录参考SQL与Python的数值一致性，不是模型端到端准确率。应用现有7个指标的范围不因加入挑战题自动扩大。
snapshot日期固定2026-10-16，构建版本20261023，不连接真实ERP/OMS/WMS，不发布应用版本。
''',encoding='utf-8')
    hashes={str(p.relative_to(out)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.rglob('*')) if p.is_file()}
    dump(out/'checksums.json',hashes)
    return overview


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',required=True);p.add_argument('--seed',type=int,default=20261023);a=p.parse_args()
    print(json.dumps(build(a.out,a.seed),ensure_ascii=False))
