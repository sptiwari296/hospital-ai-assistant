"use client";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { api } from "@/services/api";
import { useMe } from "@/hooks/useMe";

interface Doc {
  document_id: string; title: string; department: string; document_type: string; lineage_id: string;
  version: string; effective_date: string; expiry_date: string | null; approval_status: string;
  authority_level: number; chunks: number; searchable: boolean;
}
interface Job { job_id: string; document_id: string; file_name: string; status: string; message: string | null; chunk_count: number | null; created_at: string }

const TYPES = ["hospital_sop", "clinical_protocol", "nursing_procedure", "admission_policy", "discharge_guideline",
  "insurance_agreement", "emergency_procedure", "government_advisory"];
const AUTHORITY = [[1, "1 – Government / regulator"], [2, "2 – Clinical guideline"], [3, "3 – Hospital policy"], [4, "4 – Department SOP"]];

const JOB_TONE: Record<string, string> = { ingested: "ok", rejected: "er", failed: "er", queued: "mu", running: "info" };
const DOC_TONE: Record<string, string> = { approved: "ok", draft: "wa", retired: "mu" };

export default function AdminPage() {
  const { me, error } = useMe(["doc_admin"]);
  const [docs, setDocs] = useState<Doc[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    const [d, j] = await Promise.all([api<Doc[]>("/api/documents"), api<Job[]>("/api/documents/jobs")]);
    setDocs(d);
    setJobs(j);
    return j;
  }, []);

  useEffect(() => { if (me?.role === "doc_admin") refresh(); }, [me, refresh]);

  // poll while any job is still queued or running
  useEffect(() => {
    if (!jobs.some((j) => j.status === "queued" || j.status === "running")) return;
    const t = setTimeout(refresh, 1500);
    return () => clearTimeout(t);
  }, [jobs, refresh]);

  async function upload(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    setMessage(null);
    const form = new FormData(e.currentTarget);
    if (!form.get("expiry_date")) form.delete("expiry_date");
    if (!form.get("document_id")) form.delete("document_id");
    try {
      const r = await api<{ job_id: string; document_id: string }>("/api/documents/upload", { method: "POST", body: form });
      setMessage(`Upload accepted as ${r.document_id} (job ${r.job_id}). Ingestion runs in the background.`);
      (e.target as HTMLFormElement).reset();
      await refresh();
    } catch (err) {
      setMessage(`Upload failed: ${(err as Error).message}`);
    } finally {
      setBusy(false);
    }
  }

  async function setStatus(id: string, status: string) {
    try {
      await api(`/api/documents/${id}/status`, { method: "PATCH", body: JSON.stringify({ approval_status: status }) });
      await refresh();
    } catch (err) {
      setMessage((err as Error).message);
    }
  }

  if (error) return <p style={{ color: "var(--bad)" }}>{error}</p>;
  if (!me) return <p style={{ color: "var(--muted)" }}>Loading…</p>;

  return (
    <div style={{ display: "grid", gap: 20 }}>
      <div className="hero" style={{ marginBottom: 0 }}>
        <div className="eyebrow">CityCare · knowledge base</div>
        <h1>Documents the assistant <i>may</i> use</h1>
        <p>Upload, approve and retire policies. Only approved documents that are in force and the newest of their lineage are searchable.</p>
      </div>

      <section className="panel">
        <div className="panel-title">Upload a document</div>
        <form onSubmit={upload} style={{ display: "grid", gap: 12, gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))" }}>
          <label className="field" style={{ gridColumn: "1 / -1" }}>File (PDF, DOCX or TXT, max 20 MB)
            <input name="file" type="file" accept=".pdf,.docx,.txt" required className="input" /></label>
          <label className="field">Title<input name="title" required className="input" /></label>
          <label className="field">Department<input name="department" required className="input" /></label>
          <label className="field">Type<select name="document_type" className="input">{TYPES.map((t) => <option key={t}>{t}</option>)}</select></label>
          <label className="field">Policy lineage
            <input name="lineage_id" required list="lineages" placeholder="e.g. ICU_VISITING" className="input" />
            <datalist id="lineages">{[...new Set(docs.map((d) => d.lineage_id))].map((l) => <option key={l} value={l} />)}</datalist>
          </label>
          <label className="field">Version<input name="version" required placeholder="2.1" className="input" /></label>
          <label className="field">Effective date<input name="effective_date" type="date" required className="input" /></label>
          <label className="field">Expiry date (optional)<input name="expiry_date" type="date" className="input" /></label>
          <label className="field">Authority<select name="authority_level" defaultValue="3" className="input">
            {AUTHORITY.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
          <label className="field">Status<select name="approval_status" className="input"><option>approved</option><option>draft</option></select></label>
          <label className="field">Document ID (optional)<input name="document_id" placeholder="auto" className="input" /></label>
          <div style={{ display: "flex", alignItems: "flex-end" }}>
            <button disabled={busy} className="btn btn-primary" style={{ width: "100%" }}>{busy ? "Uploading…" : "Upload & ingest"}</button>
          </div>
        </form>
        {message && <div className="notice" style={{ marginTop: 12 }}>{message}</div>}
      </section>

      <section className="panel" style={{ overflowX: "auto" }}>
        <div className="panel-title">Ingestion jobs</div>
        <table className="table">
          <thead><tr><th>Job</th><th>File</th><th>Document</th><th>Status</th><th>Chunks</th><th>Message</th></tr></thead>
          <tbody>{jobs.map((j) => (
            <tr key={j.job_id}>
              <td className="mono">{j.job_id}</td><td>{j.file_name}</td><td className="mono">{j.document_id}</td>
              <td><span className={`badge ${JOB_TONE[j.status] ?? "mu"}`}>{j.status}</span></td>
              <td>{j.chunk_count ?? "–"}</td><td style={{ color: "var(--muted)" }}>{j.message}</td>
            </tr>))}
            {jobs.length === 0 && <tr><td colSpan={6} style={{ color: "var(--muted)" }}>No uploads yet.</td></tr>}
          </tbody>
        </table>
      </section>

      <section className="panel" style={{ overflowX: "auto" }}>
        <div className="panel-title">Knowledge base</div>
        <table className="table">
          <thead>
            <tr><th>ID</th><th>Title</th><th>Lineage</th><th>Ver.</th><th>Effective</th><th>Status</th><th>Auth.</th><th>Chunks</th><th>Used today</th><th></th></tr>
          </thead>
          <tbody>{docs.map((d) => (
            <tr key={d.document_id}>
              <td className="mono">{d.document_id}</td><td>{d.title}</td><td className="mono" style={{ color: "var(--dim)" }}>{d.lineage_id}</td>
              <td>{d.version}</td><td>{d.effective_date}{d.expiry_date && ` → ${d.expiry_date}`}</td>
              <td><span className={`badge ${DOC_TONE[d.approval_status] ?? "mu"}`}>{d.approval_status}</span></td>
              <td>{d.authority_level}</td><td>{d.chunks}</td>
              <td>{d.searchable ? <span className="badge ok">✓ live</span> : <span style={{ color: "var(--dim)" }}>—</span>}</td>
              <td style={{ whiteSpace: "nowrap" }}>
                {d.approval_status === "draft" && <button className="btn" onClick={() => setStatus(d.document_id, "approved")}>Approve</button>}
                {d.approval_status === "approved" && <button className="btn" style={{ color: "var(--bad)" }} onClick={() => setStatus(d.document_id, "retired")}>Retire</button>}
              </td>
            </tr>))}
          </tbody>
        </table>
        <div className="pmeta" style={{ marginTop: 8 }}>&quot;Used today&quot; = approved, in force and the newest version of its lineage.</div>
      </section>
    </div>
  );
}
