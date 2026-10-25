/** Thin fetch wrapper: same-origin cookies, CSRF header on writes, normalised errors. */
export class ApiError extends Error {
  status: number;
  code: string;
  fields?: { field: string; message: string }[];
  constructor(status: number, code: string, message: string, fields?: { field: string; message: string }[]) {
    super(message);
    this.status = status;
    this.code = code;
    this.fields = fields;
  }
}

let csrf: string | null = null;
export const setCsrf = (t: string | null) => {
  csrf = t;
  if (typeof window !== "undefined") {
    try {
      if (t) sessionStorage.setItem("cf_csrf", t);
      else sessionStorage.removeItem("cf_csrf");
    } catch {
      // Ignore storage errors in restricted iframe/private mode
    }
  }
};

export const getCsrf = (): string | null => {
  if (csrf) return csrf;
  if (typeof window !== "undefined") {
    try {
      const stored = sessionStorage.getItem("cf_csrf");
      if (stored) {
        csrf = stored;
        return stored;
      }
    } catch {
      // Ignore storage errors
    }
  }
  return null;
};

async function parse(res: Response) {
  const ct = res.headers.get("content-type") || "";
  const body = ct.includes("json") ? await res.json().catch(() => null) : null;
  if (!res.ok) {
    const e = body?.error;
    throw new ApiError(res.status, e?.code || "http_error", e?.message || (res.status >= 500 ? "The server had a problem. Nothing was lost; please try again." : `Request failed (${res.status}).`), e?.fields);
  }
  return body;
}

export async function api<T = any>(
  path: string,
  opts: { method?: string; json?: unknown; form?: FormData; signal?: AbortSignal; _retryCsrf?: boolean } = {}
): Promise<T> {
  const method = opts.method || (opts.json !== undefined || opts.form ? "POST" : "GET");
  const headers: Record<string, string> = {};
  const activeCsrf = getCsrf();
  if (method !== "GET" && activeCsrf) headers["X-CSRF-Token"] = activeCsrf;
  let body: BodyInit | undefined;
  if (opts.json !== undefined) { headers["Content-Type"] = "application/json"; body = JSON.stringify(opts.json); }
  if (opts.form) body = opts.form;
  let res: Response;
  try {
    res = await fetch(`/api${path}`, { method, headers, body, credentials: "same-origin", signal: opts.signal, cache: "no-store" });
  } catch (e: any) {
    if (e?.name === "AbortError") throw e;
    throw new ApiError(0, "network_error", "Cannot reach the server. Check your connection; your work on this page is not lost.");
  }

  // If server returned 403 csrf_failed, transparently refresh token via /auth/me and retry once
  if (res.status === 403 && !opts._retryCsrf && path !== "/auth/me" && path !== "/auth/login" && path !== "/auth/logout" && path !== "/auth/signup") {
    try {
      const clone = res.clone();
      const errBody = await clone.json().catch(() => null);
      if (errBody?.error?.code === "csrf_failed") {
        const meRes = await fetch("/api/auth/me", { credentials: "same-origin", cache: "no-store" });
        if (meRes.ok) {
          const meData = await meRes.json();
          if (meData?.csrf_token) {
            setCsrf(meData.csrf_token);
            return await api<T>(path, { ...opts, _retryCsrf: true });
          }
        }
      }
    } catch {
      // Fall through to regular parse/error throw
    }
  }

  return parse(res);
}

export const errMessage = (e: unknown): string => (e instanceof ApiError ? e.message : e instanceof Error ? e.message : "Something went wrong.");
