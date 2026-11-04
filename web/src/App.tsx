import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiClient } from "./lib/api";
import { loadConfig } from "./config";
import {
  TOKEN_KEY,
  VERIFIER_KEY,
  STATE_KEY,
  exchangeCode,
  parseCallback,
  startLogin,
} from "./lib/oidc";
import type { Answer, Principal } from "./types";
import { LoginView } from "./components/LoginView";
import { AskView } from "./components/AskView";
import { AnswerView } from "./components/AnswerView";
import { HistoryView } from "./components/HistoryView";
import { SchemaView } from "./components/SchemaView";

type Tab = "ask" | "history" | "schema";

export function App() {
  const config = useMemo(() => loadConfig(), []);
  const [token, setToken] = useState<string | null>(null);
  const [principal, setPrincipal] = useState<Principal | null>(null);
  const [tab, setTab] = useState<Tab>("ask");
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [asking, setAsking] = useState(false);
  const [askError, setAskError] = useState<string | null>(null);
  const [historyNonce, setHistoryNonce] = useState(0);
  const [callbackError, setCallbackError] = useState<string | null>(null);
  const [pendingQuestion, setPendingQuestion] = useState<string | null>(null);

  const epoch = useRef(0);
  const pendingRequest = useRef<{ question: string; key: string; parent?: string } | null>(null);
  const [activeJob, setActiveJob] = useState<string | null>(null);
  const [lastCompletedJob, setLastCompletedJob] = useState<string | null>(null);
  const [continuePrevious, setContinuePrevious] = useState(false);
  const [cancelRequested, setCancelRequested] = useState(false);
  useEffect(() => { epoch.current += 1; setAnswer(null); setActiveJob(null); setAsking(false); setLastCompletedJob(null); setContinuePrevious(false); setCancelRequested(false); return () => { epoch.current += 1; }; }, [token]);

  const client = useMemo(
    () =>
      new ApiClient({
        baseUrl: config.apiBase,
        getToken: () => token,
      }),
    [config.apiBase, token],
  );

  // 恢复已保存令牌
  useEffect(() => {
    const saved = sessionStorage.getItem(TOKEN_KEY);
    if (saved) setToken(saved);
  }, []);

  // 处理 OIDC 回调（/callback?code=...&state=...）
  useEffect(() => {
    const cb = parseCallback(window.location.search);
    if (!cb) return;

    const expectedState = sessionStorage.getItem(STATE_KEY);
    const verifier = sessionStorage.getItem(VERIFIER_KEY);
    sessionStorage.removeItem(STATE_KEY);
    sessionStorage.removeItem(VERIFIER_KEY);

    if (!verifier || !expectedState || expectedState !== cb.state) {
      setCallbackError("OAuth 状态校验失败，登录被拒绝");
      window.history.replaceState({}, "", window.location.pathname);
      return;
    }

    exchangeCode(
      {
        issuer: config.oidcIssuer,
        authorizationEndpoint: config.oidcAuthorizationEndpoint,
        tokenEndpoint: config.oidcTokenEndpoint,
        clientId: config.oidcClientId,
        redirectUri: `${window.location.origin}/callback`,
      },
      cb.code,
      verifier,
    )
      .then((resp) => {
        sessionStorage.setItem(TOKEN_KEY, resp.access_token);
        setToken(resp.access_token);
      })
      .catch((e) => setCallbackError(e instanceof Error ? e.message : String(e)))
      .finally(() => {
        window.history.replaceState({}, "", window.location.pathname);
      });
  }, [config]);

  // 拉取身份
  useEffect(() => {
    if (!token) {
      setPrincipal(null);
      return;
    }
    let valid = true;
    setPrincipal(null);
    client.me().then((p) => { if (valid) setPrincipal(p); }).catch(() => { if (valid) { setPrincipal(null); setAnswer(null); } });
    return () => { valid = false; };
  }, [token, client]);

  const handleAsk = useCallback(
    async (question: string) => {
      const current = ++epoch.current;
      setAsking(true);
      setCancelRequested(false);
      setAskError(null);
      setAnswer(null);
      try {
        const parent = continuePrevious ? lastCompletedJob ?? undefined : undefined;
        if (pendingRequest.current?.question !== question || pendingRequest.current?.parent !== parent) pendingRequest.current = { question, key: crypto.randomUUID(), parent };
        const { id } = await client.submitQuery(question, pendingRequest.current.key, continuePrevious ? lastCompletedJob ?? undefined : undefined);
        pendingRequest.current = null;
        if (current !== epoch.current) return;
        setActiveJob(id);
        for (let attempts = 0; attempts < 600; attempts += 1) {
          const job = await client.queryJob(id);
          if (current !== epoch.current) return;
          if (job.state === "completed") { setAnswer(job.result ?? null); setLastCompletedJob(id); break; }
          if (["failed", "cancelled", "interrupted"].includes(job.state)) throw new Error(job.error_code ?? job.state);
          if (attempts === 599) throw new Error("任务仍在后台运行，请在历史记录中查看");
          await new Promise((resolve) => setTimeout(resolve, 1000));
        }
        setHistoryNonce((n) => n + 1);
      } catch (e) {
        if (current === epoch.current) setAskError(e instanceof Error ? e.message : String(e));
      } finally {
        if (current === epoch.current) { setAsking(false); setActiveJob(null); }
      }
    },
    [client, continuePrevious, lastCompletedJob],
  );

  const handleReask = useCallback((q: string) => {
    setTab("ask");
    setPendingQuestion(q);
  }, []);

  const handleLogin = useCallback(() => {
    setCallbackError(null);
    startLogin(
      {
        issuer: config.oidcIssuer,
        authorizationEndpoint: config.oidcAuthorizationEndpoint,
        tokenEndpoint: config.oidcTokenEndpoint,
        clientId: config.oidcClientId,
        redirectUri: `${window.location.origin}/callback`,
      },
      config.oidcRole,
      config.oidcLoginHint,
    );
  }, [config]);

  const handleLogout = useCallback(() => {
    epoch.current += 1;
    sessionStorage.removeItem(TOKEN_KEY);
    sessionStorage.removeItem(VERIFIER_KEY);
    sessionStorage.removeItem(STATE_KEY);
    setToken(null);
    setPrincipal(null);
    setAnswer(null);
  }, []);

  const needsLogin = config.oidcIssuer !== "" && token === null;

  return (
    <div className="app-shell">
      {activeJob && <div role="status">任务 {activeJob} {cancelRequested ? "取消请求已受理，等待任务状态更新" : "正在处理"} <button disabled={cancelRequested} onClick={() => client.cancelQuery(activeJob).then(() => setCancelRequested(true)).catch((e) => setAskError(String(e)))}>取消查询</button></div>}
      {lastCompletedJob && !asking && <label><input type="checkbox" checked={continuePrevious} onChange={(e) => setContinuePrevious(e.target.checked)} />继续上一问题（仅切换已批准分组或年月）</label>}
      <header className="topbar">
        <span className="brand">灵犀问数 · Lingxi</span>
        <div className="topbar-actions">
          {principal && (
            <span className="badge role">
              {principal.role}
              {principal.managed ? " · 已认证" : ""}
            </span>
          )}
          {principal && (
            <span className="badge">{principal.department || "未分配部门"}</span>
          )}
          {needsLogin ? (
            <button onClick={handleLogin}>登录</button>
          ) : (
            token && (
              <button className="secondary" onClick={handleLogout}>
                退出登录
              </button>
            )
          )}
        </div>
      </header>

      {needsLogin ? (
        <LoginView config={config} callbackError={callbackError} />
      ) : (
        <main className="main">
          <aside className="sidebar">
            <nav className="panel">
              <button
                className={tab === "ask" ? "" : "secondary"}
                onClick={() => setTab("ask")}
              >
                问数
              </button>{" "}
              <button
                className={tab === "history" ? "" : "secondary"}
                onClick={() => setTab("history")}
              >
                历史
              </button>{" "}
              <button
                className={tab === "schema" ? "" : "secondary"}
                onClick={() => setTab("schema")}
              >
                数据目录
              </button>
            </nav>
          </aside>

          <section className="panel">
            {tab === "ask" && (
              <>
                <AskView
                  client={client}
                  asking={asking}
                  pendingQuestion={pendingQuestion}
                  onConsumedPending={() => setPendingQuestion(null)}
                  onAsk={handleAsk}
                />
                {askError && <div className="error-banner">{askError}</div>}
                {answer && <AnswerView key={answer.request_id} answer={answer} onFeedback={lastCompletedJob ? async (vote, category, comment) => { await client.feedback(lastCompletedJob, vote, category, comment); } : undefined} />}
              </>
            )}
            {tab === "history" && (
              <HistoryView
                client={client}
                nonce={historyNonce}
                onReask={handleReask}
              />
            )}
            {tab === "schema" && <SchemaView client={client} />}
          </section>
        </main>
      )}
    </div>
  );
}
