import { useEffect, useState } from "react";
import type { ApiClient } from "../lib/api";

export function AskView({
  client,
  asking,
  pendingQuestion,
  onConsumedPending,
  onAsk,
}: {
  client: ApiClient;
  asking: boolean;
  pendingQuestion: string | null;
  onConsumedPending: () => void;
  onAsk: (question: string) => void;
}) {
  const [question, setQuestion] = useState("");
  const [examples, setExamples] = useState<string[]>([]);

  useEffect(() => {
    client
      .examples()
      .then((r) => setExamples(r.questions ?? []))
      .catch(() => setExamples([]));
  }, [client]);

  useEffect(() => {
    if (pendingQuestion) {
      setQuestion(pendingQuestion);
      onConsumedPending();
      onAsk(pendingQuestion);
    }
  }, [pendingQuestion, onConsumedPending, onAsk]);

  function submit() {
    const q = question.trim();
    if (!q || asking) return;
    onAsk(q);
  }

  return (
    <div className="ask-area">
      <textarea
        aria-label="查询问题"
        value={question}
        onChange={(e) => setQuestion(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) submit();
        }}
        placeholder="用自然语言提问，例如：2026年9月各渠道净销售额（Ctrl+Enter 提交）"
      />
      <div className="ask-actions">
        <button onClick={submit} disabled={asking || !question.trim()}>
          {asking ? "查询中…" : "提问"}
        </button>
      </div>
      {examples.length > 0 && (
        <div className="examples">
          {examples.map((q) => (
            <span
              key={q}
              className="example-chip"
              onClick={() => {
                setQuestion(q);
                onAsk(q);
              }}
            >
              {q}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
