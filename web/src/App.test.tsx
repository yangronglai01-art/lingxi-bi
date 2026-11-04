// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, render, screen } from "@testing-library/react";
import { App } from "./App";

/**
 * App 集成冒烟测试：demo 模式（未配置 OIDC 提供者）下工作台直接渲染，
 * 无需登录；等价于原 Streamlit 工作台「启动即渲染、无异常」的验收点。
 */

function okResponse(status: number, body: unknown) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("App", () => {
  it("demo 模式渲染工作台，无需登录", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(okResponse(200, { questions: [] })),
    );

    render(<App />);
    await act(async () => {});

    expect(screen.getByText(/灵犀问数/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "问数" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "历史" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "数据目录" })).toBeTruthy();
    // demo 模式无需登录，不应出现 OIDC 登录按钮
    expect(
      screen.queryByRole("button", { name: "使用企业身份登录" }),
    ).toBeNull();
  });
});
