export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

/** FastAPI sends `detail` as text, or as a list of {loc, msg} for a rejected body. */
export function describeDetail(detail: unknown): string | null {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const parts = detail.map((d) => {
      const msg = typeof d?.msg === "string" ? d.msg.replace(/^Value error, /, "") : null;
      const field = Array.isArray(d?.loc) ? d.loc.filter((x: unknown) => x !== "body").join(".") : "";
      return msg ? (field ? `${field}: ${msg}` : msg) : null;
    });
    const text = parts.filter(Boolean).join("; ");
    return text || null;
  }
  return null;
}

/** JSON request against the backend (same origin: the dev server proxies /api). */
export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = describeDetail(body.detail) ?? detail;
    } catch {
      // not JSON; keep the status text
    }
    throw new ApiError(response.status, detail);
  }
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}
