// Thin typed client for the AEGIS API. The bearer token lives in memory and in sessionStorage
// (cleared when the tab closes). It is never placed in URLs.

const BASE = "/api/v1";
const KEY = "aegis.session";

export class ApiError extends Error {
  status: number;
  detail: string;
  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
}

let token: string | null = null;
try {
  token = sessionStorage.getItem(KEY);
} catch {
  token = null;
}
let onUnauthorized: (() => void) | null = null;

export function setToken(t: string | null) {
  token = t;
  try {
    if (t) sessionStorage.setItem(KEY, t);
    else sessionStorage.removeItem(KEY);
  } catch {
    /* storage unavailable: keep in memory only */
  }
}
export const getToken = () => token;
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn;
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  let res: Response;
  try {
    res = await fetch(BASE + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch {
    throw new ApiError(0, "The backend is not reachable. Check that it is running on port 8000.");
  }
  const text = await res.text();
  let data: any = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (!res.ok) {
    if (res.status === 401 && path !== "/auth/login") onUnauthorized?.();
    let detail = typeof data === "object" && data?.detail ? data.detail : `Request failed (HTTP ${res.status})`;
    if (typeof detail !== "string") detail = JSON.stringify(detail);
    if (data?.errors?.length) detail += ": " + data.errors.map((e: any) => `${(e.loc || []).slice(1).join(".")} ${e.msg}`).join("; ");
    throw new ApiError(res.status, detail);
  }
  return data as T;
}

export const api = {
  get: <T = any>(p: string) => request<T>("GET", p),
  post: <T = any>(p: string, b: unknown = {}) => request<T>("POST", p, b),
  patch: <T = any>(p: string, b: unknown) => request<T>("PATCH", p, b),
  put: <T = any>(p: string, b: unknown) => request<T>("PUT", p, b),
};

export function qs(params: Record<string, string | number | boolean | undefined | null>): string {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") u.set(k, String(v));
  const s = u.toString();
  return s ? `?${s}` : "";
}

export async function downloadReport(id: string) {
  const res = await fetch(`${BASE}/reports/${encodeURIComponent(id)}/download`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw new ApiError(res.status, "Download failed");
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `aegis-report-${id}.html`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
