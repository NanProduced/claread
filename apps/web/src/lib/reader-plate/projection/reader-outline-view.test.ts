import { describe, expect, it } from "vitest";

import {
  applySemanticOutlineRoles,
  pickReaderOutlineSource,
  projectMarkdownOutlineView,
  projectReaderOutlineView,
  selectMostSpecificCoveringNode,
  type OutlineItem,
  type ReaderOutlineViewModel,
} from "@/lib/reader-plate/projection/reader-outline-view";
import type { ReaderSemanticOutlineProjectionDto } from "@/lib/reader-plate/projection/semantic-outline";
import {
  READER_RECORD_PLATE_DOCUMENT_SCHEMA_VERSION,
  type ReaderRecordPlateDocument,
} from "@/lib/reader-plate/projection/reader-record-plate-document";
import {
  READER_PLATE_SNAPSHOT_SCHEMA_KIND,
  READER_TEXT_RANGE_HASH_ALGORITHM,
  type ReaderPlateSnapshotDto,
} from "@/types/api/reader-plate";

type UnitIn = {
  unit_id: string;
  order_index: number;
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
        unit_type: "body",
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

const doc4 = () => makeDoc(["u1", "u2", "u3", "u4"]);

type ViewModelOverrides = {
  sourceKind: ReaderOutlineViewModel["identity"]["sourceKind"];
  identity?: Partial<ReaderOutlineViewModel["identity"]>;
} & Partial<Omit<ReaderOutlineViewModel, "identity">>;

function makeViewModel(overrides: ViewModelOverrides): ReaderOutlineViewModel {
  const { sourceKind, identity, ...rest } = overrides;
  return {
    available: false,
    status: null,
    isPartial: false,
    panelItems: [],
    tickItems: [],
    orderedUnitIds: [],
    unitOrderById: new Map(),
    ...rest,
    identity: {
      sourceKind,
      sourceIdentityKey: "base_1:1",
      revision: null,
      ...identity,
    },
  };
}

describe("projectReaderOutlineView (semantic source mapping)", () => {
  it("ready outline maps to a source-agnostic view model", () => {
    const view = projectReaderOutlineView(
      makeSnapshot(bodyUnits, readyOutline()),
      doc4(),
    );

    expect(view.available).toBe(true);
    expect(view.status).toBe("ready");
    expect(view.isPartial).toBe(false);
    expect(view.identity.sourceKind).toBe("semantic");
    expect(view.identity.sourceIdentityKey).toBe("base_1:1");
    expect(view.identity.revision).toBe("rev_1");

    expect(view.panelItems.map((i) => i.key)).toEqual(["n1", "n2", "n3"]);
    const root = view.panelItems[0]!;
    expect(root.depth).toBe(1);
    expect(root.title).toBe("Root A");
    expect(root.parentKey).toBeNull();
    expect(root.target).toEqual({ unitId: "u1", anchorSegmentId: "seg_u1" });
    expect(root.coverage).toEqual({ startUnitId: "u1", endUnitId: "u3" });
    expect(root.orderIndex).toBe(1);
    // A semantic node without a same-start child is a navigable section.
    expect(root.role).toBe("section");

    const child = view.panelItems[1]!;
    expect(child.parentKey).toBe("n1");
    expect(child.depth).toBe(2);
    // Only the *start* target survives the projection (end anchor is dropped).
    expect(child.target.anchorSegmentId).toBeNull();

    expect(view.tickItems.map((i) => i.key)).toEqual(["n1", "n3"]);
    expect(view.tickItems.every((i) => i.depth === 1)).toBe(true);
    expect(view.orderedUnitIds).toEqual(["u1", "u2", "u3", "u4"]);
    expect(view.unitOrderById.get("u3")).toBe(3);
  });

  it("partial maps to available + isPartial, source kind semantic", () => {
    const view = projectReaderOutlineView(
      makeSnapshot(
        bodyUnits,
        readyOutline({
          status: "partial",
          diagnostics: {
            drops: [{ node_id: "bad", reason_code: "empty_title" }],
            skipped_node_count: 1,
          },
        }),
      ),
      doc4(),
    );
    expect(view.available).toBe(true);
    expect(view.isPartial).toBe(true);
    expect(view.identity.sourceKind).toBe("semantic");
  });

  it.each([
    ["null", null],
    ["absent", undefined],
    ["pending", readyOutline({ status: "pending" })],
    ["failed", readyOutline({ status: "failed" })],
    ["stale", readyOutline({ status: "stale" })],
    ["unavailable", readyOutline({ status: "unavailable" })],
  ] as const)("%s → unavailable, no unit fallback", (_label, outline) => {
    const view = projectReaderOutlineView(
      makeSnapshot(bodyUnits, outline as never),
      doc4(),
    );
    expect(view.available).toBe(false);
    expect(view.panelItems).toEqual([]);
    expect(view.tickItems).toEqual([]);
    // Falls through to the (unavailable) semantic model; identity key still present
    // so the rail's reset fence works pre-availability.
    expect(view.identity.sourceKind).toBe("semantic");
    expect(view.identity.sourceIdentityKey).toBe("base_1:1");
  });

  it("empty nodes fail-closed", () => {
    const view = projectReaderOutlineView(
      makeSnapshot(bodyUnits, readyOutline({}, [])),
      doc4(),
    );
    expect(view.available).toBe(false);
  });

  it("source identity mismatch fail-closed", () => {
    const view = projectReaderOutlineView(
      makeSnapshot(
        bodyUnits,
        readyOutline({ source_identity: { base_id: "base_other", generation: 1 } }),
      ),
      doc4(),
    );
    expect(view.available).toBe(false);
  });
});

describe("pickReaderOutlineSource (priority seam)", () => {
  const markdownItem: OutlineItem = {
    key: "md-1",
    parentKey: null,
    depth: 1,
    title: "Markdown Heading",
    target: { unitId: "u1", anchorSegmentId: null },
    coverage: { startUnitId: "u1", endUnitId: "u4" },
    orderIndex: 1,
    fallbackIndex: 0,
    role: "section",
  };

  it("a usable markdown outline wins over semantic", () => {
    const markdown = makeViewModel({
      sourceKind: "markdown",
      available: true,
      status: "ready",
      panelItems: [markdownItem],
      tickItems: [markdownItem],
    });
    const semantic = makeViewModel({
      sourceKind: "semantic",
      available: true,
      status: "ready",
    });
    const picked = pickReaderOutlineSource(markdown, semantic);
    expect(picked.identity.sourceKind).toBe("markdown");
    expect(picked.panelItems.map((i) => i.key)).toEqual(["md-1"]);
  });

  it("unavailable markdown falls through to semantic", () => {
    const markdown = makeViewModel({ sourceKind: "markdown", available: false });
    const semantic = makeViewModel({
      sourceKind: "semantic",
      available: true,
      status: "ready",
    });
    expect(pickReaderOutlineSource(markdown, semantic).identity.sourceKind).toBe(
      "semantic",
    );
  });

  it("usable markdown wins even when semantic is unavailable", () => {
    const markdown = makeViewModel({
      sourceKind: "markdown",
      available: true,
      status: "ready",
      panelItems: [markdownItem],
    });
    const semantic = makeViewModel({ sourceKind: "semantic", available: false });
    const picked = pickReaderOutlineSource(markdown, semantic);
    expect(picked.identity.sourceKind).toBe("markdown");
    expect(picked.available).toBe(true);
  });

  it("both unavailable → unavailable (rail hides, no fallback)", () => {
    const markdown = makeViewModel({ sourceKind: "markdown", available: false });
    const semantic = makeViewModel({ sourceKind: "semantic", available: false });
    const picked = pickReaderOutlineSource(markdown, semantic);
    expect(picked.available).toBe(false);
    expect(picked.panelItems).toEqual([]);
  });
});

describe("projectMarkdownOutlineView (not implemented this round)", () => {
  it("returns an honest unavailable model with no faked headings", () => {
    const view = projectMarkdownOutlineView(
      makeSnapshot(bodyUnits, readyOutline()),
      doc4(),
    );
    expect(view.available).toBe(false);
    expect(view.identity.sourceKind).toBe("markdown");
    expect(view.identity.sourceIdentityKey).toBe("base_1:1");
    expect(view.panelItems).toEqual([]);
    expect(view.tickItems).toEqual([]);
  });
});

describe("selectMostSpecificCoveringNode (outline view model)", () => {
  const items: OutlineItem[] = [
    {
      key: "root",
      parentKey: null,
      depth: 1,
      title: "R",
      target: { unitId: "u1", anchorSegmentId: null },
      coverage: { startUnitId: "u1", endUnitId: "u4" },
      orderIndex: 1,
      fallbackIndex: 0,
      role: "section",
    },
    {
      key: "child",
      parentKey: "root",
      depth: 2,
      title: "C",
      target: { unitId: "u2", anchorSegmentId: null },
      coverage: { startUnitId: "u2", endUnitId: "u3" },
      orderIndex: 2,
      fallbackIndex: 1,
      role: "section",
    },
  ];
  const order = new Map([
    ["u1", 1],
    ["u2", 2],
    ["u3", 3],
    ["u4", 4],
  ]);

  it("picks deepest covering item", () => {
    expect(selectMostSpecificCoveringNode(items, order, "u2")).toBe("child");
    expect(selectMostSpecificCoveringNode(items, order, "u1")).toBe("root");
  });

  it("null unit → null", () => {
    expect(selectMostSpecificCoveringNode(items, order, null)).toBeNull();
  });
});

describe("applySemanticOutlineRoles (semantic group vs section)", () => {
  function node(
    key: string,
    parentKey: string | null,
    depth: number,
    startUnitId: string,
    endUnitId: string,
    orderIndex: number,
    anchor: string | null = null,
  ): OutlineItem {
    return {
      key,
      parentKey,
      depth,
      title: key,
      target: { unitId: startUnitId, anchorSegmentId: anchor },
      coverage: { startUnitId, endUnitId },
      orderIndex,
      fallbackIndex: 0,
      role: "section",
    };
  }

  it("marks a parent sharing its first child's start as a group", () => {
    const out = applySemanticOutlineRoles([
      node("root", null, 1, "u1", "u2", 1, "s1"),
      node("a", "root", 2, "u1", "u1", 2, "s1"),
      node("b", "root", 2, "u2", "u2", 3, "s9"),
    ]);
    const root = out.find((n) => n.key === "root")!;
    expect(root.role).toBe("group");
    // Preserved verbatim — kept for hierarchy, NOT flattened or stripped.
    expect(root.depth).toBe(1);
    expect(root.parentKey).toBeNull();
    expect(root.title).toBe("root");
    expect(root.coverage).toEqual({ startUnitId: "u1", endUnitId: "u2" });
    expect(root.target).toEqual({ unitId: "u1", anchorSegmentId: "s1" });
    // Children stay sections at their own depth (navigable derived from role).
    expect(out.find((n) => n.key === "a")!.role).toBe("section");
    expect(out.find((n) => n.key === "a")!.depth).toBe(2);
    expect(out.find((n) => n.key === "a")!.parentKey).toBe("root");
    expect(out.find((n) => n.key === "b")!.role).toBe("section");
    expect(out.find((n) => n.key === "b")!.depth).toBe(2);
    expect(out.find((n) => n.key === "b")!.parentKey).toBe("root");
  });

  it("keeps a parent with an independent start as a section", () => {
    const out = applySemanticOutlineRoles([
      node("root", null, 1, "u1", "u3", 1, "own"),
      node("a", "root", 2, "u1", "u2", 2, "s1"),
    ]);
    expect(out.find((n) => n.key === "root")!.role).toBe("section");
  });

  it("keeps a childless node as a section", () => {
    const out = applySemanticOutlineRoles([
      node("leaf", null, 1, "u1", "u1", 1),
    ]);
    expect(out[0]!.role).toBe("section");
  });

  it("projectReaderOutlineView yields the record as group + two sections", () => {
    const units = [
      { unit_id: "u1", order_index: 1 },
      { unit_id: "u2", order_index: 2 },
    ];
    const view = projectReaderOutlineView(
      makeSnapshot(
        units,
        readyOutline({}, [
          {
            node_id: "root",
            parent_node_id: null,
            depth: 1,
            title: "哈里王子与王室关系紧张",
            start_unit_id: "u1",
            end_unit_id: "u2",
            start_anchor_segment_id: "s1",
            end_anchor_segment_id: null,
            order_index: 1,
          },
          {
            node_id: "a",
            parent_node_id: "root",
            depth: 2,
            title: "哈里王子被拒住白金汉宫",
            start_unit_id: "u1",
            end_unit_id: "u1",
            start_anchor_segment_id: "s1",
            end_anchor_segment_id: null,
            order_index: 2,
          },
          {
            node_id: "b",
            parent_node_id: "root",
            depth: 2,
            title: "媒体关注关系恶化",
            start_unit_id: "u2",
            end_unit_id: "u2",
            start_anchor_segment_id: "s9",
            end_anchor_segment_id: null,
            order_index: 3,
          },
        ]),
      ),
      makeDoc(["u1", "u2"]),
    );
    expect(view.available).toBe(true);
    expect(view.panelItems.map((n) => n.key)).toEqual(["root", "a", "b"]);
    expect(view.panelItems[0]!.role).toBe("group");
    expect(view.panelItems[0]!.depth).toBe(1);
    expect(view.panelItems[1]!.role).toBe("section");
    expect(view.panelItems[1]!.depth).toBe(2);
    expect(view.panelItems[2]!.role).toBe("section");
    expect(view.panelItems[2]!.depth).toBe(2);
    expect(view.tickItems.map((n) => n.key)).toEqual(["root"]);
  });

  it("the priority combinator never applies the semantic group rule", () => {
    // A Markdown adapter emits headings as sections even when a parent and its
    // first child share a start; only the semantic adapter marks groups.
    const shared = (
      key: string,
      parent: string | null,
      unit: string,
    ): OutlineItem => ({
      key,
      parentKey: parent,
      depth: parent ? 2 : 1,
      title: key,
      target: { unitId: unit, anchorSegmentId: "s1" },
      coverage: { startUnitId: unit, endUnitId: unit },
      orderIndex: 1,
      fallbackIndex: 0,
      role: "section",
    });
    const markdown = makeViewModel({
      sourceKind: "markdown",
      available: true,
      status: "ready",
      panelItems: [shared("p", null, "u1"), shared("c", "p", "u1")],
      tickItems: [shared("p", null, "u1")],
    });
    const semantic = makeViewModel({ sourceKind: "semantic", available: false });
    const picked = pickReaderOutlineSource(markdown, semantic);
    expect(picked.panelItems.every((n) => n.role === "section")).toBe(true);
  });
});

describe("OutlineItem role contract (type-level single source of truth)", () => {
  it("exposes only `role`; navigability is derived, not a parallel field", () => {
    const item: OutlineItem = {
      key: "x",
      parentKey: null,
      depth: 1,
      title: "x",
      target: { unitId: "u1", anchorSegmentId: null },
      coverage: { startUnitId: "u1", endUnitId: "u1" },
      orderIndex: 1,
      fallbackIndex: 0,
      role: "section",
    };
    // Derivation the UI relies on everywhere.
    expect(item.role === "section").toBe(true);
    // A separate `navigable` field must not exist — it would re-introduce the
    // role/navigable drift. Re-adding it leaves this @ts-expect-error unused,
    // which fails `tsc`, so the contradictory combination is impossible by
    // construction.
    // @ts-expect-error navigable is intentionally not a field on OutlineItem
    expect(item.navigable).toBeUndefined();
  });
});
