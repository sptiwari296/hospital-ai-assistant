"use client";
import { useEffect, useState } from "react";
import { api, getToken, Me } from "./api";

/** Loads the logged-in user; redirects to /login when there is no valid session. */
export function useMe(allowed?: Me["role"][]) {
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!getToken()) {
      window.location.href = "/login";
      return;
    }
    api<Me>("/api/me")
      .then((user) => {
        if (allowed && !allowed.includes(user.role)) setError("Your role cannot open this page.");
        setMe(user);
      })
      .catch((e) => setError(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return { me, error };
}
