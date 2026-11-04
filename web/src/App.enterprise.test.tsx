// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { App } from "./App";

const answer = { status: "ok", request_id: "job-1", generation: "qa-v2", elapsed_ms: 10,
  context: { watermark: "2026-10-16" }, plan: {}, truncated: false, question: "各型号净销售额",
  sql: "SELECT dimension, net_sales FROM approved_view", result: { columns: ["dimension", "net_sales"], rows: [["产品A", "123.45"]], row_count: 1 },
  chart: null, insights: [], error: "", mock_used: false, generation_mode: "approved_template" };

afterEach(() => { cleanup(); sessionStorage.clear(); vi.unstubAllGlobals(); });

function setup(cancelled: { value: boolean }, pending = false) {
  sessionStorage.setItem("lingxi.access_token", "test-token");
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    let body: unknown = {};
    let status = 200;
    if (url.endsWith("/me")) body = { subject: "alice", role: "analyst", regions: [], managed: true };
    else if (url.endsWith("/examples")) body = { questions: [] };
    else if (url.endsWith("/queries/job-1/cancel")) { cancelled.value = true; body = { state: "cancelled" }; }
    else if (url.endsWith("/queries") && init?.method === "POST") { body = { id: "job-1" }; status = 202; }
    else if (url.endsWith("/queries/job-1")) body = cancelled.value ? { id: "job-1", state: "cancelled" } : pending ? { id: "job-1", state: "running" } : { id: "job-1", state: "completed", result: answer };
    return { ok: true, status, json: async () => body } as Response;
  });
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}

it("提交持久任务后展示执行结果，并保留Decimal字符串", async () => {
  const fetcher = setup({ value: false }); render(<App />);
  await waitFor(() => expect(screen.getByRole("textbox")).toBeTruthy());
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "各型号净销售额" } });
  fireEvent.click(screen.getByRole("button", { name: "提问" }));
  await waitFor(() => expect(screen.getByText("123.45")).toBeTruthy());
  const submit = fetcher.mock.calls.find(([url, init]) => String(url).endsWith("/queries") && init?.method === "POST");
  expect(new Headers(submit?.[1]?.headers).get("Idempotency-Key")).toBeTruthy();
  expect(fetcher.mock.calls.some(([url]) => String(url).endsWith("/ask"))).toBe(false);
});

it("取消操作发送到同一持久任务，并展示取消状态", async () => {
  const state = { value: false }; setup(state, true); render(<App />);
  await waitFor(() => expect(screen.getByRole("textbox")).toBeTruthy());
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "各型号净销售额" } });
  fireEvent.click(screen.getByRole("button", { name: "提问" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "取消查询" })).toBeTruthy());
  fireEvent.click(screen.getByRole("button", { name: "取消查询" }));
  await waitFor(() => expect(screen.getByText("cancelled")).toBeTruthy(), { timeout: 2500 });
  expect(state.value).toBe(true);
});


it("追问显式引用上一个任务，反馈发送到同一归属任务", async () => {
  const fetcher = setup({ value: false }); render(<App />);
  const input = await screen.findByRole("textbox");
  fireEvent.change(input, { target: { value: "各型号净销售额" } });
  fireEvent.click(screen.getByRole("button", { name: "提问" }));
  await screen.findByText("123.45");
  fireEvent.click(screen.getByRole("button", { name: "提交反馈" }));
  await screen.findByText("反馈已提交，待人工审核");
  expect(fetcher.mock.calls.some(([url]) => String(url).endsWith("/queries/job-1/feedback"))).toBe(true);
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.change(screen.getByLabelText("查询问题"), { target: { value: "按渠道看" } });
  fireEvent.click(screen.getByRole("button", { name: "提问" }));
  await waitFor(() => {
    const submits = fetcher.mock.calls.filter(([url, init]) => String(url).endsWith("/queries") && init?.method === "POST");
    expect(JSON.parse(String(submits.at(-1)?.[1]?.body)).previous_job_id).toBe("job-1");
  });
});
