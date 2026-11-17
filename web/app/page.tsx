"use client";
import { useEffect } from "react";
import { api, getToken, Me } from "@/lib/api";

export default function Home() {
  useEffect(() => {
    if (!getToken()) {
      window.location.href = "/login";
      return;
    }
    api<Me>("/api/me")
      .then((me) => { window.location.href = me.role === "doc_admin" ? "/admin" : "/chat"; })
      .catch(() => { window.location.href = "/login"; });
  }, []);
  return <p className="text-muted">Loading…</p>;
}
