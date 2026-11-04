import { useState } from "react";
import type { AppConfig } from "../config";
import { startLogin } from "../lib/oidc";

export function LoginView({
  config,
  callbackError,
}: {
  config: AppConfig;
  callbackError: string | null;
}) {
  const [busy, setBusy] = useState(false);

  async function login() {
    setBusy(true);
    try {
      await startLogin(
        {
          issuer: config.oidcIssuer,
          clientId: config.oidcClientId,
          redirectUri: `${window.location.origin}/callback`,
        },
        config.oidcRole,
        config.oidcLoginHint,
      );
    } finally {
      // 页面即将跳转，无需恢复状态
    }
  }

  return (
    <div className="login-view">
      <h2>欢迎使用灵犀问数</h2>
      <p className="muted">
        通过企业身份（OIDC）安全登录，以你所属角色与数据域访问数仓。
      </p>
      <button onClick={login} disabled={busy}>
        {busy ? "正在跳转…" : "使用企业身份登录"}
      </button>
      {callbackError && <div className="error-banner">{callbackError}</div>}
    </div>
  );
}
