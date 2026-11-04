/**
 * 类型化的 API 客户端：封装 fetch，自动附带 Bearer 令牌并统一错误处理。
 *
 * 后端来源：src/petkit/api.py + src/petkit/operations_api.py。
 */

import type {
  Answer,
  HealthResponse,
  HistoryItem,
  Metric,
  Principal,
  SchemaResponse,
} from "../types";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export interface ApiClientOptions {
  baseUrl: string;
  /** 返回当前访问令牌（demo 模式或未登录时返回 null）。 */
  getToken: () => string | null;
}

export class ApiClient {
  constructor(private readonly opts: ApiClientOptions) {}

  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    const headers = new Headers(init?.headers);
    const token = this.opts.getToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);

    const res = await fetch(`${this.opts.baseUrl}${path}`, { ...init, headers });

    if (!res.ok) {
      let message = `HTTP ${res.status}`;
      try {
        const body = (await res.json()) as { detail?: unknown };
        if (typeof body.detail === "string") message = body.detail;
        else if (body.detail) message = JSON.stringify(body.detail);
      } catch {
        // 保持默认 message
      }
      throw new ApiError(res.status, message);
    }

    if (res.status === 204) return undefined as T;
    return (await res.json()) as T;
  }

  health(): Promise<HealthResponse> {
    return this.request<HealthResponse>("/health");
  }

  me(): Promise<Principal> {
    return this.request<Principal>("/me");
  }

  schema(q = "", coreOnly = false, offset = 0, limit = 50): Promise<SchemaResponse> {
    const params = new URLSearchParams({
      q,
      core_only: String(coreOnly),
      offset: String(offset),
      limit: String(limit),
    });
    return this.request<SchemaResponse>(`/schema?${params.toString()}`);
  }

  metrics(): Promise<{ metrics: Metric[] }> {
    return this.request<{ metrics: Metric[] }>("/metrics");
  }

  examples(): Promise<{ questions: string[] }> {
    return this.request<{ questions: string[] }>("/examples");
  }

  ask(question: string): Promise<Answer> {
    return this.request<Answer>("/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
  }

  submitQuery(question: string, key: string, previousJobId?: string): Promise<{ id: string }> {
    return this.request("/queries", { method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": key }, body: JSON.stringify({ question, ...(previousJobId ? { previous_job_id: previousJobId } : {}) }) });
  }

  feedback(id: string, vote: string, category: string, comment: string): Promise<{ id: string; state: string }> {
    return this.request(`/queries/${encodeURIComponent(id)}/feedback`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ vote, category, comment }) });
  }

  queryJob(id: string): Promise<{ id: string; state: string; error_code?: string; result?: Answer }> {
    return this.request(`/queries/${encodeURIComponent(id)}`);
  }

  cancelQuery(id: string): Promise<unknown> {
    return this.request(`/queries/${encodeURIComponent(id)}/cancel`, { method: "POST" });
  }

  history(favoriteOnly = false, limit = 100): Promise<{ items: HistoryItem[] }> {
    const params = new URLSearchParams({
      favorite_only: String(favoriteOnly),
      limit: String(limit),
    });
    return this.request<{ items: HistoryItem[] }>(`/history?${params.toString()}`);
  }

  toggleFavorite(id: number): Promise<{ id: number; favorited: boolean }> {
    return this.request<{ id: number; favorited: boolean }>(
      `/history/${id}/favorite`,
      { method: "POST" },
    );
  }

  deleteHistory(id: number): Promise<{ deleted: boolean }> {
    return this.request<{ deleted: boolean }>(`/history/${id}`, {
      method: "DELETE",
    });
  }
}
