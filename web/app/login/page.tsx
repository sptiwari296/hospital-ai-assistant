"use client";
import { FormEvent, useState } from "react";
import { api, Me } from "@/lib/api";

const DEMO_USERS = [
  ["rahul.sharma", "Patient – knee surgery planned"],
  ["priya.nair", "Patient – MRI booked"],
  ["arjun.mehta", "Patient – cardiac, expired insurance"],
  ["nurse.anita", "Staff – orthopaedics"],
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
    <div className="mx-auto mt-10 max-w-sm">
      <h1 className="mb-1 text-2xl font-semibold">Sign in</h1>
      <p className="mb-6 text-sm text-muted">Synthetic demo hospital. No real patient data.</p>
      <form onSubmit={submit} className="space-y-3 rounded-lg border border-border bg-surface p-5">
        <label className="block text-sm">Username
          <input className="mt-1 w-full rounded border border-border bg-background px-3 py-2" value={username}
            onChange={(e) => setUsername(e.target.value)} autoComplete="username" required />
        </label>
        <label className="block text-sm">Password
          <input type="password" className="mt-1 w-full rounded border border-border bg-background px-3 py-2"
            value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
        </label>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <button disabled={busy} className="w-full rounded bg-accent px-3 py-2 font-medium text-white disabled:opacity-50">
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
      <div className="mt-6 text-sm text-muted">
        <p className="mb-2">Demo accounts (password is DEMO_PASSWORD in the backend&apos;s .env):</p>
        <ul className="space-y-1">
          {DEMO_USERS.map(([u, d]) => (
            <li key={u}>
              <button className="text-accent hover:underline" onClick={() => setUsername(u)}>{u}</button> – {d}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
