"use client";
import { ChatResponse, Citation } from "@/services/api";
import { TYPE } from "./AnswerCard";

function sourceCard(c: Citation, n: number) {
  const delay = { animationDelay: `${n * 70}ms` };
  const stampDelay = { animationDelay: `${n * 70 + 380}ms` };
  if (c.system) {
    return (
      <div key={`s-${c.system}`} className="pcard" style={delay}>
        <span className="stamp" style={stampDelay}>✓ live record</span>
        <div className="pname">{c.system}</div>
        <div className="pmeta">hospital system · read with your permissions</div>
        <div className="reason">💡 Values come straight from the system of record, not from the AI.</div>
      </div>
    );
  }
  return (
    <div key={`d-${c.document_id}-${c.section}`} className="pcard" style={delay}>
      <span className="stamp" style={stampDelay}>✓ verified</span>
      <div className="pname">{c.title ?? c.document_id}</div>
      <div className="pmeta">{c.document_id} · v{c.version} · effective {c.effective_date}{c.page ? ` · p.${c.page}` : ""}</div>
      <div className="big">§{c.section ?? "–"}{c.section_title ? ` ${c.section_title}` : ""}</div>
      <span className="badge ok">Approved</span><span className="badge ac">In force</span>
    </div>
  );
}

/** Right column: sources of the latest answer, escalation, and how the answer was made. */
export default function ResultsPanel({ r }: { r: ChatResponse | null }) {
  return (
    <div>
      <div className="panel-title">Results · live</div>
      {!r ? (
        <p style={{ color: "var(--muted)", fontSize: 13 }}>Sources, escalations and the answer trace will appear here.</p>
      ) : (
        <>
          <div className="filt">
            🔎 {r.intent || "—"} · {(TYPE[r.answer_type] ?? TYPE.not_found).label}
            {r.tools_invoked.length > 0 && ` · ${r.tools_invoked.map((t) => t.tool).join(" → ")}`}
          </div>

          {r.escalation?.required && (
            <div className="pcard" style={{ borderColor: "rgba(251,191,36,.45)" }}>
              <span className="stamp warn">⚠ escalate</span>
              <div className="pname">Clinical decision needed</div>
              <div className="pmeta">{r.escalation.to.join(" · ")}</div>
              <div className="reason">💡 {r.escalation.reason}</div>
            </div>
          )}

          {r.citations.length === 0 && (
            <p style={{ color: "var(--muted)", fontSize: 13, marginBottom: 12 }}>No sources for this answer.</p>
          )}
          {r.citations.map((c, n) => sourceCard(c, n))}

          <details className="trace">
            <summary>How this answer was made</summary>
            <div style={{ display: "grid", gap: 6, color: "var(--muted)" }}>
              <div><b style={{ color: "var(--text)" }}>Intent:</b> <span className="mono">{r.intent || "—"}</span> · <b style={{ color: "var(--text)" }}>Type:</b> <span className="mono">{r.answer_type}</span> · <b style={{ color: "var(--text)" }}>Time:</b> {r.latency_ms} ms</div>
              <div><b style={{ color: "var(--text)" }}>Steps:</b> {r.tools_invoked.length
                ? r.tools_invoked.map((t) => `${t.tool} (${t.status}, ${t.latency_ms} ms)`).join(" · ") : "none"}</div>
              <div><b style={{ color: "var(--text)" }}>Why:</b> {r.explanation}</div>
              {r.model && <div><b style={{ color: "var(--text)" }}>Model:</b> <span className="mono">{r.model}</span></div>}
              <div className="pmeta">trace {r.trace_id}</div>
            </div>
          </details>
        </>
      )}
    </div>
  );
}
