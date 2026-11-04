"""Paired first-attempt review ablation; reports observations, never enables review."""
import argparse
import json
from pathlib import Path


def compare(baseline,candidate):
    def indexed(rows):
        if len({r['id'] for r in rows})!=len(rows) or any(r.get('attempt',1)!=1 for r in rows):raise ValueError('Unique first attempts required')
        return {r['id']:r for r in rows}
    a,b=indexed(baseline),indexed(candidate)
    if not a or set(a)!=set(b):raise ValueError('Identical nonempty frozen case IDs required')
    improved=regressed=review_blocks=0;latency_delta=[]
    for cid in a:
        old,new=a[cid],b[cid]
        if old.get('difficulty')!=new.get('difficulty'):raise ValueError('Case strata changed')
        op=old['judgment'].startswith('passed_');np=new['judgment'].startswith('passed_')
        improved+=int(np and not op);regressed+=int(op and not np)
        review_blocks+=int(op and not np and new.get('first_result',{}).get('clarification_reason')=='review')
        latency_delta.append(float(new['elapsed_seconds'])-float(old['elapsed_seconds']))
    return dict(paired_cases=len(a),improved=improved,regressed=regressed,correct_baseline_blocked_by_review=review_blocks,
        mean_latency_delta_seconds=sum(latency_delta)/len(latency_delta),enable_authorized=False,
        note='Confirm same dataset/frozen criteria, data release and generator revision; control stochasticity and run order. This summary alone does not prove causal benefit or authorize enforcement.')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('baseline','candidate','out'):p.add_argument('--'+n,required=True)
    a=p.parse_args()
    def read(path):
        path=Path(path)
        if path.stat().st_size>50_000_000:raise ValueError('Run exceeds size budget')
        return [json.loads(l) for l in path.read_text(encoding='utf-8').splitlines() if l.strip()]
    result=compare(read(a.baseline),read(a.candidate))
    with Path(a.out).open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2)


if __name__=='__main__':main()
