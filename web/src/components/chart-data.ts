import type { ChartConfig, QueryResult } from "../types";

export function resultChart(result: QueryResult | null, question: string): ChartConfig | null {
  if (!result || result.columns.length < 2 || !result.rows.length || result.rows.length > 50) return null;
  const column = result.columns.length - 1;
  const values = result.rows.map((r) => r[column] === null ? NaN : Number(r[column]));
  if (values.some((v) => !Number.isFinite(v) || Math.abs(v) > 1e12)) return null;
  return { chart_type: /每月|趋势/.test(question) ? "line" : "bar", title: result.columns[column], x: result.columns[0], y: [result.columns[column]],
    labels: result.rows.map((r) => String(r[0])), values, reason: "图表为数值近似展示，精确金额以结果表Decimal字符串为准。" };
}

