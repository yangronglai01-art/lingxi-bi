"""Persistent organization workbench: every question is a durable job."""
import streamlit as st
import pandas as pd
from . import control

def main(principal):
    st.subheader('模拟业务工作台')
    st.caption(f'部门：{principal.department} · 地区：{", ".join(principal.regions) or "全部"} · 授权版本：{principal.policy_version}')
    st.info('提交后由后台工作进程执行，刷新可查看进度。取消运行中的任务会停止发布结果，外部模型调用可能已产生费用。')
    scope=f'{principal.subject}:{principal.policy_version}'
    if st.session_state.get('rollout_scope')!=scope:
        st.session_state.pop('rollout_job',None)
        st.session_state['rollout_scope']=scope
    from .retrieval import current_catalog
    from .planning import examples
    cat=current_catalog(); allowed={n for n,t in cat['tables'].items() if principal.allowed(t)}
    metric_ids={m['id'] for m in cat['metrics'] if set(m['dependencies'])<=allowed}
    questions=[x['question'] for x in examples() if x['metric_id'] in metric_ids]
    st.caption(f'当前可见目录：{len(allowed)}张表')
    from .operations_panel import render
    render(principal,cat['generation'])
    selected=st.selectbox('参考问题',questions or ['当前角色没有可用指标'])
    question=st.text_input('业务问题',value=selected)
    if st.button('提交后台任务',disabled=not questions):
        try: st.session_state['rollout_job']=control.submit(principal,question)
        except Exception as exc: st.error(str(exc))
    with control.db() as c:
        recent=c.execute('SELECT id,question,state FROM jobs WHERE subject=? AND policy_version=? ORDER BY created DESC LIMIT 20',(principal.subject,principal.policy_version)).fetchall()
    labels={r['id']:r['question']+' / '+r['state']+' / '+r['id'][:8] for r in recent}
    task_id=st.selectbox('最近提交的任务',list(labels),format_func=lambda x:labels[x]) if labels else None
    st.button('刷新状态')
    if task_id:
        try: job=control.get_job(principal,task_id)
        except Exception: st.warning('该任务不存在或当前授权不可访问'); return
        st.write('状态：'+job['state'])
        if job['state'] in {'queued','running'} and st.button('取消任务'):
            control.cancel(principal,task_id); st.rerun()
        answer=job['result']
        if answer:
            if answer.get('generation') and answer['generation']!=cat['generation']:
                st.warning('这是历史数据版本的结果；如需当前数据，请重新提交任务。')
            st.write('问数状态：'+answer['status'])
            if answer.get('error'): st.warning(answer['error'])
            if (answer.get('context',{}).get('metric') or {}).get('id') in {'activation30','service30'} and not answer.get('context',{}).get('output_contract'):
                st.warning('该历史结果未标注统一百分数契约，请核对SQL单位或重新提交。')
            if answer.get('result'):
                data=answer['result']; st.dataframe(pd.DataFrame(data['rows'],columns=data['columns']),hide_index=True)
            with st.expander('SQL与数据版本'):
                st.code(answer.get('sql',''),language='sql'); st.write(answer.get('generation',''))
        elif job['error']: st.warning(job['error'])
