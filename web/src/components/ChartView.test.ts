import { expect, it } from "vitest";
import { resultChart } from "./ChartView";
it("Decimal字符串图表转换不修改精确结果", () => {
  const result = { columns: ["product", "amount"], rows: [["A", "12.03"]], row_count: 1 };
  expect(resultChart(result, "净销售额")?.values).toEqual([12.03]);
  expect(result.rows[0][1]).toBe("12.03");
});
it("超精度或非数值结果保留表格", () => {
  expect(resultChart({ columns: ["id", "amount"], rows: [["A", "123456789012345678.01"]], row_count: 1 }, "金额")).toBeNull();
  expect(resultChart({ columns: ["id", "amount"], rows: [["A", null]], row_count: 1 }, "金额")).toBeNull();
});
it("月度趋势使用折线图", () => {
  expect(resultChart({ columns: ["month", "amount"], rows: [["2026-09", "12.03"]], row_count: 1 }, "每月净销售额趋势")?.chart_type).toBe("line");
});
