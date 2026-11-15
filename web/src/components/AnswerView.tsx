import type { Answer } from "../types";
import { lazy, Suspense, useState } from "react";
import { resultChart } from "./chart-data";
const ChartView = lazy(() => import("./ChartView").then((m) => ({ default: m.ChartView })));

export function AnswerView({ answer, onFeedback }: { answer: Answer; onFeedback?: (vote: string, category: string, comment: string) => Promise<void> }) {
  const [vote, setVote] = useState("negative");
  const [category, setCategory] = useState("other");
  const [comment, setComment] = useState("");
  const [feedbackState, setFeedbackState] = useState("");
  const result = answer.result;
  const metadata = answer as unknown as { answer_policy?: string; covered_channels?: string[] };
  const watermark = (answer.context?.watermark ?? (answer as unknown as Record<string, unknown>).watermark) as Record<string, unknown> | undefined;
  const cutoff = String(watermark?.event_time ?? answer.context?.data_as_of ?? "未提供");
  const approvedSla = answer.freshness_sla_seconds;
  const sla = typeof approvedSla === "number" && approvedSla > 0 ? approvedSla : 86400;
  const stale = !Number.isFinite(Date.parse(cutoff)) || Date.now() - Date.parse(cutoff) > sla * 1000;
  const chart = answer.truncated ? null : answer.chart ?? resultChart(result, answer.question);
  return (
    <div className="answer">
      <p className="muted">数据截至：{cutoff}</p>
      {stale && <p role="alert">{approvedSla ? `数据水位超过批准的${sla}秒刷新要求或无法确认` : "数据水位超过24小时技术警戒值或无法确认（业务SLA待签收）"}，请核对同步状态；本结果不代表最新经营状态。</p>}
      {metadata.answer_policy === "partial" && <p role="alert">仅展示已覆盖渠道：{metadata.covered_channels?.join("、")}。全渠道合计暂缓。</p>}
      {metadata.answer_policy === "coverage_unverified" && <p>当前快照的数据覆盖范围尚未验证。</p>}
      {answer.error && <div className="error-banner">{answer.error}</div>}

      <p className="muted">数据版本：{answer.generation} · 路径：{String((answer as unknown as Record<string, unknown>).generation_mode ?? "未标记")}</p>
      {answer.truncated && <p role="alert">结果已截断，请缩小范围后重新查询。</p>}
      <details><summary>指标口径与数据水位</summary><pre>{JSON.stringify({ context: answer.context, plan: answer.plan }, null, 2)}</pre></details>
      {chart && <Suspense fallback={<p role="status">图表加载中，精确数据可先查看下表。</p>}><ChartView chart={chart} /></Suspense>}
      {onFeedback && <form onSubmit={(e) => { e.preventDefault(); setFeedbackState("提交中"); void onFeedback(vote, category, comment).then(() => setFeedbackState("反馈已提交，待人工审核")).catch(() => setFeedbackState("反馈提交失败，请稍后重试")); }}>
        <label>反馈评价<select aria-label="反馈评价" value={vote} onChange={(e) => setVote(e.target.value)}><option value="negative">需要改进</option><option value="positive">结果有帮助</option></select></label>
        <label>问题分类<select aria-label="问题分类" value={category} onChange={(e) => setCategory(e.target.value)}>{["correct","metric","retrieval","generation","data","permission","performance","other"].map((c) => <option key={c} value={c}>{({correct:"正确",metric:"口径",retrieval:"检索",generation:"生成",data:"数据",permission:"权限",performance:"性能",other:"其他"} as Record<string,string>)[c]}</option>)}</select></label>
        <textarea aria-label="反馈说明" maxLength={2000} value={comment} onChange={(e) => setComment(e.target.value)} placeholder="请描述问题，避免填写个人信息" />
        <button type="submit" disabled={feedbackState === "提交中" || feedbackState.includes("已提交")}>提交反馈</button><span role="status">{feedbackState}</span>
      </form>}

      {result && (
        <>
          <div className="section-title">结果（{result.row_count} 行）</div>
          {result.rows.length > 0 ? (
            <table className="result-table">
              <thead>
                <tr>
                  {result.columns.map((c) => (
                    <th key={c}>{c}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {result.rows.map((row, i) => (
                  <tr key={i}>
                    {result.columns.map((_, j) => (
                      <td key={j}>
                        {formatCell(row[j])}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="muted">无返回数据。</p>
          )}
        </>
      )}

      {answer.insights.length > 0 && (
        <>
          <div className="section-title">洞察</div>
          <ul className="insights">
            {answer.insights.map((ins, i) => (
              <li key={i}>{ins}</li>
            ))}
          </ul>
        </>
      )}

      {answer.sql && (
        <>
          <div className="section-title">生成的 SQL</div>
          <pre className="sql-block">{answer.sql}</pre>
        </>
      )}

      {answer.mock_used && (
        <p className="muted">※ 本次为演示数据（mock），未命中真实数仓。</p>
      )}
    </div>
  );
}

function formatCell(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") {
    return Number.isInteger(v) ? String(v) : v.toLocaleString("zh-CN");
  }
  return String(v);
}
