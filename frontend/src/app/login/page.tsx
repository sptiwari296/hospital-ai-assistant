"use client";
import { FormEvent, useState } from "react";
import { api, Me } from "@/services/api";

const DEMO_USERS = [
  ["rahul.sharma", "Patient · knee surgery planned"],
  ["priya.nair", "Patient · MRI booked"],
  ["arjun.mehta", "Patient · cardiac, expired insurance"],
  ["nurse.anita", "Staff · orthopaedics"],
  ["admin_01", "Document administrator"],
];

export default function LoginPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      // the server sets the httpOnly session cookie; the token in the body is ignored on purpose
      const res = await api<{ user: Me }>("/api/auth/login", {
        method: "POST", body: JSON.stringify({ username, password }),
      });
      window.location.href = res.user.role === "doc_admin" ? "/admin" : "/chat";
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ maxWidth: 980, margin: "0 auto" }}>
      <div className="hero">
        <div className="eyebrow">CityCare · AI patient services assistant</div>
        <h1>Hospital answers, <i>grounded</i> in policy</h1>
        <p>A synthetic demo hospital. No real patient data. Sign in as one of the demo accounts to try it.</p>
      </div>

      <div style={{ display: "grid", gap: 32, gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))" }}>
        <form onSubmit={submit} className="panel" style={{ display: "grid", gap: 14 }}>
          <div className="panel-title">Sign in</div>
          <label className="field">Username
            <input className="input" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" required />
          </label>
          <label className="field">Password
            <input type="password" className="input" value={password} onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password" required />
          </label>
          {error && <div style={{ color: "var(--bad)", fontSize: 13 }}>{error}</div>}
          <button disabled={busy} className="btn btn-primary">{busy ? "Signing in…" : "Sign in"}</button>
        </form>

        <div>
          <div className="panel-title">Demo accounts</div>
          {DEMO_USERS.map(([u, d], n) => (
            <button key={u} type="button" className="pcard" onClick={() => setUsername(u)}
              style={{ animationDelay: `${n * 70}ms`, display: "block", width: "100%", textAlign: "left", cursor: "pointer", marginBottom: 10 }}>
              <div className="pname">{u}</div>
              <div className="pmeta">{d}</div>
            </button>
          ))}
          <div className="pmeta">Password: DEMO_PASSWORD in the backend&apos;s .env</div>
        </div>
      </div>
    </div>
  );
}
