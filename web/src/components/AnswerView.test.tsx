// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { AnswerView } from "./AnswerView";
import type { Answer } from "../types";

/**
 * AnswerView 组件测试：覆盖「工作台渲染结果」——结果表 / 洞察 / SQL / 错误 / mock 提示。
 */

function baseAnswer(overrides: Partial<Answer> = {}): Answer {
  return {
    status: "success",
    request_id: "r-1",
    generation: "g-1",
    elapsed_ms: 10,
    context: {},
    plan: {},
    truncated: false,
    question: "各型号净销售额",
    sql: "",
    result: null,
    chart: null,
    insights: [],
    error: "",
    mock_used: false,
    ...overrides,
  };
}

describe("AnswerView", () => {
  it("渲染结果表格（列名 + 行数）", () => {
    render(
      <AnswerView
        answer={baseAnswer({
          result: {
            columns: ["型号", "净销售额"],
            rows: [
              ["A", 100],
              ["B", 200],
            ],
            row_count: 2,
          },
        })}
      />,
    );

    expect(screen.getByText("型号")).toBeTruthy();
    expect(screen.getByRole("columnheader", { name: "净销售额" })).toBeTruthy();
    expect(screen.getByText("结果（2 行）")).toBeTruthy();
  });

  it("渲染洞察与生成的 SQL", () => {
    render(
      <AnswerView
        answer={baseAnswer({
          sql: "SELECT model, SUM(amount) AS total FROM sales GROUP BY model",
          insights: ["洞察一", "洞察二"],
        })}
      />,
    );

    expect(screen.getByText("洞察一")).toBeTruthy();
    expect(screen.getByText("洞察二")).toBeTruthy();
    expect(screen.getByText(/SELECT model/)).toBeTruthy();
  });

  it("渲染错误横幅", () => {
    render(<AnswerView answer={baseAnswer({ error: "查询失败：权限不足" })} />);
    expect(screen.getByText("查询失败：权限不足")).toBeTruthy();
  });

  it("命中 mock 时渲染演示数据提示", () => {
    render(<AnswerView answer={baseAnswer({ mock_used: true })} />);
    expect(screen.getByText(/演示数据/)).toBeTruthy();
  });
});
