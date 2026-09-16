# -*- coding: utf-8 -*-
"""Streamlit 工作台：中文自然语言问数前端。

布局：
- 主区域：问题输入框 + 示例问题；结果区依次展示
  洞察 → 图表（plotly）→ 结果表格 → 生成 SQL（可折叠）
- 侧边栏：数据字典 / 历史记录 / 我的收藏（三栏切换）
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src import config, schema
from src.engine.engine import QueryEngine
from src.history import HistoryStore

# ---------------------------------------------------------------------------
# 页面配置
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="恒岳汽车零部件 · 经营分析智能问数系统",
    page_icon="🚗",
    layout="wide",
)

# 示例问题（点一下即可快速体验）
EXAMPLES = [
    "2026 年 3 月各产品销售额是多少？",
    "各区域的销售额占比如何？",
    "近 12 个月销售额趋势",
    "哪条产线的缺陷率最高？",
    "各产品的平均单价是多少？",
]


# ---------------------------------------------------------------------------
# 单例资源
# ---------------------------------------------------------------------------
@st.cache_resource
def get_engine() -> QueryEngine:
    """缓存问数引擎实例，避免每次刷新重复初始化。"""
    return QueryEngine()


@st.cache_resource
def get_store() -> HistoryStore:
    """缓存历史记录仓库实例。"""
    return HistoryStore()


engine = get_engine()
store = get_store()


# ---------------------------------------------------------------------------
# 渲染辅助函数
# ---------------------------------------------------------------------------
def render_chart(chart):
    """根据 chart_type 渲染 plotly 图表，返回 Figure 或 None。

    参数:
        chart: 引擎返回的 ChartConfig（chart_type ∈ indicator/line/bar/pie/table）。

    返回:
        plotly Figure（indicator/line/bar/pie），table 类型返回 None（用表格展示）。
    """
    if chart is None:
        return None

    ct = chart.chart_type
    if ct == "indicator":
        value = chart.values[0] if chart.values else 0
        return go.Figure(
            go.Indicator(mode="number", value=value, title={"text": chart.title or "指标"})
        )
    if ct == "line":
        fig = go.Figure(
            go.Scatter(
                x=chart.labels, y=chart.values, mode="lines+markers",
                name=chart.y[0] if chart.y else "数值",
            )
        )
        fig.update_layout(title=chart.title, xaxis_title=chart.x or "")
        return fig
    if ct == "bar":
        fig = go.Figure(
            go.Bar(x=chart.labels, y=chart.values, name=chart.y[0] if chart.y else "数值")
        )
        fig.update_layout(title=chart.title, xaxis_title=chart.x or "")
        return fig
    if ct == "pie":
        fig = go.Figure(go.Pie(labels=chart.labels, values=chart.values))
        fig.update_layout(title=chart.title)
        return fig
    return None  # table → 用 st.dataframe 展示


def run_query(question: str):
    """执行问数并把结果写入历史，返回 Answer。"""
    answer = engine.ask(question)
    store.add(
        question=answer.question,
        sql=answer.sql,
        result=answer.result,
        chart=answer.chart,
        insights=answer.insights,
        error=answer.error,
        mock_used=answer.mock_used,
    )
    return answer


def render_answer(answer) -> None:
    """渲染一次问数的完整结果（洞察 → 图表 → 表格 → SQL）。"""
    if answer.error:
        st.error(f"❌ {answer.error}")
        if answer.sql:
            with st.expander("🔍 已生成 SQL（未通过校验 / 执行失败）"):
                st.code(answer.sql, language="sql")
        return

    # 洞察
    if answer.insights:
        st.markdown("### 🧠 AI 洞察")
        for ins in answer.insights:
            st.markdown(f"- {ins}")
        st.divider()

    if answer.mock_used:
        st.info("ℹ️ 当前为 mock 降级模式（未连接大模型或 ENABLE_MOCK=true），SQL 与洞察由规则生成。")

    # 图表
    if answer.chart is not None:
        fig = render_chart(answer.chart)
        if fig is not None:
            st.markdown("### 📊 可视化")
            st.plotly_chart(fig, width="stretch")
            st.caption(f"选图依据：{answer.chart.reason}")

    # 结果表格
    if answer.result is not None:
        st.markdown(
            f"### 📋 查询结果（{answer.result.row_count} 行 × {len(answer.result.columns)} 列）"
        )
        df = pd.DataFrame(answer.result.rows, columns=answer.result.columns)
        st.dataframe(df, width="stretch", height=360)

    # SQL
    with st.expander("🔍 查看生成的 SQL"):
        st.code(answer.sql, language="sql")


# ---------------------------------------------------------------------------
# 侧边栏面板
# ---------------------------------------------------------------------------
def show_schema_panel() -> None:
    """侧边栏：数据字典。"""
    st.markdown("#### 📖 数据字典")
    for t in schema.TABLES.values():
        with st.expander(f"{t.name} · {t.comment}"):
            df = pd.DataFrame(
                [
                    {"字段": c.name, "类型": c.dtype, "说明": c.description, "示例": c.example}
                    for c in t.columns
                ]
            )
            st.dataframe(df, hide_index=True, width="stretch")


def show_history_panel(favorite_only: bool) -> None:
    """侧边栏：历史记录 / 我的收藏。"""
    items = store.list(favorite_only=favorite_only)
    if not items:
        st.info("暂无收藏" if favorite_only else "暂无历史记录，快去问一个问题吧～")
        return
    for it in items:
        star = "⭐" if it["favorited"] else "🕘"
        with st.expander(f"{star} {it['summary']} · {it['created_at'][:16]}"):
            st.markdown(it["question"])
            c1, c2 = st.columns(2)
            if c1.button("🔁 重新提问", key=f"refill_{it['id']}", width="stretch"):
                st.session_state.q = it["question"]
                st.rerun()
            fav_label = "💔 取消收藏" if it["favorited"] else "❤️ 收藏"
            if c2.button(fav_label, key=f"fav_{it['id']}", width="stretch"):
                store.toggle_favorite(it["id"])
                st.rerun()


# ---------------------------------------------------------------------------
# 侧边栏
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("## 🚗 恒岳 BI 工作台")
    st.caption("汽车零部件 · 经营分析智能问数系统")
    st.divider()

    panel = st.radio(
        "侧栏功能", ["📖 数据字典", "🕘 历史记录", "⭐ 我的收藏"],
        label_visibility="collapsed",
    )
    st.divider()

    if panel == "📖 数据字典":
        show_schema_panel()
    elif panel == "🕘 历史记录":
        show_history_panel(favorite_only=False)
    else:
        show_history_panel(favorite_only=True)

    st.divider()
    wh_ok = config.WAREHOUSE_PATH.exists()
    st.caption(f"数仓：{'✅ 已就绪' if wh_ok else '⚠️ 未构建（请先运行 scripts/build_all.py）'}")
    st.caption(f"模型：{config.MODEL_NAME}")


# ---------------------------------------------------------------------------
# 主区域：问数工作台
# ---------------------------------------------------------------------------
st.title("🚗 恒岳汽车零部件 · 经营分析智能问数系统")
st.caption("用中文提问，自动生成 SQL、查询数仓、绘制图表并给出 AI 洞察")

st.markdown("### 💬 中文自然语言问数")
question = st.text_input(
    "请输入经营分析问题",
    key="q",
    placeholder="例如：2026 年 3 月各产品销售额是多少？",
)
run_clicked = st.button("🔍 开始分析", type="primary")

# 示例问题（点击即提问）
st.markdown("**💡 试试示例：**")
_ex_cols = st.columns(len(EXAMPLES))
for i, ex in enumerate(EXAMPLES):
    if _ex_cols[i].button(ex, key=f"example_{i}"):
        st.session_state.q = ex
        st.session_state.run_example = ex
        st.rerun()

# 触发条件：点「开始分析」或点示例
if run_clicked:
    final_q = question
    st.session_state.pop("run_example", None)
elif "run_example" in st.session_state:
    final_q = st.session_state.pop("run_example")
else:
    final_q = None

st.divider()

if final_q:
    if not final_q.strip():
        st.warning("问题不能为空，请输入后重试～")
    else:
        with st.spinner("正在生成 SQL、查询数仓、分析洞察……"):
            answer = run_query(final_q)
        render_answer(answer)
else:
    st.info("👈 在输入框提问，或点击上方示例快速体验。")
