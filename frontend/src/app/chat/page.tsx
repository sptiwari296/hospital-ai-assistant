"use client";
import { FormEvent, useEffect, useRef, useState } from "react";
import AnswerCard from "@/components/AnswerCard";
import { api, ChatResponse } from "@/services/api";
import { useMe } from "@/hooks/useMe";

type Turn = { role: "user"; text: string } | { role: "assistant"; r: ChatResponse };

const SUGGESTIONS = [
  "What are the ICU visiting hours?",
  "What is my latest HbA1c?",
  "Am I covered for knee replacement?",
  "Can I proceed with surgery next week?",
  "Reschedule my cardiology appointment",
];

export default function ChatPage() {
  const { me, error } = useMe(["patient", "staff"]);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [patientId, setPatientId] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // braces matter: newer browsers return a Promise from scrollIntoView, and an effect must not return one
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns, busy]);

  async function send(payload: { message?: string; decision?: "confirm" | "decline"; confirmation_id?: string },
                      shown: string) {
    setBusy(true);
    setFailure(null);
    setTurns((t) => [...t, { role: "user", text: shown }]);
    try {
      const r = await api<ChatResponse>("/api/chat", {
        method: "POST",
        body: JSON.stringify({ message: "", ...payload, session_id: sessionId, patient_id: patientId }),
      });
      setSessionId(r.session_id);
      setTurns((t) => [...t, { role: "assistant", r }]);
    } catch (e) {
      setFailure((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    send({ message: text }, text);
  }

  const last = [...turns].reverse().find((t) => t.role === "assistant") as { r: ChatResponse } | undefined;

  if (error) return <p className="text-red-600">{error}</p>;
  if (!me) return <p className="text-muted">Loading…</p>;

  return (
    <div className="flex h-[calc(100vh-7rem)] flex-col">
      {me.role === "staff" && (
        <div className="mb-3 flex items-center gap-2 text-sm">
          <span className="text-muted">Patient:</span>
          <select className="rounded border border-border bg-surface px-2 py-1" value={patientId ?? ""}
            onChange={(e) => { setPatientId(e.target.value || null); setSessionId(null); setTurns([]); }}>
            <option value="">Select an assigned patient…</option>
            {me.assigned_patients?.map((p) => (
              <option key={p.patient_id} value={p.patient_id}>{p.patient_id} – {p.full_name}</option>
            ))}
          </select>
        </div>
      )}

      <div className="flex-1 space-y-4 overflow-y-auto pb-4">
        {turns.length === 0 && (
          <div className="mt-8 text-center text-muted">
            <p className="mb-4">Ask about hospital policies or your own records.</p>
            <div className="flex flex-wrap justify-center gap-2">
              {SUGGESTIONS.map((s) => (
                <button key={s} onClick={() => send({ message: s }, s)} disabled={busy}
                  className="rounded-full border border-border bg-surface px-3 py-1 text-sm hover:border-accent">{s}</button>
              ))}
            </div>
          </div>
        )}
        {turns.map((t, i) =>
          t.role === "user" ? (
            <div key={i} className="ml-auto w-fit max-w-[80%] rounded-lg bg-accent px-4 py-2 text-white">{t.text}</div>
          ) : (
            <AnswerCard key={i} r={t.r} busy={busy || t.r !== last?.r}
              onOption={(n) => send({ message: String(n) }, `Option ${n}`)}
              onDecide={(d) => send({ decision: d, confirmation_id: t.r.confirmation_id ?? undefined },
                d === "confirm" ? "Confirm change" : "Keep as is")} />
          ))}
        {busy && <p className="text-sm text-muted">Thinking…</p>}
        {failure && <p className="text-sm text-red-600">{failure}</p>}
        <div ref={bottom} />
      </div>

      <form onSubmit={submit} className="flex gap-2 border-t border-border pt-3">
        <input value={input} onChange={(e) => setInput(e.target.value)} maxLength={2000}
          placeholder="Type your question…" disabled={busy || (me.role === "staff" && !patientId)}
          className="flex-1 rounded border border-border bg-surface px-3 py-2" />
        <button disabled={busy || !input.trim()} className="rounded bg-accent px-4 py-2 font-medium text-white disabled:opacity-50">
          Send
        </button>
      </form>
    </div>
  );
}
