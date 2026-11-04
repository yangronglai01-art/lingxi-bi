import { useCallback, useEffect, useState } from "react";
import type { ApiClient } from "../lib/api";
import type { HistoryItem } from "../types";

export function HistoryView({
  client,
  nonce,
  onReask,
}: {
  client: ApiClient;
  nonce: number;
  onReask: (question: string) => void;
}) {
  const [items, setItems] = useState<HistoryItem[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    client
      .history(false, 100)
      .then((r) => setItems(r.items ?? []))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [client]);

  useEffect(() => {
    load();
  }, [load, nonce]);

  return (
    <div className="panel">
      <div className="section-title" style={{ marginTop: 0 }}>
        历史记录
      </div>
      {error && <div className="error-banner">{error}</div>}
      {items.length === 0 && <p className="muted">暂无历史。</p>}
      {items.map((item) => (
        <div key={item.id} className="history-item">
          <div className="q" onClick={() => onReask(item.question)}>
            {item.favorited ? "★ " : ""}
            {item.question}
          </div>
          <div className="meta">
            <span>{item.created_at}</span>
          </div>
        </div>
      ))}
    </div>
  );
}
