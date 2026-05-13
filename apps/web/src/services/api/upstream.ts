import "server-only";

export type UpstreamResult<T> =
  | { ok: true; data: T }
  | { ok: false; status: number; message: string; payload?: unknown };

export interface FastApiFetchOptions extends RequestInit {
  sessionToken?: string;
}

const DEFAULT_BASE_URL = "http://127.0.0.1:8000";

function getBaseUrl(): string {
  const raw =
    process.env.CLAREAD_FASTAPI_BASE_URL ??
    process.env.CLAREAD_API_BASE_URL ??
    DEFAULT_BASE_URL;

  return raw.replace(/\/+$/, "");
}

function getErrorMessage(payload: unknown, fallback: string): string {
  if (payload && typeof payload === "object" && "detail" in payload) {
    const detail = (payload as { detail?: unknown }).detail;
    return typeof detail === "string" ? detail : JSON.stringify(detail);
  }

  return fallback;
}

function parseResponsePayload(text: string): unknown {
  if (!text) {
    return null;
  }

  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
}

export async function fastApiFetch<T>(
  path: string,
  options: FastApiFetchOptions = {},
): Promise<UpstreamResult<T>> {
  const headers = new Headers(options.headers);
  headers.set("accept", "application/json");

  if (options.body && !headers.has("content-type")) {
    headers.set("content-type", "application/json");
  }

  if (options.sessionToken) {
    headers.set("authorization", `Bearer ${options.sessionToken}`);
  }

  try {
    const response = await fetch(`${getBaseUrl()}${path}`, {
      ...options,
      headers,
      cache: options.cache ?? "no-store",
    });

    const text = await response.text();
    const payload = parseResponsePayload(text);

    if (!response.ok) {
      return {
        ok: false,
        status: response.status,
        message: getErrorMessage(payload, response.statusText),
        payload,
      };
    }

    return { ok: true, data: payload as T };
  } catch (error) {
    return {
      ok: false,
      status: 0,
      message: error instanceof Error ? error.message : "FastAPI upstream request failed",
    };
  }
}
