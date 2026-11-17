"use client";
import { useEffect, useState } from "react";
import { api, Me } from "./api";

/** Loads the logged-in user (cookie session); the API client redirects to /login on 401. */
export function useMe(allowed?: Me["role"][]) {
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
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
