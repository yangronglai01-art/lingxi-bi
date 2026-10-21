"""On-demand operational panel for the persistent global analyst only."""
import streamlit as st

def render(principal,generation):
    if not principal.managed or principal.role!='analyst' or principal.regions:return
    scope=(principal.subject,principal.policy_version)
    if st.session_state.get('ops_health_scope')!=scope:
        st.session_state.pop('ops_health_snapshot',None);st.session_state['ops_health_scope']=scope
    with st.expander('运维诊断',expanded=False):
        st.caption('只读快照，按需读取；不自动修复或发送通知。近期结果最多200个样本。')
        if st.button('读取运维快照'):
            try:
                from .diagnostics import snapshot
                st.session_state['ops_health_snapshot']=snapshot(principal)
            except Exception:
                st.session_state.pop('ops_health_snapshot',None);st.warning('运维快照暂不可用，请检查当前授权及控制库。')
        value=st.session_state.get('ops_health_snapshot')
        if not value:return
        labels={'healthy':'运行健康','degraded':'需要关注','unavailable':'暂不可用'}
        getattr(st,{'healthy':'success','degraded':'warning','unavailable':'error'}[value['status']])(labels[value['status']])
        st.caption('采集时间（UTC）：'+value['observed_at']+'；数据版本：'+str(value['generation']))
        if value['generation']!=generation:st.warning('这是历史数据版本的运维快照，请重新读取。')
        cols=st.columns(4)
        for col,label,number in zip(cols,['排队任务','运行任务','本次守护重启','运行失败样本'],[value['queue']['queued'],value['queue']['running'],value['supervisor']['restarts'] if value['supervisor']['restarts'] is not None else '人工模式',value['recent']['execution_failures']]):col.metric(label,number)
        st.write('守护状态：'+value['supervisor']['phase'])
        st.write('工作进程心跳距今（秒）：'+str(value['worker']['heartbeat_age_seconds']))
        for alert in value['alerts']:
            st.warning(alert['message']);st.caption(alert['action'])
        recent=value['recent'];st.caption(f"最近{recent['window_minutes']}分钟，读取{recent['sample_count']}/{recent['terminal_count']}个终态任务；成功率排除拒绝、澄清、中断、取消、替代及不支持。")
        if recent['truncated']:st.warning('近期任务超过200个，结果分类与耗时仅代表最新200个样本。')
        st.write('结果分类');st.json(recent['outcomes'])
        st.write('成功任务排队至完成耗时（秒）');st.json(recent['successful_end_to_end_seconds'])
        if recent['failures']:
            st.write('近期异常任务（仅标识及分类）');st.dataframe(recent['failures'],hide_index=True)
