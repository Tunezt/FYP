const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export const OWNER_TOKEN_KEY = "wp_owner_token";
export const POS_TOKEN_KEY = "wp_pos_token";
export const POS_PAIRING_KEY = "wp_pos_pairing";

export class ApiError extends Error {
  status: number;
  detail: string;
  /** bill-1: a 409 for a table that already has an open bill names that bill. */
  openBillId: string | null;

  constructor(status: number, detail: string, openBillId: string | null = null) {
    super(detail);
    this.status = status;
    this.detail = detail;
    this.openBillId = openBillId;
  }
}

export async function api<T>(
  path: string,
  opts: { method?: string; body?: unknown; token?: string | null } = {}
): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    method: opts.method ?? (opts.body !== undefined ? "POST" : "GET"),
    headers: {
      "Content-Type": "application/json",
      ...(opts.token ? { Authorization: `Bearer ${opts.token}` } : {}),
    },
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail, res.headers.get("X-Open-Bill-Id"));
  }
  return res.json() as Promise<T>;
}

export function getOwnerToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(OWNER_TOKEN_KEY);
}

export function getPosToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(POS_TOKEN_KEY);
}
