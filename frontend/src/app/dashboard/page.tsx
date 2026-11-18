"use client";
import { useEffect, useState } from "react";
import { api } from "@/services/api";
import { useMe } from "@/hooks/useMe";

interface Appointment { appointment_id: string; display_time: string; doctor: string; department: string; type: string }
interface ToolResult { summary: string; data: { appointments?: Appointment[] }; source: string; notes: string[] }
interface Profile {
  patient_id: string; full_name: string; dob: string; blood_group: string; allergies: string[];
  phone: string; email: string; national_id: string;
  insurance: { provider: string; plan_name: string; policy_number: string; valid_to: string; status: string } | null;
}
interface Labs { latest_by_test: { test_name: string; value: number; unit: string; reference_range: string; flag: string; collected_at: string }[]; note: string }

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-lg border border-border bg-surface p-4">
      <h2 className="mb-3 font-semibold">{title}</h2>
      {children}
    </section>
  );
}

export default function Dashboard() {
  const { me, error } = useMe(["patient", "staff"]);
  const [patientId, setPatientId] = useState<string | null>(null);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [appts, setAppts] = useState<ToolResult | null>(null);
  const [labs, setLabs] = useState<Labs | null>(null);
  const [bills, setBills] = useState<ToolResult | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  useEffect(() => {
    if (!me || (me.role === "staff" && !patientId)) return;
    const q = me.role === "staff" ? `?patient_id=${patientId}` : "";
    setFailure(null);
    Promise.all([
      api<Profile>(`/api/patient/profile${q}`), api<ToolResult>(`/api/appointments${q}`),
      api<Labs>(`/api/labs${q}`), api<ToolResult>(`/api/bills${q}`).catch(() => null),
    ]).then(([p, a, l, b]) => { setProfile(p); setAppts(a); setLabs(l); setBills(b); })
      .catch((e) => setFailure(e.message));
  }, [me, patientId]);

  if (error) return <p className="text-red-600">{error}</p>;
  if (!me) return <p className="text-muted">Loading…</p>;

  return (
    <div className="space-y-4">
      {me.role === "staff" && (
        <select className="rounded border border-border bg-surface px-2 py-1 text-sm" value={patientId ?? ""}
          onChange={(e) => setPatientId(e.target.value || null)}>
          <option value="">Select an assigned patient…</option>
          {me.assigned_patients?.map((p) => <option key={p.patient_id} value={p.patient_id}>{p.patient_id} – {p.full_name}</option>)}
        </select>
      )}
      {failure && <p className="text-red-600">{failure}</p>}
      {profile && (
        <div className="grid gap-4 md:grid-cols-2">
          <Card title="Profile">
            <dl className="grid grid-cols-[8rem_1fr] gap-y-1 text-sm">
              <dt className="text-muted">Name</dt><dd>{profile.full_name} ({profile.patient_id})</dd>
              <dt className="text-muted">Date of birth</dt><dd>{profile.dob}</dd>
              <dt className="text-muted">Blood group</dt><dd>{profile.blood_group}</dd>
              <dt className="text-muted">Allergies</dt><dd>{profile.allergies.join(", ") || "none recorded"}</dd>
              <dt className="text-muted">Phone</dt><dd>{profile.phone}</dd>
              <dt className="text-muted">ID number</dt><dd>{profile.national_id}</dd>
              <dt className="text-muted">Insurance</dt>
              <dd>{profile.insurance ? `${profile.insurance.provider} ${profile.insurance.plan_name} · ${profile.insurance.policy_number} · ${profile.insurance.status} until ${profile.insurance.valid_to}` : "none"}</dd>
            </dl>
          </Card>
          <Card title="Upcoming appointments">
            {appts?.data.appointments?.length ? (
              <ul className="space-y-2 text-sm">
                {appts.data.appointments.map((a) => (
                  <li key={a.appointment_id} className="border-b border-border pb-1 last:border-0">
                    <span className="font-medium">{a.display_time}</span><br />
                    <span className="text-muted">{a.doctor} · {a.department} · {a.type}</span>
                  </li>
                ))}
              </ul>
            ) : <p className="text-sm text-muted">{appts?.summary}</p>}
            <p className="mt-3 text-xs text-muted">To reschedule, ask in the chat.</p>
          </Card>
          <Card title="Latest lab results">
            <table className="w-full text-sm [&_td]:pr-3 [&_th]:pr-3">
              <thead className="text-left text-muted"><tr><th>Test</th><th>Value</th><th>Range</th><th>Date</th></tr></thead>
              <tbody>
                {labs?.latest_by_test.map((r) => (
                  <tr key={r.test_name} className="border-t border-border">
                    <td className="py-1">{r.test_name}</td>
                    <td className={r.flag === "normal" ? "" : "font-semibold text-orange-700 dark:text-orange-300"}>
                      {r.value} {r.unit}{r.flag !== "normal" && ` (${r.flag})`}
                    </td>
                    <td className="text-muted">{r.reference_range}</td>
                    <td className="text-muted">{r.collected_at.slice(0, 10)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-2 text-xs text-muted">{labs?.note}</p>
          </Card>
          <Card title="Bills">
            <p className="whitespace-pre-wrap text-sm">{bills?.summary ?? "No bills."}</p>
          </Card>
        </div>
      )}
    </div>
  );
}
