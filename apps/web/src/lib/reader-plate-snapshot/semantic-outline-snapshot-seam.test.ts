/**
 * semantic_outline Web DTO / polling seam.
 * No L2 UI — types, consumer helpers, progressive + L0/L1 isolation only.
 */

import { describe, expect, it } from "vitest";

import {
  buildOutlineSourceIdentityKey,
  hasTrustedSemanticOutline,
  type ReaderSemanticOutlineProjectionDto,
} from "@/lib/reader-plate/projection/semantic-outline";
import { projectReaderRecordNavigation } from "@/lib/reader-plate/projection/reader-record-navigation";
import {
  applySnapshotReload,
  createInitialProgressiveState,
  listPublishedLayerKeys,
  listVisibleLayerTypes,
  makePuxSnapshot,
} from "@/lib/reader-plate-snapshot/progressive-transition";
import {
  classifyReaderEvent,
  RELIABLE_RELOAD_EVENT_TYPES,
} from "@/lib/reader-plate-snapshot/representation-event-classifier";
import type { ReaderRecordPlateDocument } from "@/lib/reader-plate/projection/reader-record-plate-document";
import type {
  ReaderEventResponseDto,
  ReaderPlateSnapshotDto,
} from "@/types/api/reader-plate";

function readyOutline(
  overrides?: Partial<ReaderSemanticOutlineProjectionDto>,
): ReaderSemanticOutlineProjectionDto {
  return {
    schema_kind: "reader_semantic_outline",
    schema_version: 1,
    status: "ready",
    source_identity: { base_id: "base_pux_1", generation: 1 },
    publication: {
      outline_revision: "olrev_ready",
      layer_id: "layer_outline_1",
      published_at: "2026-07-17T00:00:00Z",
    },
    provenance: { kind: "llm", builder: "test", model: "m" },
    nodes: [
      {
        node_id: "n1",
        parent_node_id: null,
        depth: 1,
        title: "Section One",
        start_unit_id: "unit_1",
        end_unit_id: "unit_1",
        start_anchor_segment_id: null,
        end_anchor_segment_id: null,
        order_index: 1,
      },
    ],
    diagnostics: { drops: [], skipped_node_count: 0 },
    ...overrides,
  };
}

function withOutline(
  snap: ReaderPlateSnapshotDto,
  outline: ReaderPlateSnapshotDto["semantic_outline"],
  opts?: { inventory?: boolean },
): ReaderPlateSnapshotDto {
  const next: ReaderPlateSnapshotDto = {
    ...snap,
    semantic_outline: outline,
  };
  if (opts?.inventory && outline && typeof outline === "object") {
    next.enhancement_layers = [
      ...snap.enhancement_layers,
      {
        layer_id: outline.publication.layer_id ?? "layer_outline_1",
        layer_type: "semantic_outline",
        layer_subtype: null,
        owner: "system_ai",
        base_id: snap.base.base_id,
        target_scope: "record",
        target_key: "document",
        status: "published",
        schema_version: 1,
        published_at: "2026-07-17T00:00:00Z",
        output: outline as unknown as Record<string, unknown>,
      },
    ];
  }
  return next;
}

function emptyDoc(): ReaderRecordPlateDocument {
  // Minimal plate document for L0/L1 isolation only (not full document truth).
  return {
    type: "reader_record_plate_document",
    schemaVersion: "reader-record-plate-document/v1",
    record: {
      recordId: "rec_pux_1",
      title: "t",
      generation: 1,
      productState: "readable_enhancing",
      readinessState: "article_ready",
    },
    snapshot: {
      snapshotId: "s",
      snapshotTakenAt: "2026-07-17T00:00:00Z",
      lastEventSequence: 1,
    },
    base: {
      baseId: "base_pux_1",
      contentSha256: "a".repeat(64),
      textLengthUtf16: 10,
      hashAlgorithm: "fnv1a32-utf16",
    },
    progress: {
      overallStatus: "processing",
      layers: [],
    },
    children: [
      {
        type: "paragraph",
        id: "p-unit_1",
        children: [
          {
            text: "Hello text",
            owner: "stable",
            lockSource: true,
            sourceRole: "segment_text",
            baseRange: { startUtf16: 0, endUtf16: 10 },
            marks: [],
          },
        ],
        data: {
          anchorSegmentId: "seg_1",
          coveredAnchorSegmentIds: ["seg_1"],
          sentenceId: "sent_1",
          unitId: "unit_1",
          isUnitStart: true,
          baseId: "base_pux_1",
          baseRange: { startUtf16: 0, endUtf16: 10 },
          unitRange: { startUtf16: 0, endUtf16: 10 },
          textHash: "hash",
          hashAlgorithm: "fnv1a32-utf16",
          segmentType: "sentence",
          boundaryQuality: "normal",
        },
      },
    ],
  } as ReaderRecordPlateDocument;
}

