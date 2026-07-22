import { describe, expect, it } from "vitest";

import {
  projectReaderRecordNavigation,
} from "@/lib/reader-plate/projection/reader-record-navigation";
import {
  projectReaderSemanticOutlineNav,
} from "@/lib/reader-plate/projection/semantic-outline-nav";
import type { ReaderSemanticOutlineProjectionDto } from "@/lib/reader-plate/projection/semantic-outline";
import {
  READER_RECORD_PLATE_DOCUMENT_SCHEMA_VERSION,
  type ReaderRecordPlateDocument,
} from "@/lib/reader-plate/projection/reader-record-plate-document";
import {
  READER_PLATE_SNAPSHOT_SCHEMA_KIND,
  READER_TEXT_RANGE_HASH_ALGORITHM,
  type ReaderPlateSnapshotDto,
  type ReaderUnitType,
} from "@/types/api/reader-plate";

type UnitIn = {
  unit_id: string;
  order_index: number;
  unit_type?: ReaderUnitType;
  label?: string | null;
};

function makeParagraph(
  unitId: string,
  text: string,
): ReaderRecordPlateDocument["children"][number] {
  return {
    type: "paragraph",
    id: `p-${unitId}`,
    children: [
      {
        text,
        owner: "stable",
        lockSource: true,
        sourceRole: "segment_text",
        baseRange: { startUtf16: 0, endUtf16: text.length },
        marks: [],
      },
    ],
    data: {
      anchorSegmentId: `seg_${unitId}`,
      coveredAnchorSegmentIds: [`seg_${unitId}`],
      sentenceId: `sent_${unitId}`,
      unitId,
      isUnitStart: true,
      baseId: "base_1",
      baseRange: { startUtf16: 0, endUtf16: text.length },
      unitRange: { startUtf16: 0, endUtf16: text.length },
      textHash: "hash",
      hashAlgorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
      segmentType: "sentence",
      boundaryQuality: "normal",
    },
  };
}

function makeDoc(unitIds: string[]): ReaderRecordPlateDocument {
  return {
    type: "reader_record_plate_document",
    schemaVersion: READER_RECORD_PLATE_DOCUMENT_SCHEMA_VERSION,
    record: {
      recordId: "record_1",
      title: "T",
      generation: 1,
      productState: "readable_enhancing",
      readinessState: "article_ready",
    },
    snapshot: {
      snapshotId: "s1",
      snapshotTakenAt: "2026-07-17T00:00:00Z",
      lastEventSequence: 1,
    },
    base: {
      baseId: "base_1",
      contentSha256: "a".repeat(64),
      textLengthUtf16: 10,
      hashAlgorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
    },
    progress: { overallStatus: "ready", layers: [] },
    children: unitIds.map((id) => makeParagraph(id, `Text ${id}`)),
  };
}

function makeSnapshot(
  units: UnitIn[],
  outline?: ReaderPlateSnapshotDto["semantic_outline"],
  opts?: { baseId?: string; generation?: number },
): ReaderPlateSnapshotDto {
  return {
    schema_kind: READER_PLATE_SNAPSHOT_SCHEMA_KIND,
    snapshot_id: "snap_1",
    snapshot_taken_at: "2026-07-17T00:00:00Z",
    last_event_sequence: 1,
    record_id: "record_1",
    record: {
      title: "Title",
      display_title_zh: null,
      title_generation_status: "succeeded",
      title_generation_error_code: null,
      title_generation_error_message: null,
      reading_goal: "daily_reading",
      reading_variant: "beginner_reading",
      created_at: "2026-07-17T00:00:00Z",
      source_type: "text",
      source_metadata: {},
      generation: opts?.generation ?? 1,
      product_state: "readable_enhancing",
      readiness_state: "article_ready",
    },
    base: {
      base_id: opts?.baseId ?? "base_1",
      content_sha256: "sha",
      canonicalizer_version: "v1",
      builder_version: "v1",
      segmenter_version: "v1",
      text_length_utf16: 100,
      hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
    },
    navigation: {
      units: units.map((u) => ({
        unit_id: u.unit_id,
        order_index: u.order_index,
        label: u.label ?? null,
        unit_type: u.unit_type ?? "body",
        boundary_quality: "normal" as const,
        base_start_utf16: 0,
        base_end_utf16: 10,
        text_hash: "hash",
        hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
      })),
    },
    anchor_segments: [],
    enhancement_layers: [],
    enhancement_progress: { overall_status: "ready", layers: [] },
    ask_supplements: [],
    user_assets: [],
    parsed_decisions: [],
    value: [],
    semantic_outline: outline,
  };
}

