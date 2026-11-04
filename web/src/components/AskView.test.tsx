// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { AskView } from "./AskView";
import type { ApiClient } from "../lib/api";

/**
 * AskView 组件测试：覆盖「工作台渲染并执行」的交互链路——
 * 加载示例问题 → 点击示例 / 提交问题 → 触发 onAsk（等价于后端 /examples + /ask）。
 */

function fakeClient(examples: string[]): ApiClient {
  return {
    examples: () => Promise.resolve({ questions: examples }),
  } as unknown as ApiClient;
}

const noop = () => {};

describe("AskView", () => {
  it("挂载后加载并渲染示例问题", async () => {
    render(
      <AskView
        client={fakeClient(["各型号净销售额", "各区域近30天销售额"])}
        asking={false}
        pendingQuestion={null}
        onConsumedPending={noop}
        onAsk={noop}
      />,
    );

    expect(await screen.findByText("各型号净销售额")).toBeTruthy();
    expect(screen.getByText("各区域近30天销售额")).toBeTruthy();
  });

  it("点击示例问题触发 onAsk", async () => {
    const onAsk = vi.fn();
    render(
      <AskView
        client={fakeClient(["各型号净销售额"])}
        asking={false}
        pendingQuestion={null}
        onConsumedPending={noop}
        onAsk={onAsk}
      />,
    );

    fireEvent.click(await screen.findByText("各型号净销售额"));
    expect(onAsk).toHaveBeenCalledWith("各型号净销售额");
  });

  it("提交时去除首尾空白并调用 onAsk", async () => {
    const onAsk = vi.fn();
    render(
      <AskView
        client={fakeClient([])}
        asking={false}
        pendingQuestion={null}
        onConsumedPending={noop}
        onAsk={onAsk}
      />,
    );
    await act(async () => {});

    fireEvent.change(screen.getByPlaceholderText(/自然语言提问/), {
      target: { value: "  各型号净销售额  " },
    });
    fireEvent.click(screen.getByRole("button", { name: "提问" }));

    expect(onAsk).toHaveBeenCalledWith("各型号净销售额");
  });

  it("空问题时提交按钮禁用", async () => {
    render(
      <AskView
        client={fakeClient([])}
        asking={false}
        pendingQuestion={null}
        onConsumedPending={noop}
        onAsk={noop}
      />,
    );
    await act(async () => {});

    expect(
      screen.getByRole("button", { name: "提问" }).hasAttribute("disabled"),
    ).toBe(true);
  });
});
