/**
 * 前端类型定义，与后端 FastAPI 返回的模型一一对应。
 *
 * 后端来源：
 * - Answer            → src/engine/engine.py
 * - QueryResult       → src/engine/executor.py
 * - ChartConfig       → src/engine/chart_selector.py
 * - HistoryStore item → src/history.py
 * - Principal         → src/petkit/access.py
 */

export interface QueryResult {
  columns: string[];
  rows: unknown[][];
  row_count: number;
}

export type ChartType = "indicator" | "line" | "bar" | "pie" | "table";

export interface ChartConfig {
  chart_type: ChartType;
  title: string;
  x?: string | null;
  y: string[];
  labels: string[];
  values: number[];
  reason: string;
}

export interface Answer {
  status: string;
  request_id: string;
  generation: string;
  elapsed_ms: number;
  context: Record<string, unknown>;
  plan: Record<string, unknown>;
  truncated: boolean;
  question: string;
  sql: string;
  result: QueryResult | null;
  chart: ChartConfig | null;
  insights: string[];
  error: string;
  mock_used: boolean;
  history_id?: number;
  freshness_sla_seconds?: number | null;
}

export interface HistoryItem {
  id: number;
  question: string;
  sql: string | null;
  summary: string | null;
  columns: string[];
  sample_rows: unknown[][];
  chart_type: string;
  insights: string[];
  error: string | null;
  mock_used: boolean;
  favorited: boolean;
  created_at: string;
}

export interface Principal {
  subject: string;
  role: string;
  department: string;
  regions: string[];
  policy_version: number;
  managed: boolean;
}

export interface CatalogTable {
  name: string;
  description: string;
  domain: string;
  core: boolean;
  grain?: string;
  aliases?: string[];
  columns?: Record<string, { description?: string; type?: string }>;
  [key: string]: unknown;
}

export interface Metric {
  id: string;
  name?: string;
  aliases?: string[];
  definition?: string;
  dependencies?: string[];
  [key: string]: unknown;
}

export interface SchemaResponse {
  total: number;
  version: string;
  generation: string;
  tables: CatalogTable[];
  synthetic: boolean;
}

export interface HealthResponse {
  status: string;
  profile: string;
  mode: string;
  synthetic: boolean;
  warehouse_ready: boolean;
}
