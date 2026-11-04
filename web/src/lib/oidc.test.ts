import { describe, expect, it, vi, afterEach } from "vitest";
import {
  buildAuthorizeUrl,
  exchangeCode,
  parseCallback,
  type OIDCConfig,
} from "./oidc";

const config: OIDCConfig = {
  issuer: "http://127.0.0.1:9000",
  clientId: "lingxi",
  redirectUri: "http://127.0.0.1:5173/callback",
};

describe("oidc", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("buildAuthorizeUrl 包含 PKCE S256 参数", () => {
    const url = buildAuthorizeUrl(config, "state-1", "challenge-1", "analyst");
    const u = new URL(url);
    expect(u.origin + u.pathname).toBe("http://127.0.0.1:9000/authorize");
    expect(u.searchParams.get("response_type")).toBe("code");
    expect(u.searchParams.get("scope")).toBe("openid profile");
    expect(u.searchParams.get("code_challenge_method")).toBe("S256");
    expect(u.searchParams.get("code_challenge")).toBe("challenge-1");
    expect(u.searchParams.get("state")).toBe("state-1");
    expect(u.searchParams.get("client_id")).toBe("lingxi");
    expect(u.searchParams.get("role")).toBe("analyst");
    expect(u.searchParams.get("redirect_uri")).toBe(config.redirectUri);
  });

  it("parseCallback 解析 code 与 state", () => {
    const parsed = parseCallback("?code=abc&state=xyz");
    expect(parsed).toEqual({ code: "abc", state: "xyz" });
  });

  it("parseCallback 缺少 code 返回 null", () => {
    expect(parseCallback("?state=xyz")).toBeNull();
    expect(parseCallback("")).toBeNull();
  });

  it("exchangeCode 成功返回 TokenResponse", async () => {
    const fake = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        access_token: "tok-1",
        token_type: "Bearer",
        expires_in: 3600,
        id_token: "id-1",
      }),
    });
    vi.stubGlobal("fetch", fake);

    const res = await exchangeCode(config, "code-1", "verifier-1");
    expect(res.access_token).toBe("tok-1");
    expect(res.token_type).toBe("Bearer");

    const [url, init] = fake.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://127.0.0.1:9000/token");
    expect(init.method).toBe("POST");
    expect(init.headers).toEqual({
      "Content-Type": "application/x-www-form-urlencoded",
    });
    const body = new URLSearchParams(init.body as string);
    expect(body.get("grant_type")).toBe("authorization_code");
    expect(body.get("code")).toBe("code-1");
    expect(body.get("code_verifier")).toBe("verifier-1");
    expect(body.get("client_id")).toBe("lingxi");
  });

  it("exchangeCode 非 2xx 抛出错误", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 401, json: async () => ({}) }),
    );
    await expect(exchangeCode(config, "code-1", "v")).rejects.toThrow(
      "令牌交换失败 (HTTP 401)",
    );
  });
});
