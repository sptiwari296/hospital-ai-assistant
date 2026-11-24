"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { api, logout, Me } from "@/services/api";

const LINKS: { href: string; label: string; roles: Me["role"][] }[] = [
  { href: "/chat", label: "💬  Chat", roles: ["patient", "staff"] },
  { href: "/dashboard", label: "📋  My records", roles: ["patient", "staff"] },
  { href: "/admin", label: "📚  Documents", roles: ["doc_admin"] },
];

/** Left sidebar: who is signed in, navigation, API status and "How it works". Hidden on /login. */
export default function Sidebar() {
  const pathname = usePathname();
  const [me, setMe] = useState<Me | null>(null);
  const [online, setOnline] = useState<boolean | null>(null);

  useEffect(() => {
    if (pathname === "/login") return;
    api<Me>("/api/me").then(setMe).catch(() => setMe(null));
    api<{ status: string }>("/api/health").then((h) => setOnline(h.status === "ok")).catch(() => setOnline(false));
  }, [pathname]);

  if (pathname === "/login") return null;

  return (
    <aside className="sidebar">
      <div className="brand">✚ City<b>Care</b></div>

      {me && (
        <div className="side-user">
          <div className="side-label" style={{ marginBottom: 4 }}>Signed in as</div>
          <div style={{ fontWeight: 600 }}>{me.display_name}</div>
          <div className="pmeta">{me.username} · {me.role}{me.patient_id ? ` · ${me.patient_id}` : ""}</div>
        </div>
      )}

      <nav className="side-nav">
        {me && LINKS.filter((l) => l.roles.includes(me.role)).map((l) => (
          <Link key={l.href} href={l.href} className={pathname === l.href ? "active" : ""}>{l.label}</Link>
        ))}
      </nav>

      {pathname === "/chat" && (
        <button className="btn" onClick={() => window.dispatchEvent(new Event("new-conversation"))}>
          New conversation
        </button>
      )}

      <div style={{ fontSize: 13, color: "var(--muted)" }}>
        {online === null ? "⚪ checking API…" : online ? "🟢 API online" : "🔴 API offline"}
      </div>

      <div className="divider" />
      <div className="howto">
        <div className="side-label" style={{ marginBottom: 6 }}>How it works</div>
        <ol>
          <li><b>Understand:</b> LLM classifies the request</li>
          <li><b>Route:</b> backend checks tools &amp; permissions</li>
          <li><b>Retrieve:</b> approved documents or hospital systems</li>
          <li><b>Write:</b> grounded answer, facts only</li>
          <li><b>Check:</b> every citation and number verified</li>
        </ol>
      </div>

      {me && <button className="btn" style={{ marginTop: "auto" }} onClick={logout}>Log out</button>}
    </aside>
  );
}
