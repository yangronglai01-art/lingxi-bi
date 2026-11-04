"""Frozen first-attempt release criteria, split by intended generation path."""
import math


def report(records, cases, criteria=None):
    references={c['id']:c for c in cases}
    if len(references)!=len(cases) or {r['id'] for r in records}!=set(references) or len(records)!=len(cases):
        raise ValueError('Every frozen case needs exactly one first attempt')
    output={};violations=[]
    for difficulty in ('simple','join','complex','behavior'):
        group=[r for r in records if references[r['id']]['difficulty']==difficulty]
        breakdown={}
        for path in ('model','template_or_behavior'):
            selected=[r for r in group if bool(references[r['id']].get('requires_model'))==(path=='model')]
            passed=sum(r['judgment'].startswith('passed_') for r in selected)
            wrong=sum(r.get('first_result',{}).get('status')=='ok' and not r['judgment'].startswith('passed_') for r in selected)
            latencies=sorted(float(r['elapsed_seconds']) for r in selected)
            if any(not math.isfinite(n) or n<0 for n in latencies):raise ValueError('Invalid elapsed time')
            breakdown[path]=dict(total=len(selected),passed=passed,wrong_answers=wrong,
                correct_rate=passed/len(selected) if selected else None,wrong_answer_rate=wrong/len(selected) if selected else None,
                p95_seconds=latencies[math.ceil(.95*len(latencies))-1] if latencies else None,
                p99_seconds=latencies[math.ceil(.99*len(latencies))-1] if latencies else None,
                statuses={s:sum(r.get('first_result',{}).get('status')==s for r in selected)
                    for s in sorted({r.get('first_result',{}).get('status','unknown') for r in selected})})
        output[difficulty]=dict(total=len(group),by_expected_path=breakdown)
        if criteria:
            rule=criteria['by_difficulty'][difficulty]
            if len(group)<rule['min_cases']:violations.append(difficulty+': insufficient samples')
            for path,stats in breakdown.items():
                if not stats['total']:continue
                if stats['correct_rate']<rule['min_correct_rate']:violations.append(difficulty+'/'+path+': correct rate')
                if stats['wrong_answer_rate']>rule['max_wrong_answer_rate']:violations.append(difficulty+'/'+path+': wrong answer rate')
                if stats['p95_seconds']>rule['max_p95_seconds']:violations.append(difficulty+'/'+path+': latency')
    for r in records:
        if references[r['id']].get('zero_tolerance') is True and not r['judgment'].startswith('passed_'):
            violations.append('zero-tolerance case failed: '+str(r['id']))
    return dict(by_difficulty=output,gate_passed=not violations if criteria else None,violations=violations,
        note='First attempts, including failures/timeouts. Model and template paths are judged separately. Percentiles are sample observations, not capacity guarantees.')
