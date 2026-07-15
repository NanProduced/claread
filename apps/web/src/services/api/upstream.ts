import "server-only";

export type UpstreamResult<T> =
  | { ok: true; data: T }
  | {
      ok: false;
      status: number;
      message: string;
      /**
       * Raw parsed body of the upstream response (string for non-JSON bodies,
       * parsed value for JSON bodies). Kept for legacy BFF adapters that
       * inspect the upstream error envelope as `unknown`.
       */
      payload?: unknown;
      /**
       * Parsed JSON object of the upstream error response, when the body
       * is JSON-parseable AND a plain object. Used by BFF adapters that
       * need the upstream's typed error shape (e.g. S4 candidate recovery
       * conflict resolution). Always `undefined` for the success case,
       * for non-2xx responses whose body is not a plain JSON object, and
       * for non-JSON upstream bodies.
       */
      body?: unknown;
    };

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return (
    typeof value === "object" &&
    value !== null &&
    !Array.isArray(value)
  );
}

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
        body: isPlainObject(payload) ? payload : undefined,
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
