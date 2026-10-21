"""500-table demo workbench, with explicit retrieval mode and source disclosures."""
import json
import os
import pandas as pd
import plotly.express as px
import streamlit as st
from src.engine.engine import QueryEngine
from .access import authenticate
from .history import history_for
from .planning import examples
from .retrieval import current_catalog
from .settings import MODE


def main():
    st.set_page_config(page_title="灵犀 · 小佩业务模拟问数",page_icon="🐾",layout="wide")
    st.title("灵犀 · 智能宠物硬件问数")
    st.caption("基于小佩公开业务背景的自建模拟项目 · 500表目录 · 非真实企业内部数据")
    with st.sidebar:
        st.header("问数工作台")
        token=st.text_input("访问凭据（模拟业务实例必填）" if os.getenv("PETKIT_DEPLOYMENT")=="simulated" else "访问凭据（本地演示可留空）",value=os.getenv("PETKIT_LOCAL_UI_TOKEN",""),type="password")
        try: principal=authenticate(token)
        except Exception as exc: st.error(str(exc)); st.stop()
        st.caption(f"主体：{principal.subject} / 角色：{principal.role}")
        st.caption(f"运行模式：{MODE}，demo为批准的离线模板，不是大模型生成")
    if os.getenv("PETKIT_DEPLOYMENT")=="simulated":
        from .operations_ui import main as operations_main
        operations_main(principal)
        return
    try: cat=current_catalog()
    except FileNotFoundError:
        st.info("请先运行 python scripts/build_petkit500.py 构建模拟数仓。")
        st.stop()
    allowed={n:t for n,t in cat["tables"].items() if principal.allowed(t)}
    identity_key=f"{principal.subject}:{principal.role}"
    if st.session_state.get("petkit-owner")!=identity_key:
        st.session_state.pop("petkit-answer",None)
    st.session_state["petkit-owner"]=identity_key
    ids={m["id"] for m in cat["metrics"] if set(m["dependencies"])<=set(allowed)}
    approved=[x["question"] for x in examples() if x["metric_id"] in ids]
    history=history_for(principal)
    with st.sidebar:
        st.metric("授权表目录",len(allowed))
        st.caption(f"版本：{cat['generation']} / 数据截止：{cat['as_of']}")
        panel=st.radio("查看",["数据字典","指标口径","历史收藏"])
        if panel=="数据字典":
            core=st.checkbox("只看核心表",value=True)
            keyword=st.text_input("搜索表")
            candidates=[t for t in allowed.values() if (not core or t["core"]) and
                        (not keyword or keyword in t["name"]+t["description"])]
            st.caption(f"匹配{len(candidates)}张，最多展示50张")
            for t in candidates[:50]:
                with st.expander(t["name"]):
                    st.write(t["description"]); st.caption(t["grain"])
                    st.dataframe(pd.DataFrame([dict(column=c,**v) for c,v in t["columns"].items()]),hide_index=True)
        elif panel=="指标口径":
            for m in cat["metrics"]:
                if m["id"] in ids:
                    with st.expander(m["name"]): st.write(m["definition"])
        else:
            favorite_only=st.checkbox("只看收藏")
            for item in history.list(favorite_only=favorite_only,limit=30):
                with st.expander(item["question"]):
                    st.code(item["sql"],language="sql")
                    st.write(item["summary"])
                    if st.button("切换收藏",key=f"fav-{item['id']}"):
                        history.toggle_favorite(item["id"]); st.rerun()
    c1,c2,c3=st.columns(3)
    c1.metric("目录表数",len(cat["tables"]))
    c2.metric("核心表数",sum(t["core"] for t in cat["tables"].values()))
    c3.metric("批准示例数",len(approved))
    if MODE=="demo": st.info("离线模板模式：仅执行示例中的批准问题；未配置真实模型也可演示。")
    chosen=st.selectbox("批准示例",[""]+approved)
    with st.form("question-form"):
        question=st.text_input("输入中文问题",value=chosen,placeholder="例如：各型号售出30天售后设备率")
        submitted=st.form_submit_button("开始分析",type="primary")
    if submitted:
        with st.spinner("检索元数据、校验并执行查询…"):
            answer=QueryEngine().ask(question,principal=principal)
        history.add(question=answer.question,sql=answer.sql,result=answer.result,chart=answer.chart,
                    insights=answer.insights,error=answer.error,mock_used=answer.mock_used)
        st.session_state["petkit-answer"]=answer
    answer=st.session_state.get("petkit-answer")
    if answer:
        st.caption(f"状态：{answer.status} / 请求：{answer.request_id} / 耗时：{answer.elapsed_ms}ms")
        if answer.error:
            if answer.status in {"clarification","unsupported"}: st.warning(answer.error)
            else: st.error(answer.error)
        for insight in answer.insights: st.write(insight)
        if answer.context:
            with st.expander("检索依据与最终选表",expanded=True):
                st.caption(answer.context.get("retrieval_method",""))
                st.dataframe(pd.DataFrame(answer.context.get("selection",[])),hide_index=True)
                st.json(answer.context.get("evidence",[]))
        with st.expander("查询计划与口径"): st.json(answer.plan)
        if answer.sql: st.code(answer.sql,language="sql")
        if answer.result:
            df=pd.DataFrame(answer.result.rows,columns=answer.result.columns)
            st.dataframe(df,hide_index=True)
            numbers=list(df.select_dtypes(include="number").columns)
            categories=[c for c in df.columns if c not in numbers]
            if len(df) and numbers and categories:
                st.plotly_chart(px.bar(df,x=categories[0],y=numbers[-1]),width="stretch")
