/**
 * OIDC 授权码 + PKCE 客户端。
 *
 * 与后端 src/petkit/oidc.py 的 LocalOIDCProvider 对接：
 * - GET  {issuer}/authorize → 302 携带 code
 * - POST {issuer}/token     → { access_token, token_type, expires_in, id_token }
 *
 * 生产环境可指向任意标准 OIDC 提供者（Keycloak / Auth0），接口一致。
 */

import { codeChallengeS256, generateCodeVerifier, generateState } from "./pkce";

export interface OIDCConfig {
  issuer: string;
  clientId: string;
  redirectUri: string;
  authorizationEndpoint?: string;
  tokenEndpoint?: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  id_token?: string;
}

export const VERIFIER_KEY = "lingxi.pkce.verifier";
export const STATE_KEY = "lingxi.pkce.state";
export const TOKEN_KEY = "lingxi.access_token";

/** 拼接授权端点 URL（含 PKCE S256 参数）。 */
export function buildAuthorizeUrl(
  config: OIDCConfig,
  state: string,
  challenge: string,
  role?: string,
): string {
  const params = new URLSearchParams({
    redirect_uri: config.redirectUri,
    response_type: "code",
    scope: "openid profile",
    state,
    code_challenge: challenge,
    code_challenge_method: "S256",
    client_id: config.clientId,
  });
  if (role) params.set("role", role);
  return `${config.authorizationEndpoint ?? `${config.issuer}/authorize`}?${params.toString()}`;
}

/** 生成 verifier/state 并跳转到 IdP 授权端点。 */
export async function startLogin(
  config: OIDCConfig,
  role?: string,
  loginHint?: string,
): Promise<void> {
  const verifier = generateCodeVerifier();
  const state = generateState();
  const challenge = await codeChallengeS256(verifier);
  sessionStorage.setItem(VERIFIER_KEY, verifier);
  sessionStorage.setItem(STATE_KEY, state);
  const params = new URLSearchParams({
    redirect_uri: config.redirectUri,
    response_type: "code",
    scope: "openid profile",
    state,
    code_challenge: challenge,
    code_challenge_method: "S256",
    client_id: config.clientId,
  });
  if (role) params.set("role", role);
  if (loginHint) params.set("login_hint", loginHint);
  window.location.assign(`${config.authorizationEndpoint ?? `${config.issuer}/authorize`}?${params.toString()}`);
}

/** 从回调 URL 解析 code 与 state。 */
export function parseCallback(search: string): { code: string; state: string } | null {
  const params = new URLSearchParams(search);
  const code = params.get("code");
  if (!code) return null;
  return { code, state: params.get("state") ?? "" };
}

/** 用授权码向 token 端点换取 access_token。 */
export async function exchangeCode(
  config: OIDCConfig,
  code: string,
  verifier: string,
): Promise<TokenResponse> {
  const body = new URLSearchParams({
    grant_type: "authorization_code",
    code,
    code_verifier: verifier,
    client_id: config.clientId,
    redirect_uri: config.redirectUri,
  });
  const res = await fetch(config.tokenEndpoint ?? `${config.issuer}/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: body.toString(),
  });
  if (!res.ok) {
    throw new Error(`令牌交换失败 (HTTP ${res.status})`);
  }
  return (await res.json()) as TokenResponse;
}