describe("semantic outline snapshot seam", () => {
  it("null and absent are both untrusted and safe", () => {
    expect(hasTrustedSemanticOutline(null)).toBe(false);
    expect(hasTrustedSemanticOutline(undefined)).toBe(false);
    const absent = makePuxSnapshot({
      snapshotId: "s1",
      lastEventSequence: 1,
      readiness: "article_ready",
      layers: [],
    });
    expect(absent.semantic_outline).toBeUndefined();
    expect(hasTrustedSemanticOutline(absent.semantic_outline)).toBe(false);
    const withNull = withOutline(absent, null);
    expect(withNull.semantic_outline).toBeNull();
    expect(hasTrustedSemanticOutline(withNull.semantic_outline)).toBe(false);
  });

  it("ready and partial are trusted; L0/L1 navigation items unchanged", () => {
    const base = makePuxSnapshot({
      snapshotId: "s2",
      lastEventSequence: 1,
      readiness: "article_ready",
      layers: ["translation"],
    });
    const ready = withOutline(base, readyOutline());
    const partial = withOutline(
      base,
      readyOutline({
        status: "partial",
        diagnostics: { drops: [], skipped_node_count: 1 },
      }),
    );
    expect(hasTrustedSemanticOutline(ready.semantic_outline)).toBe(true);
    expect(hasTrustedSemanticOutline(partial.semantic_outline)).toBe(true);

    const navNone = projectReaderRecordNavigation(base, emptyDoc());
    const navReady = projectReaderRecordNavigation(ready, emptyDoc());
    const navPartial = projectReaderRecordNavigation(partial, emptyDoc());
    expect(navReady.items).toEqual(navNone.items);
    expect(navPartial.items).toEqual(navNone.items);
    expect(navReady.sourceIdentityKey).toBe(navNone.sourceIdentityKey);
  });

  it("layer_published remains reliable full reload", () => {
    expect(RELIABLE_RELOAD_EVENT_TYPES.has("layer_published")).toBe(true);
    const event: ReaderEventResponseDto = {
      id: "ev1",
      reading_record_id: "rec_pux_1",
      sequence: 3,
      event_type: "layer_published",
      created_at: "2026-07-17T00:00:00Z",
      payload: {
        layer_type: "semantic_outline",
        layer_id: "layer_outline_1",
        generation: 1,
        base_id: "base_pux_1",
      },
    };
    const decision = classifyReaderEvent(event, {
      generation: 1,
      baseId: "base_pux_1",
    });
    expect(decision.kind).toBe("reload_snapshot");
  });

  it("same-source reload with outline accepted and cursor advances", () => {
    let state = createInitialProgressiveState();
    const s1 = makePuxSnapshot({
      snapshotId: "s4a",
      lastEventSequence: 1,
      readiness: "article_ready",
      layers: ["translation"],
    });
    const a1 = applySnapshotReload(state, s1);
    expect(a1.ok).toBe(true);
    if (!a1.ok) return;
    state = a1.state;
    expect(state.cursor).toBe(1);

    const s2 = withOutline(
      makePuxSnapshot({
        snapshotId: "s4b",
        lastEventSequence: 2,
        readiness: "article_ready",
        layers: ["translation"],
      }),
      readyOutline(),
      { inventory: true },
    );
    const a2 = applySnapshotReload(state, s2);
    expect(a2.ok).toBe(true);
    if (!a2.ok) return;
    expect(a2.state.cursor).toBe(2);
    expect(hasTrustedSemanticOutline(a2.state.snapshot?.semantic_outline)).toBe(
      true,
    );
  });

  it("rejected stale snapshot with newer outline does not swap accepted", () => {
    let state = createInitialProgressiveState();
    const accepted = withOutline(
      makePuxSnapshot({
        snapshotId: "s5_accepted",
        lastEventSequence: 5,
        readiness: "article_ready",
        layers: ["translation"],
      }),
      readyOutline({
        publication: {
          outline_revision: "old",
          layer_id: "ol_old",
          published_at: "2026-07-17T00:00:00Z",
        },
      }),
    );
    const a1 = applySnapshotReload(state, accepted);
    expect(a1.ok).toBe(true);
    if (!a1.ok) return;
    state = a1.state;

    const stale = withOutline(
      makePuxSnapshot({
        snapshotId: "s5_stale",
        lastEventSequence: 2,
        readiness: "article_ready",
        layers: ["translation"],
      }),
      readyOutline({
        publication: {
          outline_revision: "new_should_not_apply",
          layer_id: "ol_new",
          published_at: "2026-07-17T00:00:00Z",
        },
      }),
    );
    const a2 = applySnapshotReload(state, stale);
    expect(a2.ok).toBe(false);
    if (a2.ok) return;
    expect(a2.reason).toBe("stale_snapshot_sequence");
    expect(state.snapshot?.snapshot_id).toBe("s5_accepted");
    expect(state.snapshot?.semantic_outline?.publication.outline_revision).toBe(
      "old",
    );
  });

  it("outline source-identity key changes with base or generation", () => {
    expect(buildOutlineSourceIdentityKey("base_a", 1)).toBe("base_a:1");
    expect(buildOutlineSourceIdentityKey("base_a", 2)).toBe("base_a:2");
    expect(buildOutlineSourceIdentityKey("base_b", 1)).toBe("base_b:1");
    expect(buildOutlineSourceIdentityKey("base_a", 1)).not.toBe(
      buildOutlineSourceIdentityKey("base_a", 2),
    );
  });

  it("null / absent / non-trusted status objects ignored without throw", () => {
    expect(hasTrustedSemanticOutline(null)).toBe(false);
    expect(hasTrustedSemanticOutline(undefined)).toBe(false);
    expect(hasTrustedSemanticOutline(readyOutline({ status: "failed" }))).toBe(
      false,
    );
    expect(hasTrustedSemanticOutline(readyOutline({ status: "pending" }))).toBe(
      false,
    );
    expect(hasTrustedSemanticOutline(readyOutline({ status: "stale" }))).toBe(
      false,
    );
    expect(
      hasTrustedSemanticOutline(readyOutline({ status: "unavailable" })),
    ).toBe(false);
    const snap = withOutline(
      makePuxSnapshot({
        snapshotId: "s7",
        lastEventSequence: 1,
        readiness: "article_ready",
        layers: [],
      }),
      readyOutline({ status: "failed" }),
    );
    expect(() => projectReaderRecordNavigation(snap, emptyDoc())).not.toThrow();
  });

  it("same snapshot_id re-apply is accepted (duplicate guard path)", () => {
    let state = createInitialProgressiveState();
    const snap = makePuxSnapshot({
      snapshotId: "s9",
      lastEventSequence: 3,
      readiness: "article_ready",
      layers: ["translation"],
    });
    const a1 = applySnapshotReload(state, snap);
    expect(a1.ok).toBe(true);
    if (!a1.ok) return;
    state = a1.state;
    const a2 = applySnapshotReload(state, snap);
    expect(a2.ok).toBe(true);
    if (!a2.ok) return;
    expect(a2.state.cursor).toBe(3);
    expect(a2.state.lastRejected).toBe(false);
  });

  it("outline inventory appear/disappear alone does not layer-regress", () => {
    let state = createInitialProgressiveState();
    const withOutlineSnap = withOutline(
      makePuxSnapshot({
        snapshotId: "s10a",
        lastEventSequence: 2,
        readiness: "article_ready",
        layers: ["translation"],
      }),
      readyOutline(),
      { inventory: true },
    );
    const a1 = applySnapshotReload(state, withOutlineSnap);
    expect(a1.ok).toBe(true);
    if (!a1.ok) return;
    state = a1.state;
    expect(
      listPublishedLayerKeys(withOutlineSnap).some((k) =>
        k.startsWith("semantic_outline:"),
      ),
    ).toBe(false);
    expect(listVisibleLayerTypes(withOutlineSnap)).toEqual(["translation"]);

    const withoutOutline = makePuxSnapshot({
      snapshotId: "s10b",
      lastEventSequence: 3,
      readiness: "article_ready",
      layers: ["translation"],
    });
    const a2 = applySnapshotReload(state, withoutOutline);
    expect(a2.ok).toBe(true);
    if (!a2.ok) return;
    expect(a2.state.cursor).toBe(3);
  });
});
