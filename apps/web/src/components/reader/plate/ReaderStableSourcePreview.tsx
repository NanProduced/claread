"use client";

/**
 * Reader-owned source preview: renders StructuredSourceRenderer from real
 * stable-document blocks.
 *
 * This component is a self-contained consumer of the stable-document BFF
 * route (`GET /api/web/reader-plate/records/{id}/stable-document`). It:
 *   - Fetches blocks on mount / recordId change.
 *   - Adapts the wide BFF DTO to the G0 Structured Source contract via
 *     `adaptStableBlocksToStructuredSource` (pure, fail-safe).
 *   - Renders `StructuredSourceRenderer` when adapted blocks are non-empty.
 *   - Renders nothing (null) on loading / error / empty — the caller's
 *     existing rendering remains visible as the fallback.
 *
 * Isolation guarantees (M2 hard constraints):
 *   - Does NOT re-parse raw Markdown.
 *   - Does NOT touch the Ask panel / SSE / transport / RAG sidecar.
 *   - Does NOT replace snapshot / unit / anchor projection — it renders in a
 *     dedicated source-preview slot, adjacent to (not inside) the plate
 *     document, outline rail, AI workspace, or dictionary rail.
 *   - Does NOT modify the candidate route or BFF whitelist.
 *
 * Reference: docs/tmp/TMP-reader-markdown-rich-input-refactor-plan-2026-07-22.md §5 M2
 */

import { useEffect, useMemo, useState } from "react";

import { StructuredSourceRenderer } from "@/lib/reader-plate/projection/structured-source-renderer";
import { adaptStableBlocksToStructuredSource } from "@/lib/reader-plate/projection/stable-block-to-structured-source";
import type {
  ReaderStableDocumentBlockDto,
  ReaderStructuredSourceBlock,
} from "@/types/api/reader-plate";

export interface ReaderStableSourcePreviewProps {
  recordId: string;
  className?: string;
}

type LoadState =
  | { kind: "loading" }
  | { kind: "ready"; blocks: ReaderStructuredSourceBlock[] }
  | { kind: "empty" }
  | { kind: "error" };

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isBlockArray(value: unknown): value is ReaderStableDocumentBlockDto[] {
  if (!Array.isArray(value)) return false;
  return value.every((entry) => isObject(entry) && typeof entry.block_id === "string");
}

/**
 * Fetch stable-document blocks and adapt them to the Structured Source
 * contract. Returns `null` on any failure (network, malformed body, identity
 * mismatch) so the caller can fall back to existing rendering.
 */
async function fetchAndAdaptBlocks(
  recordId: string,
  fetchImpl: typeof fetch,
): Promise<ReaderStructuredSourceBlock[] | null> {
  const url = `/api/web/reader-plate/records/${encodeURIComponent(recordId)}/stable-document`;

  let response: Response;
  try {
    response = await fetchImpl(url, {
      method: "GET",
      headers: { accept: "application/json" },
      credentials: "same-origin",
    });
  } catch {
    return null;
  }

  // 404/409 → stable document not ready yet; treat as empty (no preview).
  if (response.status === 404 || response.status === 409) {
    return null;
  }

  if (!response.ok) {
    return null;
  }

  let body: unknown;
  try {
    body = await response.json();
  } catch {
    return null;
  }

  if (!isObject(body) || body.ok !== true) {
    return null;
  }

  // Fence: the response must be for the same record we asked about.
  if (body.reading_record_id !== recordId) {
    return null;
  }

  const rawBlocks = body.blocks;
  if (!isBlockArray(rawBlocks)) {
    return null;
  }

  return adaptStableBlocksToStructuredSource(rawBlocks);
}

export function ReaderStableSourcePreview({
  recordId,
  className,
}: ReaderStableSourcePreviewProps) {
  const [state, setState] = useState<LoadState>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    setState({ kind: "loading" });

    fetchAndAdaptBlocks(recordId, fetch)
      .then((adapted) => {
        if (cancelled) return;
        if (adapted === null) {
          setState({ kind: "error" });
          return;
        }
        if (adapted.length === 0) {
          setState({ kind: "empty" });
          return;
        }
        setState({ kind: "ready", blocks: adapted });
      })
      .catch(() => {
        if (cancelled) return;
        setState({ kind: "error" });
      });

    return () => {
      cancelled = true;
    };
  }, [recordId]);

  const blocks = useMemo(() => {
    if (state.kind !== "ready") return null;
    return state.blocks;
  }, [state]);

  // Loading / error / empty → render nothing; existing rendering remains.
  if (blocks === null) {
    return null;
  }

  return (
    <div
      data-testid="reader-stable-source-preview"
      data-reader-record-source-preview="true"
      className={className}
    >
      <StructuredSourceRenderer blocks={blocks} />
    </div>
  );
}
