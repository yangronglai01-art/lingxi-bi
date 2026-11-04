import { describe, expect, it } from "vitest";
import {
  base64UrlEncode,
  codeChallengeS256,
  generateCodeVerifier,
  generateState,
} from "./pkce";

const UNRESERVED =
  "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~";

describe("pkce", () => {
  it("generateCodeVerifier 生成指定长度且仅含未保留字符", () => {
    const v = generateCodeVerifier();
    expect(v).toHaveLength(64);
    expect(v.length).toBeGreaterThanOrEqual(43);
    expect(v.length).toBeLessThanOrEqual(128);
    for (const c of v) expect(UNRESERVED).toContain(c);
  });

  it("generateCodeVerifier 尊重自定义长度", () => {
    expect(generateCodeVerifier(48)).toHaveLength(48);
  });

  it("generateState 生成指定长度随机串", () => {
    const s = generateState();
    expect(s).toHaveLength(32);
    expect(generateState(16)).toHaveLength(16);
  });

  it("base64UrlEncode 无 padding 且替换 +/", () => {
    // "test" -> base64 "dGVzdA==" -> base64url "dGVzdA"
    const bytes = new TextEncoder().encode("test");
    expect(base64UrlEncode(bytes)).toBe("dGVzdA");
  });

  it("codeChallengeS256 匹配 RFC 7636 附录 B 示例", async () => {
    const verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk";
    const expected = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM";
    await expect(codeChallengeS256(verifier)).resolves.toBe(expected);
  });
});
