import { useEffect, useState } from "react";
import type { ApiClient } from "../lib/api";
import type { CatalogTable, Metric } from "../types";

export function SchemaView({ client }: { client: ApiClient }) {
  const [q, setQ] = useState("");
  const [tables, setTables] = useState<CatalogTable[]>([]);
  const [total, setTotal] = useState(0);
  const [metrics, setMetrics] = useState<Metric[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    client
      .schema(q, false, 0, 50)
      .then((r) => {
        setTables(r.tables ?? []);
        setTotal(r.total ?? 0);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [client, q]);

  useEffect(() => {
    client
      .metrics()
      .then((r) => setMetrics(r.metrics ?? []))
      .catch(() => setMetrics([]));
  }, [client]);

  return (
    <div>
      <div className="schema-search">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="搜索数据表…"
        />
      </div>
      {error && <div className="error-banner">{error}</div>}
      <p className="muted">共 {total} 张表</p>

      <div className="section-title">数据表</div>
      <div className="schema-table">
        {tables.map((t) => (
          <div key={t.name} className="history-item">
            <div className="name">
              {t.name}
              {t.core ? " · 核心" : ""}
            </div>
            <div className="desc">
              {t.domain} — {t.description}
            </div>
          </div>
        ))}
        {tables.length === 0 && <p className="muted">无匹配数据表。</p>}
      </div>

      <div className="section-title">指标（{metrics.length}）</div>
      <div className="schema-table">
        {metrics.map((m) => (
          <div key={m.id} className="history-item">
            <div className="name">{m.name ?? m.id}</div>
            <div className="desc">{m.definition ?? m.id}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
