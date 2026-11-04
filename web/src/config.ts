/**
 * 前端运行时配置：优先级「后端注入的 /config.js > Vite 构建期环境变量」。
 *
 * - apiBase          后端 API 前缀（默认 /api，经 Vite 代理到 FastAPI；
 *                    生产同源部署时由 /config.js 注入空串，走相对路径）
 * - oidcIssuer       OIDC 提供者地址；为空即 demo 模式（无需登录）
 * - oidcClientId     OIDC 客户端 ID（默认 lingxi，与 LocalOIDCProvider 一致）
 * - oidcRole         本地 IdP 登录时申请的演示角色（默认 analyst）
 * - oidcLoginHint    本地 IdP 登录提示（默认 local-interview）
 *
 * 为什么需要运行时注入：本地 IdP / API 的端口在启动时才确定，而 Vite 的
 * VITE_* 变量在构建期就已 bake 进 bundle。后端在启动时输出 /config.js 设置
 * window.__LINGXI_CONFIG__，前端加载后据此覆盖构建期默认值。
 */

export interface AppConfig {
  apiBase: string;
  oidcIssuer: string;
  oidcAuthorizationEndpoint?: string;
  oidcTokenEndpoint?: string;
  oidcClientId: string;
  oidcRole: string;
  oidcLoginHint: string;
}

/** 后端 /config.js 注入的运行时配置（端口 / 签发者等运行时才确定）。 */
interface RuntimeConfig {
  apiBase?: string;
  oidcIssuer?: string;
  oidcAuthorizationEndpoint?: string;
  oidcTokenEndpoint?: string;
  oidcClientId?: string;
  oidcRole?: string;
  oidcLoginHint?: string;
}

function runtimeConfig(): RuntimeConfig {
  const w = window as unknown as { __LINGXI_CONFIG__?: RuntimeConfig };
  return w.__LINGXI_CONFIG__ ?? {};
}

export function loadConfig(): AppConfig {
  const env = import.meta.env;
  const rt = runtimeConfig();
  // 第一个非 null/undefined 的值生效（空串保留，用于「同源相对路径」）。
  const pick = (runtime?: string, build?: string, fallback = "") =>
    runtime ?? build ?? fallback;
  return {
    apiBase: pick(rt.apiBase, env.VITE_API_BASE, "/api/v1/analytics").replace(/\/+$/, ""),
    oidcIssuer: pick(rt.oidcIssuer, env.VITE_OIDC_ISSUER, "")
      .trim()
      .replace(/\/+$/, ""),
    oidcAuthorizationEndpoint: rt.oidcAuthorizationEndpoint,
    oidcTokenEndpoint: rt.oidcTokenEndpoint,
    oidcClientId: pick(rt.oidcClientId, env.VITE_OIDC_CLIENT_ID, "lingxi"),
    oidcRole: pick(rt.oidcRole, env.VITE_OIDC_ROLE, ""),
    oidcLoginHint: pick(rt.oidcLoginHint, env.VITE_OIDC_LOGIN_HINT, ""),
  };
}

/** demo 模式：未配置 OIDC 提供者，后端以 Principal()（analyst）放行。 */
export function isDemo(config: AppConfig): boolean {
  return config.oidcIssuer.length === 0;
}
