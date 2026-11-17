"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { api, logout, Me } from "@/lib/api";

const LINKS: { href: string; label: string; roles: Me["role"][] }[] = [
  { href: "/chat", label: "Chat", roles: ["patient", "staff"] },
  { href: "/dashboard", label: "My records", roles: ["patient", "staff"] },
  { href: "/admin", label: "Documents", roles: ["doc_admin"] },
];

export default function NavBar() {
  const pathname = usePathname();
  const [me, setMe] = useState<Me | null>(null);

  useEffect(() => {
    if (pathname === "/login") {
      setMe(null);
      return;
    }
    api<Me>("/api/me").then(setMe).catch(() => setMe(null));
  }, [pathname]);

  return (
    <header className="border-b border-border bg-surface">
      <div className="mx-auto flex max-w-5xl items-center gap-6 px-4 py-3">
        <span className="font-semibold text-accent">+ Hospital Assistant</span>
        <nav className="flex gap-4 text-sm">
          {me && LINKS.filter((l) => l.roles.includes(me.role)).map((l) => (
            <Link key={l.href} href={l.href}
              className={pathname === l.href ? "font-semibold text-foreground" : "text-muted hover:text-foreground"}>
              {l.label}
            </Link>
          ))}
        </nav>
        {me && (
          <div className="ml-auto flex items-center gap-3 text-sm">
            <span className="text-muted">{me.display_name} · {me.role}</span>
            <button className="rounded border border-border px-2 py-1 hover:bg-background"
              onClick={logout}>
              Log out
            </button>
          </div>
        )}
      </div>
    </header>
  );
}
