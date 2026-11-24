"use client";
import { FormEvent, useEffect, useRef, useState } from "react";
import AnswerCard from "@/components/AnswerCard";
import ResultsPanel from "@/components/ResultsPanel";
import { useMe } from "@/hooks/useMe";
import { api, ChatResponse } from "@/services/api";

type Turn = { role: "user"; text: string } | { role: "assistant"; r: ChatResponse; secs: number };

const CHIPS = [
  "What are the ICU visiting hours?",
  "What is my latest HbA1c?",
  "Am I covered for knee replacement?",
  "Can I proceed with surgery next week?",
  "Reschedule my cardiology appointment",
  "Do you treat snake bites?",
];

export default function ChatPage() {
  const { me, error } = useMe(["patient", "staff"]);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [patientId, setPatientId] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // braces matter: an effect must not return the Promise that scroll APIs return in newer browsers
    box.current?.scrollTo({ top: box.current.scrollHeight, behavior: "smooth" });
  }, [turns, busy]);

  useEffect(() => {   // "New conversation" in the sidebar
    const reset = () => { setTurns([]); setSessionId(null); setFailure(null); };
    window.addEventListener("new-conversation", reset);
    return () => window.removeEventListener("new-conversation", reset);
  }, []);

  async function send(payload: { message?: string; decision?: "confirm" | "decline"; confirmation_id?: string },
                      shown: string) {
    setBusy(true);
    setFailure(null);
    setTurns((t) => [...t, { role: "user", text: shown }]);
    const started = performance.now();
    try {
      const r = await api<ChatResponse>("/api/chat", {
        method: "POST",
        body: JSON.stringify({ message: "", ...payload, session_id: sessionId, patient_id: patientId }),
      });
      setSessionId(r.session_id);
      setTurns((t) => [...t, { role: "assistant", r, secs: (performance.now() - started) / 1000 }]);
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

  const lastAssistant = [...turns].reverse().find((t) => t.role === "assistant") as
    { r: ChatResponse } | undefined;
  const needsPatient = me?.role === "staff" && !patientId;

  if (error) return <p style={{ color: "var(--bad)" }}>{error}</p>;
  if (!me) return <p style={{ color: "var(--muted)" }}>Loading…</p>;

  return (
    <div>
      <div className="hero">
        <div className="eyebrow">CityCare · AI patient services assistant</div>
        <h1>Answers you can <i>trust</i></h1>
        <p>Ask about hospital policies or your own records. Every answer comes from approved documents or hospital systems, with its sources shown.</p>
      </div>

      {me.role === "staff" && (
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12, maxWidth: 420 }}>
          <span className="side-label">Patient</span>
          <select className="input" value={patientId ?? ""}
            onChange={(e) => { setPatientId(e.target.value || null); setSessionId(null); setTurns([]); }}>
            <option value="">Select an assigned patient…</option>
            {me.assigned_patients?.map((p) => <option key={p.patient_id} value={p.patient_id}>{p.patient_id} · {p.full_name}</option>)}
          </select>
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1.15fr) minmax(0,1fr)", gap: 32 }} className="chat-grid">
        <div>
          <div className="chat-box" ref={box}>
            {turns.length === 0 && (
              <div className="notice">
                Ask me about the hospital or your care. For example: <i>which tests do I need before knee surgery?</i>
              </div>
            )}
            {turns.map((t, i) =>
              t.role === "user" ? (
                <div key={i} className="msg">
                  <div className="avatar user">🙂</div>
                  <div className="msg-body"><div className="text" style={{ fontWeight: 500 }}>{t.text}</div></div>
                </div>
              ) : (
                <AnswerCard key={i} r={t.r} secs={t.secs} busy={busy || t.r !== lastAssistant?.r}
                  onOption={(n) => send({ message: String(n) }, `Option ${n}`)}
                  onDecide={(d) => send({ decision: d, confirmation_id: t.r.confirmation_id ?? undefined },
                    d === "confirm" ? "Confirm change" : "Keep as is")} />
              ))}
            {busy && <div className="typing">Thinking…</div>}
            {failure && <div className="msg" style={{ borderColor: "rgba(248,113,113,.4)", color: "var(--bad)" }}>{failure}</div>}
          </div>

          <div style={{ color: "var(--muted)", fontSize: 13, margin: "12px 0 6px" }}>Try:</div>
          <div className="chips">
            {CHIPS.map((c) => (
              <button key={c} className="btn" title={c} disabled={busy || needsPatient} onClick={() => send({ message: c }, c)}>{c}</button>
            ))}
          </div>
        </div>

        <ResultsPanel r={lastAssistant?.r ?? null} />
      </div>

      <form onSubmit={submit} className="chat-input">
        <input value={input} onChange={(e) => setInput(e.target.value)} maxLength={2000} disabled={busy || needsPatient}
          placeholder={needsPatient ? "Select a patient first" : "Ask about policies, appointments, labs, insurance…"} />
        <button className="send" disabled={busy || !input.trim()} aria-label="Send">↑</button>
      </form>

      <style>{`@media (max-width: 1100px) { .chat-grid { grid-template-columns: minmax(0,1fr) !important; } }`}</style>
    </div>
  );
}
