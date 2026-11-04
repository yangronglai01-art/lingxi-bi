import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiClient, ApiError } from "./api";

/**
 * ApiClient 单测：验证前端如何向后端鉴权（Bearer 令牌）、提交问数并解析应答。
 * 对应后端 FastAPI（src/petkit/api.py + operations_api.py）。
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
  vi.restoreAllMocks();
});

describe("ApiClient", () => {
  it("请求附带 Bearer 令牌", async () => {
    const fake = vi.fn().mockResolvedValue(okResponse(200, { status: "ok" }));
    vi.stubGlobal("fetch", fake);
    const client = new ApiClient({ baseUrl: "/api", getToken: () => "tok-1" });

    await client.health();

    const [, init] = fake.mock.calls[0] as [string, RequestInit];
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer tok-1");
  });

  it("无令牌时不附带 Authorization 头", async () => {
    const fake = vi.fn().mockResolvedValue(okResponse(200, { status: "ok" }));
    vi.stubGlobal("fetch", fake);
    const client = new ApiClient({ baseUrl: "/api", getToken: () => null });

    await client.health();

    const [, init] = fake.mock.calls[0] as [string, RequestInit];
    expect(new Headers(init.headers).has("Authorization")).toBe(false);
  });

  it("ask 以 JSON 提交问题并解析应答", async () => {
    const answer = {
      status: "success",
      question: "各型号净销售额",
      sql: "SELECT 1",
      result: null,
    };
    const fake = vi.fn().mockResolvedValue(okResponse(200, answer));
    vi.stubGlobal("fetch", fake);
    const client = new ApiClient({ baseUrl: "/api", getToken: () => null });

    const res = await client.ask("各型号净销售额");

    expect(res).toEqual(answer);
    const [url, init] = fake.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/ask");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ question: "各型号净销售额" });
  });

  it("非 2xx 抛出 ApiError 并携带 detail 消息", async () => {
    const fake = vi.fn().mockResolvedValue(
      okResponse(401, { detail: "令牌无效或已过期" }),
    );
    vi.stubGlobal("fetch", fake);
    const client = new ApiClient({ baseUrl: "/api", getToken: () => null });

    const err = (await client.me().catch((e) => e)) as ApiError;

    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(401);
    expect(err.message).toBe("令牌无效或已过期");
  });
});
