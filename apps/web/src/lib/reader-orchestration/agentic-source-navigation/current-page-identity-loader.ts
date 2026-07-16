/**
 * Reader-owned loader: current page identity for agentic source navigation.
 *
 * Uses only:
 * - Snapshot fence fields (record / base / generation)
 * - Browser Web route GET /api/web/reader-plate/records/{id}/stable-document
 *
 * Never imports server-only BFF, never reads document text, never uses
 * envelope_fingerprint or DOM-derived identity.
 */

import type {
  CurrentPageIdentity,
  LoadCurrentPageIdentity,
  PageStableDocumentStatus,
} from "./agentic-source-navigation";

export type CurrentPageIdentityLoaderInput = {
  readingRecordId: string;
  baseId: string;
  recordGeneration: number;
  /** Injectable for tests; defaults to global fetch. */
  fetchImpl?: typeof fetch;
};

type StableDocumentRouteOk = {
  ok: true;
  reading_record_id: string;
  record_generation: number;
  active_base_id: string;
  stable_document?: {
    stable_document_id?: string;
  } | null;
};

function pageIdentity(
  input: CurrentPageIdentityLoaderInput,
  stable: {
    status: PageStableDocumentStatus;
    stableDocumentId: string | null;
  },
): CurrentPageIdentity {
  return {
    readingRecordId: input.readingRecordId,
    baseId: input.baseId,
    recordGeneration: input.recordGeneration,
    stableDocument: stable,
  };
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

/**
 * Create a LoadCurrentPageIdentity bound to one snapshot identity fence.
 *
 * Caching:
 * - Concurrent loads share one in-flight promise.
 * - Only successful ready+matched results are cached long-term.
 * - not_ready / failed / stale are not sticky — later clicks retry.
 */
export function createCurrentPageIdentityLoader(
  input: CurrentPageIdentityLoaderInput,
): LoadCurrentPageIdentity {
  const fetchImpl = input.fetchImpl ?? fetch;
  let inFlight: Promise<CurrentPageIdentity> | null = null;
  let readyCache: CurrentPageIdentity | null = null;

  async function loadOnce(): Promise<CurrentPageIdentity> {
    if (readyCache) {
      return readyCache;
    }

    const url = `/api/web/reader-plate/records/${encodeURIComponent(input.readingRecordId)}/stable-document`;

    let response: Response;
    try {
      response = await fetchImpl(url, {
        method: "GET",
        headers: { accept: "application/json" },
        credentials: "same-origin",
      });
    } catch {
      return pageIdentity(input, { status: "failed", stableDocumentId: null });
    }

    // Prefer HTTP status for not_ready before body parsing — empty bodies on
    // 404/409 must not be projected as generic failed.
    if (response.status === 404 || response.status === 409) {
      return pageIdentity(input, {
        status: "not_ready",
        stableDocumentId: null,
      });
    }

    let body: unknown;
    try {
      body = await response.json();
    } catch {
      return pageIdentity(input, { status: "failed", stableDocumentId: null });
    }

    if (!isObject(body)) {
      return pageIdentity(input, { status: "failed", stableDocumentId: null });
    }

    // Fail-closed success contract: both HTTP ok and body.ok === true required.
    // Missing/wrong-type `ok` is malformed → failed (never cached as ready).
    if (!response.ok || body.ok !== true) {
      const statusCode =
        typeof body.status === "number" ? body.status : response.status;
      if (statusCode === 404 || statusCode === 409) {
        return pageIdentity(input, {
          status: "not_ready",
          stableDocumentId: null,
        });
      }
      return pageIdentity(input, { status: "failed", stableDocumentId: null });
    }

    // Success path: fence against current snapshot identity.
    const okBody = body as Partial<StableDocumentRouteOk> & Record<string, unknown>;

    const readingRecordId =
      typeof okBody.reading_record_id === "string"
        ? okBody.reading_record_id
        : null;
    const activeBaseId =
      typeof okBody.active_base_id === "string" ? okBody.active_base_id : null;
    const recordGeneration =
      typeof okBody.record_generation === "number" &&
      Number.isInteger(okBody.record_generation)
        ? okBody.record_generation
        : null;
    const stableId =
      isObject(okBody.stable_document) &&
      typeof okBody.stable_document.stable_document_id === "string" &&
      okBody.stable_document.stable_document_id.length > 0
        ? okBody.stable_document.stable_document_id
        : null;

    if (
      readingRecordId == null ||
      activeBaseId == null ||
      recordGeneration == null
    ) {
      return pageIdentity(input, { status: "failed", stableDocumentId: null });
    }

    if (
      readingRecordId !== input.readingRecordId ||
      activeBaseId !== input.baseId ||
      recordGeneration !== input.recordGeneration
    ) {
      return pageIdentity(input, { status: "stale", stableDocumentId: null });
    }

    if (stableId == null) {
      return pageIdentity(input, {
        status: "not_ready",
        stableDocumentId: null,
      });
    }

    const ready = pageIdentity(input, {
      status: "ready",
      stableDocumentId: stableId,
    });
    readyCache = ready;
    return ready;
  }

  return async function loadCurrentPageIdentity(): Promise<CurrentPageIdentity> {
    if (readyCache) {
      return readyCache;
    }
    if (inFlight) {
      return inFlight;
    }
    inFlight = loadOnce().finally(() => {
      inFlight = null;
    });
    return inFlight;
  };
}
