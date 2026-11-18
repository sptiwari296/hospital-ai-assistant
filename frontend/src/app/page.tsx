"use client";
import { useEffect } from "react";
import { api, Me } from "@/services/api";

export default function Home() {
  useEffect(() => {
    api<Me>("/api/me")
      .then((me) => { window.location.href = me.role === "doc_admin" ? "/admin" : "/chat"; })
      .catch(() => { window.location.href = "/login"; });
  }, []);
  return <p className="text-muted">Loading…</p>;
}
