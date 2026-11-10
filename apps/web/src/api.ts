/**
 * Browser-safe relative `/api/v1` client for APEX ONE Workbench.
 * Never calls localhost/127.0.0.1 directly from the browser so Vite preview
 * proxying works seamlessly in both local and cloud preview environments.
 *
 * Includes:
 * - In-memory session fallback when third-party iframe storage (`localStorage`)
 *   is restricted by browser privacy settings.
 * - `X-Apex-Session-Token` header alongside `Authorization: Bearer` so cloud
 *   preview reverse proxies that rewrite `Authorization` never break login.
 */

export interface ApiSessionState {
  token: string | null;
  csrfToken: string | null;
  organizationId: string | null;
}

const STORAGE_KEY = "apex_one_session_v1";

let memorySession: ApiSessionState = {
  token: null,
  csrfToken: null,
  organizationId: null,
};

export function loadStoredSession(): ApiSessionState {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as ApiSessionState;
      memorySession = {
        token: parsed.token ?? null,
        csrfToken: parsed.csrfToken ?? null,
        organizationId: parsed.organizationId ?? null,
      };
      return { ...memorySession };
    }
  } catch {
    // Fallback to in-memory session inside strict sandboxed iframes
  }
  return { ...memorySession };
}

export function saveStoredSession(state: ApiSessionState): void {
  memorySession = {
    token: state.token ?? null,
    csrfToken: state.csrfToken ?? null,
    organizationId: state.organizationId ?? null,
  };
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(memorySession));
  } catch {
    // In-memory session remains active even if localStorage is blocked
  }
}

export function clearStoredSession(): void {
  memorySession = {
    token: null,
    csrfToken: null,
    organizationId: null,
  };
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    // ignore
  }
}

export class ApiRequestError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string | null;

  constructor(message: string, status: number, code = "API_ERROR", requestId: string | null = null) {
    super(message);
    this.status = status;
    this.code = code;
    this.requestId = requestId;
  }
}

export async function apiFetch<T = any>(
  path: string,
  options: RequestInit & { rawText?: boolean } = {}
): Promise<T> {
  const sess = loadStoredSession();
  const headers = new Headers(options.headers || {});
  if (!headers.has("Accept")) {
    headers.set("Accept", options.rawText ? "text/plain" : "application/json");
  }
  if (options.body && typeof options.body === "string" && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (sess.token) {
    headers.set("X-Apex-Session-Token", sess.token);
    headers.set("Authorization", `Bearer ${sess.token}`);
  }
  if (sess.organizationId) {
    headers.set("X-Organization-Id", sess.organizationId);
  }
  if (sess.csrfToken) {
    headers.set("X-CSRF-Token", sess.csrfToken);
  }

  const res = await fetch(path, {
    ...options,
    headers,
    credentials: "include",
  });
  const reqId = res.headers.get("X-Request-ID");

  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    let code = `HTTP_${res.status}`;
    try {
      const errJson = await res.json();
      msg =
        errJson?.error?.message ||
        errJson?.detail?.message ||
        JSON.stringify(errJson.detail || errJson);
      code = errJson?.error?.code || errJson?.detail?.code || code;
    } catch {
      msg = (await res.text()) || msg;
    }
    throw new ApiRequestError(msg, res.status, code, reqId);
  }

  if (options.rawText) {
    return (await res.text()) as unknown as T;
  }
  return (await res.json()) as T;
}
