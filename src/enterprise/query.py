"""Pinned, permission-filtered metadata and private local-chat orchestration."""
import json
import time
import logging
from .intent import derive,verify_sql,Clarify,approved_intent_example
from .review import review
from functools import lru_cache
from src.petkit.guard import check
from src.petkit.planning import plan,examples,normalize,sales_sql
from src.petkit.retrieval import Index
from src.petkit.semantic import validate_metric
from .execution import Executor,bind
from .contract import validate
from .metric_contracts import resolve
from .generation import Generator, ResultContractError, repair_feedback
from .schema_context import authorized_catalog, fuse, build as build_context


@lru_cache(maxsize=4)
def index(serialized,model_path):return Index(json.loads(serialized),model_path=model_path)


class Query:
    def __init__(self,settings,store,transport=None):
        self.settings=settings;self.store=store;self.executor=Executor(settings);self.transport=transport

    def context(self,p,release,question,metric):
        catalog=release['catalog'];mapping=release['views'].get(p.scope,{})
        allowed={n for n,t in catalog['tables'].items() if n in mapping and p.allowed(t)}
        required=(metric or {}).get('dependencies',[])
        if set(required)-allowed:raise PermissionError('Metric outside granted business domains')
        visible=authorized_catalog(catalog,allowed)
        evidence=index(json.dumps(visible,sort_keys=True),'').search(question,allowed,limit=24,method='bm25')
        dense=[]
        if self.settings.embedding_revision:
            from .vectors import search
            dense=search(self.settings,self.store,release,question,allowed,self.settings.embedding_revision,self.transport,limit=24)
        ranked=fuse(evidence,dense,allowed)
        contract=resolve(derive(question,catalog),catalog)
        result=build_context(catalog,allowed,metric,ranked,question,contract,self.settings.max_context_chars)
        result.update(generation=release['id'],watermark=release['watermark'],
            retrieval_method='BGE-M3 + BM25 + RRF' if self.settings.embedding_revision else 'BM25 (no semantic embedding)',embedding_revision=self.settings.embedding_revision or None,
            evidence=evidence,retrieval_candidates=dict(bm25=[e['table'] for e in evidence],bge=dense,fused=ranked),
            retrieval_policy='authorized-connected-schema-v1')
        return result

    def run(self,job,p):
        started=time.monotonic();trace={'stage':'intent','at':started,'durations':[]};outcome='error'
        try:
            result=self._run(job,p,trace);outcome=result['status'] if result['status'] in {'ok','clarification'} else 'error'
            return result
        except Clarify as exc:
            outcome='clarification'
            return dict(status='clarification',answer_policy='clarify',question=job['question'],request_id=job['id'],generation=job['release'],elapsed_ms=int((time.monotonic()-started)*1000),context={},plan={},sql='',result=None,chart=None,insights=[],error=str(exc),mock_used=False,truncated=False,clarification_reason=exc.reason,clarification_details=exc.details,semantic_review=exc.details if exc.reason=='review' else None)
        finally:
            trace['durations'].append((trace['stage'],outcome,time.monotonic()-trace['at']))
            trace['durations'].append(('total',outcome,time.monotonic()-started))
            from .features import observe_batch
            try:observe_batch(self.store,p.tenant,trace['durations'])
            except Exception:logging.getLogger(__name__).error('analytics_telemetry_unavailable request_id=%s',job['id'])

    def _run(self,job,p,trace):
        def phase(stage,outcome='ok'):
            now=time.monotonic();trace['durations'].append((trace['stage'],outcome,now-trace['at']));trace.update(stage=stage,at=now)
        begin=time.monotonic();model_elapsed=0
        self.store.verify(p)
        if p.tenant!=self.settings.tenant or p.scope not in self.settings.scopes or tuple(self.settings.scopes[p.scope].get('regions',[]))!=p.regions:
            raise PermissionError('Scope binding differs from workforce grant')
        release=self.store.release(p.tenant,job['release']);question=job['question']
        intent=derive(question,release['catalog'])
        proposal=plan(question,as_of=release['catalog']['as_of'])
        base=dict(question=question,request_id=job['id'],generation=job['release'],mock_used=False,
            watermark=release['watermark'],elapsed_ms=0,truncated=False,context={},plan={},sql='',result=None,chart=None,insights=[],error='')
        if proposal['status']!='ready':return dict(base,status=proposal['status'],error=proposal['message'])
        metric=next((m for m in release['catalog']['metrics'] if m['id']==(proposal.get('metric') or {}).get('id')),None)
        if not metric or metric['id'] not in self.settings.enabled_metrics:
            raise Clarify('本指标尚未在当前环境启用，需要指标Owner确认后发布。',reason='metric_disabled',
                          details={'missing_slots':['enabled_metric'],'requires_owner_approval':True})
        base['intent']=intent.payload()
        contract=resolve(intent,release['catalog']);base['metric_contract']=contract
        coverage = release['watermark'].get('metric_coverage', {}).get(metric['id'], {}).get(p.scope)
        policy = 'full' if coverage and coverage.get('status') == 'complete' else 'coverage_unverified'
        covered = set()
        if metric['id'] == 'net_sales' and coverage and coverage.get('status') == 'incomplete':
            covered = set(coverage.get('covered_channels') or [])
            if intent.group != 'channel' or not covered:
                return dict(base, status='error', answer_policy='withheld',
                            error_code='METRIC_COVERAGE_INCOMPLETE',
                            error='当前权限范围内的净销售额数据覆盖不完整，暂缓出数。')
            policy = 'partial'
        phase('retrieval')
        retrieval_begin=time.monotonic()
        context=self.context(p,release,question,metric)
        retrieval_elapsed=time.monotonic()-retrieval_begin
        approved=next((x for x in examples(release['catalog']['as_of']) if normalize(x['question'])==normalize(question)),None)
        route='exact_question' if approved else None
        if approved is None and getattr(self.settings,'approved_intent_routing',True):
            approved=approved_intent_example(intent,release['catalog'])
            if approved:route='validated_intent_signature'
        if policy == 'partial':
            approved = {'sql': sales_sql('channel', intent.month)}
            route = 'coverage_channel_template'
        mode='approved_template' if approved else 'local-chat'
        base['routing']=dict(policy='approved-intent-v1',reason=route or 'model_required',
                             model_bypassed=bool(approved))
        base.update(generation_mode=mode,model_revision=self.settings.model_revision if mode=='local-chat' else None)
        def alive():
            self.store.verify(p)
            return self.store.running(job)
        generator=Generator(self.settings,self.transport)
        attempts=[];previous=None;sql_deadline=None
        for attempt in range(1,3):
            sql=''
            try:
                if approved:
                    sql=approved['sql']
                else:
                    phase('model')
                    obj=generator.generate(question,context,contract,alive,previous)
                    if obj['status']=='clarification':
                        return dict(base,status='clarification',answer_policy='clarify',error=obj['message'][:1000],
                            clarification_reason='model',generation_attempts=attempts+[dict(attempt=attempt,outcome='clarification')])
                    sql=obj['sql']
                phase('semantic')
                checked=check(sql,context,self.settings.max_rows)
                validate_metric(checked.sql,context)
                semantic=verify_sql(checked.sql,intent,release['catalog'],self.settings.max_rows)
                phase('review')
                review_result=review(self.settings,intent,checked.sql,context,self.transport) if mode=='local-chat' else dict(mode='disabled',verdict='approved_template_skip',elapsed_seconds=0)
                if mode=='local-chat' and self.settings.review_mode=='enforce' and review_result['verdict']!='consistent':
                    raise Clarify('语义复核未确认一致，请澄清后重试。',reason='review',details=review_result)
                if not alive():raise TimeoutError('QUERY_CANCELLED')
                phase('sql')
                physical=bind(checked.sql,release['views'][p.scope])
                if sql_deadline is None:sql_deadline=time.monotonic()+self.settings.query_seconds
                result=self.executor.run(p.scope,physical,alive,lambda cid:self.store.executing(job,cid),deadline=sql_deadline)
                try:validate(metric,result)
                except ValueError as exc:raise ResultContractError('RESULT_CONTRACT_INVALID') from exc
                if policy == 'partial':
                    if 'dimension' not in result['columns'] or result['truncated']:
                        return dict(base,status='error',answer_policy='withheld',
                                    error_code='PARTIAL_RESULT_INVALID',error='无法确认渠道覆盖范围，暂缓出数。')
                    column = result['columns'].index('dimension')
                    rows = [row for row in result['rows'] if row[column] in covered]
                    result = dict(result,rows=rows,row_count=len(rows))
                    base['covered_channels'] = sorted(covered)
                attempts.append(dict(attempt=attempt,outcome='passed'))
                break
            except Exception as exc:
                feedback=repair_feedback(exc)
                attempts.append(dict(attempt=attempt,outcome='failed',code=feedback['code'] if feedback else 'terminal'))
                if approved or feedback is None:raise
                if attempt==2:
                    return dict(base,status='error',sql='',result=None,error='有限修复后仍未通过执行或语义校验，未返回查询结果；请联系维护人员。',
                        error_code='SQL_REPAIR_EXHAUSTED',failure_reason='repair_exhausted',generation_attempts=attempts,
                        repair_count=1,model_elapsed_seconds=round(generator.spent,4))
                if not alive():raise TimeoutError('QUERY_CANCELLED')
                if generator.spent>=self.settings.model_seconds:raise TimeoutError('MODEL_BUDGET_EXHAUSTED')
                if sql_deadline is not None and time.monotonic()>=sql_deadline:raise TimeoutError('SQL_DEADLINE')
                previous=dict(previous_sql=sql,feedback=feedback)
                phase('repair',outcome='error')
        model_elapsed=generator.spent
        self.store.verify(p)
        return dict(base,status='ok',answer_policy=policy,elapsed_ms=int((time.monotonic()-begin)*1000),model_elapsed_seconds=round(model_elapsed,4),retrieval_seconds=round(retrieval_elapsed,4),truncated=result['truncated'],chart=None,insights=[],error='',sql=checked.sql,plan=dict(intent.payload(),dimensions=[{'model':'product'}.get(intent.group,intent.group)],time_range={'month':intent.month,'as_of':intent.as_of},grain=metric.get('grain'),unit=metric.get('unit'),approved_dependencies=metric['dependencies'],assumptions=[metric['definition']],source='server_approved_intent'),
            generation_attempts=attempts,repair_count=max(0,len(attempts)-1),result=result,context={k:v for k,v in context.items() if k!='tables'},generation_mode=mode,
            semantic_validation=semantic,semantic_review=review_result,freshness_sla_seconds=metric.get('freshness_sla_seconds'),metric_version=context['version'],source_kind=release['watermark'].get('source_kind','unverified'))
