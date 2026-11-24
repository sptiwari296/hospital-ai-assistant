// Small API client for the FastAPI backend.
// Authentication uses an httpOnly, SameSite=Strict cookie set by the server at login:
// JavaScript never sees the token, so an XSS bug cannot steal it. Every request sends the
// X-Requested-With header, which the server requires on cookie-authenticated writes (CSRF defence).

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type Role = "patient" | "staff" | "doc_admin" | "sys_admin";

export interface Me {
  user_id: string;
  username: string;
  display_name: string;
  role: Role;
  patient_id: string | null;
  assigned_patients?: { patient_id: string; full_name: string }[];
}

export interface Citation {
  document_id?: string;
  document_name?: string;
  title?: string;
  section?: string | null;
  section_title?: string | null;
  version?: string;
  effective_date?: string;
  page?: number | null;
  system?: string;
}

export interface ChatResponse {
  trace_id: string;
  session_id: string;
  answer: string;
  answer_type: string;
  citations: Citation[];
  tools_invoked: { tool: string; status: string; latency_ms: number }[];
  explanation: string;
  intent: string;
  requires_confirmation: boolean;
  confirmation_id: string | null;
  escalation: { required: boolean; to: string[]; reason: string } | null;
  latency_ms: number;
  model?: string | null;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

// Older versions kept the token in localStorage; remove any leftover so it can't be read by scripts.
if (typeof window !== "undefined") {
  try { localStorage.removeItem("hospital_token"); } catch { /* storage unavailable */ }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("X-Requested-With", "hospital-web");
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");

  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, { ...init, headers, credentials: "include" });
  } catch {
    throw new ApiError(0, "Cannot reach the server. Is the API running on port 8000?");
  }
  if (res.status === 401 && !path.startsWith("/api/auth/") && typeof window !== "undefined"
      && window.location.pathname !== "/login") {
    window.location.href = "/login";
  }
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = body?.detail;
    const message = typeof detail === "string" ? detail
      : Array.isArray(detail) ? detail.map((d) => `${d.field ?? d.loc?.join(".")}: ${d.error ?? d.msg}`).join("; ")
      : `Request failed (${res.status})`;
    throw new ApiError(res.status, message);
  }
  return body as T;
}

export async function logout() {
  await api("/api/auth/logout", { method: "POST" }).catch(() => null);
  window.location.href = "/login";
}
