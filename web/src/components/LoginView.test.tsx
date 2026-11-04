// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { LoginView } from "./LoginView";
import type { AppConfig } from "../config";
import { startLogin } from "../lib/oidc";

vi.mock("../lib/oidc", () => ({
  startLogin: vi.fn().mockResolvedValue(undefined),
}));

const config: AppConfig = {
  apiBase: "",
  oidcIssuer: "http://127.0.0.1:9000",
  oidcClientId: "lingxi",
  oidcRole: "analyst",
  oidcLoginHint: "local-interview",
};

describe("LoginView", () => {
  it("点击登录触发 OIDC 授权跳转", () => {
    render(<LoginView config={config} callbackError={null} />);

    fireEvent.click(screen.getByRole("button", { name: "使用企业身份登录" }));

    expect(startLogin).toHaveBeenCalledTimes(1);
    expect(startLogin).toHaveBeenCalledWith(
      expect.objectContaining({ issuer: config.oidcIssuer }),
      "analyst",
      "local-interview",
    );
  });

  it("显示回调错误", () => {
    render(
      <LoginView config={config} callbackError="OAuth 状态校验失败" />,
    );
    expect(screen.getByText("OAuth 状态校验失败")).toBeTruthy();
  });
});