function readyOutline(
  overrides?: Partial<ReaderSemanticOutlineProjectionDto>,
  nodes?: ReaderSemanticOutlineProjectionDto["nodes"],
): ReaderSemanticOutlineProjectionDto {
  return {
    schema_kind: "reader_semantic_outline",
    schema_version: 1,
    status: "ready",
    source_identity: { base_id: "base_1", generation: 1 },
    publication: {
      outline_revision: "rev_1",
      layer_id: "layer_ol_1",
      published_at: "2026-07-17T00:00:00Z",
    },
    provenance: { kind: "llm", builder: "test", model: "m" },
    nodes: nodes ?? [
      {
        node_id: "n1",
        parent_node_id: null,
        depth: 1,
        title: "Root A",
        start_unit_id: "u1",
        end_unit_id: "u3",
        start_anchor_segment_id: "seg_u1",
        end_anchor_segment_id: null,
        order_index: 1,
      },
      {
        node_id: "n2",
        parent_node_id: "n1",
        depth: 2,
        title: "Child",
        start_unit_id: "u2",
        end_unit_id: "u2",
        start_anchor_segment_id: null,
        end_anchor_segment_id: null,
        order_index: 2,
      },
      {
        node_id: "n3",
        parent_node_id: null,
        depth: 1,
        title: "Root B",
        start_unit_id: "u4",
        end_unit_id: "u4",
        start_anchor_segment_id: null,
        end_anchor_segment_id: null,
        order_index: 3,
      },
    ],
    diagnostics: { drops: [], skipped_node_count: 0 },
    ...overrides,
  };
}

const bodyUnits: UnitIn[] = [
  { unit_id: "u1", order_index: 1 },
  { unit_id: "u2", order_index: 2 },
  { unit_id: "u3", order_index: 3 },
  { unit_id: "u4", order_index: 4 },
];

const l1Units: UnitIn[] = [
  { unit_id: "u1", order_index: 1, unit_type: "body" },
  { unit_id: "u2", order_index: 2, unit_type: "heading", label: "H1" },
  { unit_id: "u3", order_index: 3, unit_type: "body" },
  { unit_id: "u4", order_index: 4, unit_type: "body" },
  { unit_id: "u5", order_index: 5, unit_type: "heading", label: "H2" },
  { unit_id: "u6", order_index: 6, unit_type: "body" },
];

