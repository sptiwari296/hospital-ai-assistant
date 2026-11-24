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
    <section className="panel">
      <div className="panel-title">{title}</div>
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

  if (error) return <p style={{ color: "var(--bad)" }}>{error}</p>;
  if (!me) return <p style={{ color: "var(--muted)" }}>Loading…</p>;

  const row = (k: string, v: React.ReactNode) => (
    <><dt style={{ color: "var(--dim)" }}>{k}</dt><dd>{v}</dd></>
  );

  return (
    <div>
      <div className="hero">
        <div className="eyebrow">CityCare · my records</div>
        <h1>Your care, <i>at a glance</i></h1>
        <p>Profile, appointments, lab results and bills, read live from the hospital systems with your permissions.</p>
      </div>

      {me.role === "staff" && (
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 16, maxWidth: 420 }}>
          <span className="side-label">Patient</span>
          <select className="input" value={patientId ?? ""} onChange={(e) => setPatientId(e.target.value || null)}>
            <option value="">Select an assigned patient…</option>
            {me.assigned_patients?.map((p) => <option key={p.patient_id} value={p.patient_id}>{p.patient_id} · {p.full_name}</option>)}
          </select>
        </div>
      )}
      {failure && <p style={{ color: "var(--bad)" }}>{failure}</p>}
      {profile && (
        <div style={{ display: "grid", gap: 16, gridTemplateColumns: "repeat(auto-fit, minmax(340px, 1fr))" }}>
          <Card title="Profile">
            <div className="pname" style={{ paddingRight: 0 }}>{profile.full_name}</div>
            <div className="pmeta">{profile.patient_id} · born {profile.dob}</div>
            <dl style={{ display: "grid", gridTemplateColumns: "8rem 1fr", rowGap: 6, fontSize: 13, marginTop: 12 }}>
              {row("Blood group", profile.blood_group)}
              {row("Allergies", profile.allergies.join(", ") || "none recorded")}
              {row("Phone", profile.phone)}
              {row("ID number", <span className="mono">{profile.national_id}</span>)}
              {row("Insurance", profile.insurance ? (
                <>
                  {profile.insurance.provider} {profile.insurance.plan_name}{" "}
                  <span className={`badge ${profile.insurance.status === "active" ? "ok" : "er"}`}>{profile.insurance.status}</span>
                  <div className="pmeta">{profile.insurance.policy_number} · until {profile.insurance.valid_to}</div>
                </>
              ) : "none")}
            </dl>
          </Card>

          <Card title="Upcoming appointments">
            {appts?.data.appointments?.length ? appts.data.appointments.map((a, n) => (
              <div key={a.appointment_id} className="pcard" style={{ animationDelay: `${n * 70}ms`, marginBottom: 10 }}>
                <div className="pname">{a.display_time}</div>
                <div className="pmeta">{a.doctor} · {a.department}</div>
                <span className="badge info">{a.type}</span>
              </div>
            )) : <p style={{ color: "var(--muted)", fontSize: 13 }}>{appts?.summary}</p>}
            <div className="pmeta" style={{ marginTop: 6 }}>To reschedule, ask in the chat.</div>
          </Card>

          <Card title="Latest lab results">
            <table className="table">
              <thead><tr><th>Test</th><th>Value</th><th>Range</th><th>Date</th></tr></thead>
              <tbody>
                {labs?.latest_by_test.map((r) => (
                  <tr key={r.test_name}>
                    <td>{r.test_name}</td>
                    <td style={r.flag === "normal" ? undefined : { color: "var(--warn)", fontWeight: 600 }}>
                      {r.value} {r.unit}{r.flag !== "normal" && ` (${r.flag})`}
                    </td>
                    <td style={{ color: "var(--muted)" }}>{r.reference_range}</td>
                    <td className="mono" style={{ color: "var(--dim)", whiteSpace: "nowrap" }}>{r.collected_at.slice(0, 10)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="pmeta" style={{ marginTop: 8 }}>{labs?.note}</div>
          </Card>

          <Card title="Bills">
            <p style={{ whiteSpace: "pre-wrap", fontSize: 13 }}>{bills?.summary ?? "No bills."}</p>
          </Card>
        </div>
      )}
    </div>
  );
}
