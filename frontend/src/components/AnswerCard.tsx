"use client";
import { ChatResponse } from "@/services/api";

/** answer_type -> badge text and colour (same badge system as the shopping assistant) */
export const TYPE: Record<string, { label: string; tone: string }> = {
  retrieved_fact: { label: "Policy answer", tone: "ac" },
  calculated: { label: "From your records", tone: "ok" },
  action_completed: { label: "Done", tone: "ok" },
  clarification_needed: { label: "Question", tone: "wa" },
  not_found: { label: "Not found", tone: "mu" },
  refused: { label: "Refused", tone: "er" },
  conflict_flagged: { label: "Conflicting policies", tone: "wa" },
};

/** One assistant message in the chat box: badge + time, answer, option / confirm buttons. */
export default function AnswerCard({ r, secs, onDecide, onOption, busy }: {
  r: ChatResponse;
  secs: number;
  onDecide: (decision: "confirm" | "decline") => void;
  onOption: (n: number) => void;
  busy: boolean;
}) {
  const t = TYPE[r.answer_type] ?? TYPE.not_found;
  const lines = r.answer.split("\n");
  const options = r.answer_type === "clarification_needed" && !r.requires_confirmation
    ? lines.filter((l) => /^\d+\.\s/.test(l)) : [];
  const text = options.length ? lines.filter((l) => !/^\d+\.\s/.test(l)).join("\n") : r.answer;

  return (
    <div className="msg">
      <div className="avatar bot">🩺</div>
      <div className="msg-body">
        <div>
          <span className={`badge ${t.tone}`}>{t.label}</span>
          <span className="pmeta">{secs.toFixed(1)} s</span>
        </div>
        <div className="text">{text}</div>

        {options.length > 0 && (
          <div style={{ display: "grid", gap: 6, marginTop: 8 }}>
            {options.map((line, i) => (
              <button key={line} className="btn" style={{ textAlign: "left" }} disabled={busy} onClick={() => onOption(i + 1)}>
                {line}
              </button>
            ))}
          </div>
        )}

        {r.requires_confirmation && r.confirmation_id && (
          <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
            <button className="btn btn-primary" disabled={busy} onClick={() => onDecide("confirm")}>Confirm change</button>
            <button className="btn" disabled={busy} onClick={() => onDecide("decline")}>Keep as is</button>
          </div>
        )}

        {r.citations.length > 0 && (
          <div className="pmeta" style={{ marginTop: 6 }}>{r.citations.length} source(s) → see the results panel</div>
        )}
      </div>
    </div>
  );
}
