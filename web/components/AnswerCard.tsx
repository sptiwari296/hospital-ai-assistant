"use client";
import { ChatResponse, Citation } from "@/lib/api";

const TYPE_STYLE: Record<string, { label: string; cls: string }> = {
  retrieved_fact: { label: "From hospital documents", cls: "bg-sky-100 text-sky-800 dark:bg-sky-900/40 dark:text-sky-200" },
  calculated: { label: "From hospital systems", cls: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-200" },
  action_completed: { label: "Action completed", cls: "bg-teal-100 text-teal-800 dark:bg-teal-900/40 dark:text-teal-200" },
  clarification_needed: { label: "Needs your input", cls: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200" },
  not_found: { label: "Not found", cls: "bg-slate-200 text-slate-700 dark:bg-slate-700/50 dark:text-slate-200" },
  refused: { label: "Not permitted", cls: "bg-rose-100 text-rose-800 dark:bg-rose-900/40 dark:text-rose-200" },
  conflict_flagged: { label: "Conflicting policies", cls: "bg-orange-100 text-orange-800 dark:bg-orange-900/40 dark:text-orange-200" },
};

function citationText(c: Citation) {
  if (c.system) return c.system;
  const section = c.section ? ` §${c.section}${c.section_title ? ` ${c.section_title}` : ""}` : "";
  return `${c.title ?? c.document_id}${section} · v${c.version} · effective ${c.effective_date}` +
    (c.page ? ` · p.${c.page}` : "");
}

export default function AnswerCard({ r, onDecide, onOption, busy }: {
  r: ChatResponse;
  onDecide: (decision: "confirm" | "decline") => void;
  onOption: (n: number) => void;
  busy: boolean;
}) {
  const style = TYPE_STYLE[r.answer_type] ?? TYPE_STYLE.not_found;
  const options = r.answer_type === "clarification_needed" && !r.requires_confirmation
    ? r.answer.split("\n").filter((l) => /^\d+\.\s/.test(l)) : [];

  return (
    <div className="rounded-lg border border-border bg-surface p-4">
      <span className={`mb-2 inline-block rounded px-2 py-0.5 text-xs font-medium ${style.cls}`}>{style.label}</span>
      <p className="whitespace-pre-wrap leading-relaxed">{r.answer}</p>

      {options.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-2">
          {options.map((line, i) => (
            <button key={line} disabled={busy} onClick={() => onOption(i + 1)}
              className="rounded border border-accent px-2 py-1 text-sm text-accent hover:bg-accent hover:text-white disabled:opacity-50">
              {line}
            </button>
          ))}
        </div>
      )}

      {r.requires_confirmation && r.confirmation_id && (
        <div className="mt-3 flex gap-2">
          <button disabled={busy} onClick={() => onDecide("confirm")}
            className="rounded bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50">Confirm change</button>
          <button disabled={busy} onClick={() => onDecide("decline")}
            className="rounded border border-border px-3 py-1.5 text-sm disabled:opacity-50">Keep as is</button>
        </div>
      )}

      {r.escalation?.required && (
        <p className="mt-3 rounded border border-amber-300 bg-amber-50 p-2 text-sm text-amber-900 dark:border-amber-700 dark:bg-amber-900/20 dark:text-amber-200">
          Escalate to {r.escalation.to.join(" and ")}: {r.escalation.reason}
        </p>
      )}

      {r.citations.length > 0 && (
        <div className="mt-3 border-t border-border pt-2 text-xs text-muted">
          <p className="mb-1 font-medium">Sources</p>
          <ul className="space-y-0.5">{r.citations.map((c, i) => <li key={i}>• {citationText(c)}</li>)}</ul>
        </div>
      )}

      <p className="mt-2 text-[11px] text-muted">
        {r.trace_id} · {r.latency_ms} ms
        {r.tools_invoked.length > 0 && ` · ${r.tools_invoked.map((t) => t.tool).join(" → ")}`}
      </p>
    </div>
  );
}
