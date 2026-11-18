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
const field = "mt-1 w-full rounded border border-border bg-background px-2 py-1.5";

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

  if (error) return <p className="text-red-600">{error}</p>;
  if (!me) return <p className="text-muted">Loading…</p>;

  return (
    <div className="space-y-6">
      <section className="rounded-lg border border-border bg-surface p-4">
        <h2 className="mb-3 font-semibold">Upload a document</h2>
        <form onSubmit={upload} className="grid gap-3 text-sm md:grid-cols-3">
          <label className="md:col-span-3">File (PDF, DOCX or TXT, max 20 MB)
            <input name="file" type="file" accept=".pdf,.docx,.txt" required className={field} /></label>
          <label className="md:col-span-2">Title<input name="title" required className={field} /></label>
          <label>Department<input name="department" required className={field} /></label>
          <label>Type<select name="document_type" className={field}>{TYPES.map((t) => <option key={t}>{t}</option>)}</select></label>
          <label>Policy lineage (same for every version)
            <input name="lineage_id" required list="lineages" placeholder="e.g. ICU_VISITING" className={field} />
            <datalist id="lineages">{[...new Set(docs.map((d) => d.lineage_id))].map((l) => <option key={l} value={l} />)}</datalist>
          </label>
          <label>Version<input name="version" required placeholder="2.1" className={field} /></label>
          <label>Effective date<input name="effective_date" type="date" required className={field} /></label>
          <label>Expiry date (optional)<input name="expiry_date" type="date" className={field} /></label>
          <label>Authority<select name="authority_level" defaultValue="3" className={field}>
            {AUTHORITY.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
          <label>Status<select name="approval_status" className={field}><option>approved</option><option>draft</option></select></label>
          <label>Document ID (optional)<input name="document_id" placeholder="auto" className={field} /></label>
          <div className="flex items-end">
            <button disabled={busy} className="rounded bg-accent px-4 py-2 font-medium text-white disabled:opacity-50">
              {busy ? "Uploading…" : "Upload & ingest"}</button>
          </div>
        </form>
        {message && <p className="mt-3 text-sm">{message}</p>}
      </section>

      <section className="rounded-lg border border-border bg-surface p-4">
        <h2 className="mb-3 font-semibold">Ingestion jobs</h2>
        <table className="w-full text-sm [&_td]:pr-3 [&_th]:pr-3 [&_td]:align-top">
          <thead className="text-left text-muted"><tr><th>Job</th><th>File</th><th>Document</th><th>Status</th><th>Chunks</th><th>Message</th></tr></thead>
          <tbody>{jobs.map((j) => (
            <tr key={j.job_id} className="border-t border-border">
              <td className="py-1">{j.job_id}</td><td>{j.file_name}</td><td>{j.document_id}</td>
              <td className={j.status === "ingested" ? "text-emerald-600" : j.status === "rejected" || j.status === "failed" ? "text-rose-600" : ""}>{j.status}</td>
              <td>{j.chunk_count ?? "–"}</td><td className="text-muted">{j.message}</td>
            </tr>))}
            {jobs.length === 0 && <tr><td colSpan={6} className="py-2 text-muted">No uploads yet.</td></tr>}
          </tbody>
        </table>
      </section>

      <section className="overflow-x-auto rounded-lg border border-border bg-surface p-4">
        <h2 className="mb-3 font-semibold">Knowledge base</h2>
        <table className="w-full text-sm [&_td]:pr-3 [&_th]:pr-3 [&_td]:align-top">
          <thead className="text-left text-muted">
            <tr><th>ID</th><th>Title</th><th>Lineage</th><th>Ver.</th><th>Effective</th><th>Status</th><th>Auth.</th><th>Chunks</th><th>Used today</th><th></th></tr>
          </thead>
          <tbody>{docs.map((d) => (
            <tr key={d.document_id} className="border-t border-border">
              <td className="py-1">{d.document_id}</td><td>{d.title}</td><td className="text-muted">{d.lineage_id}</td>
              <td>{d.version}</td><td>{d.effective_date}{d.expiry_date && ` → ${d.expiry_date}`}</td>
              <td>{d.approval_status}</td><td>{d.authority_level}</td><td>{d.chunks}</td>
              <td>{d.searchable ? "✅" : "—"}</td>
              <td className="whitespace-nowrap">
                {d.approval_status === "draft" && <button className="text-accent hover:underline" onClick={() => setStatus(d.document_id, "approved")}>Approve</button>}
                {d.approval_status === "approved" && <button className="text-rose-600 hover:underline" onClick={() => setStatus(d.document_id, "retired")}>Retire</button>}
              </td>
            </tr>))}
          </tbody>
        </table>
        <p className="mt-2 text-xs text-muted">&quot;Used today&quot; = approved, in force and the newest version of its lineage.</p>
      </section>
    </div>
  );
}