describe("projectReaderSemanticOutlineNav (gate A)", () => {
  it("ready outline is available with depth=1 ticks only", () => {
    const snap = makeSnapshot(bodyUnits, readyOutline());
    const doc = makeDoc(["u1", "u2", "u3", "u4"]);
    const proj = projectReaderSemanticOutlineNav(snap, doc);
    expect(proj.available).toBe(true);
    expect(proj.status).toBe("ready");
    expect(proj.isPartial).toBe(false);
    expect(proj.panelItems.map((i) => i.nodeId)).toEqual(["n1", "n2", "n3"]);
    expect(proj.tickItems.map((i) => i.nodeId)).toEqual(["n1", "n3"]);
    expect(proj.tickItems.every((i) => i.depth === 1)).toBe(true);
  });

  it("partial is available with quiet partial flag", () => {
    const snap = makeSnapshot(
      bodyUnits,
      readyOutline({
        status: "partial",
        diagnostics: {
          drops: [{ node_id: "bad", reason_code: "empty_title" }],
          skipped_node_count: 1,
        },
      }),
    );
    const proj = projectReaderSemanticOutlineNav(
      snap,
      makeDoc(["u1", "u2", "u3", "u4"]),
    );
    expect(proj.available).toBe(true);
    expect(proj.isPartial).toBe(true);
  });

  it.each([
    ["null", null],
    ["absent", undefined],
    ["pending", readyOutline({ status: "pending" })],
    ["failed", readyOutline({ status: "failed" })],
    ["stale", readyOutline({ status: "stale" })],
    ["unavailable", readyOutline({ status: "unavailable" })],
  ] as const)("%s → unavailable", (_label, outline) => {
    const snap = makeSnapshot(bodyUnits, outline as never);
    const proj = projectReaderSemanticOutlineNav(
      snap,
      makeDoc(["u1", "u2", "u3", "u4"]),
    );
    expect(proj.available).toBe(false);
    expect(proj.panelItems).toEqual([]);
    expect(proj.tickItems).toEqual([]);
  });

  it("bad type does not throw", () => {
    const snap = makeSnapshot(bodyUnits);
    (snap as { semantic_outline: unknown }).semantic_outline = "not-an-object";
    expect(() =>
      projectReaderSemanticOutlineNav(snap, makeDoc(["u1", "u2", "u3", "u4"])),
    ).not.toThrow();
    expect(
      projectReaderSemanticOutlineNav(snap, makeDoc(["u1", "u2", "u3", "u4"]))
        .available,
    ).toBe(false);
  });

  it("empty nodes fail-closed", () => {
    const snap = makeSnapshot(bodyUnits, readyOutline({}, []));
    expect(
      projectReaderSemanticOutlineNav(snap, makeDoc(["u1", "u2", "u3", "u4"]))
        .available,
    ).toBe(false);
  });

  it("source identity mismatch fail-closed", () => {
    const snap = makeSnapshot(
      bodyUnits,
      readyOutline({
        source_identity: { base_id: "base_other", generation: 1 },
      }),
    );
    expect(
      projectReaderSemanticOutlineNav(snap, makeDoc(["u1", "u2", "u3", "u4"]))
        .available,
    ).toBe(false);
  });

  it("generation mismatch fail-closed", () => {
    const snap = makeSnapshot(bodyUnits, readyOutline(), { generation: 2 });
    expect(
      projectReaderSemanticOutlineNav(snap, makeDoc(["u1", "u2", "u3", "u4"]))
        .available,
    ).toBe(false);
  });

  it("start_unit_id missing from universe fail-closed", () => {
    const snap = makeSnapshot(
      bodyUnits,
      readyOutline({}, [
        {
          node_id: "n1",
          parent_node_id: null,
          depth: 1,
          title: "Missing",
          start_unit_id: "u_missing",
          end_unit_id: "u1",
          start_anchor_segment_id: null,
          end_anchor_segment_id: null,
          order_index: 1,
        },
      ]),
    );
    expect(
      projectReaderSemanticOutlineNav(snap, makeDoc(["u1", "u2", "u3", "u4"]))
        .available,
    ).toBe(false);
  });

  it("missing end_unit_id fail-closed", () => {
    const snap = makeSnapshot(
      bodyUnits,
      readyOutline({}, [
        {
          node_id: "n1",
          parent_node_id: null,
          depth: 1,
          title: "No end",
          start_unit_id: "u1",
          end_unit_id: null as unknown as string,
          start_anchor_segment_id: null,
          end_anchor_segment_id: null,
          order_index: 1,
        },
      ]),
    );
    const proj = projectReaderSemanticOutlineNav(
      snap,
      makeDoc(["u1", "u2", "u3", "u4"]),
    );
    expect(proj.available).toBe(false);
    expect(proj.panelItems).toEqual([]);
    expect(proj.tickItems).toEqual([]);
  });

  it("unknown end_unit_id fail-closed", () => {
    const snap = makeSnapshot(
      bodyUnits,
      readyOutline({}, [
        {
          node_id: "n1",
          parent_node_id: null,
          depth: 1,
          title: "Unknown end",
          start_unit_id: "u1",
          end_unit_id: "u_missing",
          start_anchor_segment_id: null,
          end_anchor_segment_id: null,
          order_index: 1,
        },
      ]),
    );
    expect(
      projectReaderSemanticOutlineNav(snap, makeDoc(["u1", "u2", "u3", "u4"]))
        .available,
    ).toBe(false);
  });

  it("reversed range startOrder > endOrder fail-closed", () => {
    const snap = makeSnapshot(
      bodyUnits,
      readyOutline({}, [
        {
          node_id: "n1",
          parent_node_id: null,
          depth: 1,
          title: "Reversed",
          start_unit_id: "u3",
          end_unit_id: "u1",
          start_anchor_segment_id: null,
          end_anchor_segment_id: null,
          order_index: 1,
        },
      ]),
    );
    expect(
      projectReaderSemanticOutlineNav(snap, makeDoc(["u1", "u2", "u3", "u4"]))
        .available,
    ).toBe(false);
  });

  it("L1 eligible + L2 does not change L1 projection / units", () => {
    const without = makeSnapshot(l1Units);
    const withOl = makeSnapshot(
      l1Units,
      readyOutline(
        {},
        [
          {
            node_id: "n1",
            parent_node_id: null,
            depth: 1,
            title: "Outline root",
            start_unit_id: "u1",
            end_unit_id: "u6",
            start_anchor_segment_id: null,
            end_anchor_segment_id: null,
            order_index: 1,
          },
        ],
      ),
    );
    const doc = makeDoc(["u1", "u2", "u3", "u4", "u5", "u6"]);
    const navA = projectReaderRecordNavigation(without, doc);
    const navB = projectReaderRecordNavigation(withOl, doc);
    expect(navB.mode).toBe("L1");
    expect(navB.items).toEqual(navA.items);
    expect(withOl.navigation.units).toEqual(without.navigation.units);

    const l2 = projectReaderSemanticOutlineNav(withOl, doc);
    expect(l2.available).toBe(true);
  });

  it("body-only L0 + L2 available", () => {
    const snap = makeSnapshot(bodyUnits, readyOutline());
    const doc = makeDoc(["u1", "u2", "u3", "u4"]);
    const nav = projectReaderRecordNavigation(snap, doc);
    expect(nav.mode).toBe("L0");
    expect(projectReaderSemanticOutlineNav(snap, doc).available).toBe(true);
  });

  it("ticks never include depth>1 even with many nodes", () => {
    const nodes = Array.from({ length: 30 }, (_, i) => ({
      node_id: `n${i}`,
      parent_node_id: i === 0 ? null : "n0",
      depth: i === 0 ? 1 : 2,
      title: `N${i}`,
      start_unit_id: "u1",
      end_unit_id: "u1",
      start_anchor_segment_id: null,
      end_anchor_segment_id: null,
      order_index: i + 1,
    }));
    // Only one root with many children — still only depth=1 ticks.
    const snap = makeSnapshot(bodyUnits, readyOutline({}, nodes as never));
    const proj = projectReaderSemanticOutlineNav(
      snap,
      makeDoc(["u1", "u2", "u3", "u4"]),
    );
    expect(proj.available).toBe(true);
    expect(proj.tickItems).toHaveLength(1);
    expect(proj.tickItems[0]?.depth).toBe(1);
    expect(proj.panelItems.length).toBe(30);
  });
});
