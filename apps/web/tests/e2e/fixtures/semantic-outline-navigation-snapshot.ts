/**
 * T5.5a — Browser fixture builders for L2 semantic outline navigation.
 * Pure snapshot factories for Chromium E2E on the plate spike harness.
 */

import {
  makeNavigationFixtureSnapshot,
  type L1NavUnitSpec,
} from "./l1-heading-navigation-snapshot";

export function bodyOnlyUnitSpecs(): L1NavUnitSpec[] {
  return [
    {
      unit_id: "u1",
      order_index: 1,
      unit_type: "body",
      label: null,
      text: tall("First body section of the article for outline navigation"),
    },
    {
      unit_id: "u2",
      order_index: 2,
      unit_type: "body",
      label: null,
      text: tall("Second body section continues with more readable content here"),
    },
    {
      unit_id: "u3",
      order_index: 3,
      unit_type: "body",
      label: null,
      text: tall("Third body section finishes the fixture document for scroll"),
    },
    {
      unit_id: "u4",
      order_index: 4,
      unit_type: "body",
      label: null,
      text: tall("Fourth body section provides extra vertical room for tests"),
    },
  ];
}

function tall(seed: string): string {
  return Array.from({ length: 40 }, (_, i) => `${seed} line ${i + 1}.`).join(
    " ",
  );
}

export function makeReadySemanticOutline(
  options?: {
    baseId?: string;
    generation?: number;
    status?: "ready" | "partial";
    revision?: string;
  },
): Record<string, unknown> {
  const baseId = options?.baseId ?? "base_1";
  const generation = options?.generation ?? 1;
  return {
    schema_kind: "reader_semantic_outline",
    schema_version: 1,
    status: options?.status ?? "ready",
    source_identity: { base_id: baseId, generation },
    publication: {
      outline_revision: options?.revision ?? "olrev_e2e_1",
      layer_id: "layer_ol_e2e",
      published_at: "2026-07-17T00:00:00Z",
    },
    provenance: { kind: "llm", builder: "e2e", model: "test" },
    nodes: [
      {
        node_id: "n1",
        parent_node_id: null,
        depth: 1,
        title: "Opening Theme",
        start_unit_id: "u1",
        end_unit_id: "u2",
        start_anchor_segment_id: "seg_u1",
        end_anchor_segment_id: null,
        order_index: 1,
      },
      {
        node_id: "n2",
        parent_node_id: "n1",
        depth: 2,
        title: "Detail Under Opening",
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
        title: "Closing Theme",
        start_unit_id: "u3",
        end_unit_id: "u4",
        start_anchor_segment_id: "seg_u3",
        end_anchor_segment_id: null,
        order_index: 3,
      },
    ],
    diagnostics: { drops: [], skipped_node_count: 0 },
  };
}

export function makeSemanticOutlineSnapshot(options?: {
  baseId?: string;
  generation?: number;
  withOutline?: boolean;
  outlineStatus?: "ready" | "partial";
}): Record<string, unknown> {
  const baseId = options?.baseId ?? "base_1";
  const generation = options?.generation ?? 1;
  const snap = makeNavigationFixtureSnapshot({
    units: bodyOnlyUnitSpecs(),
    baseId,
    generation,
    snapshotId: "snap_l2_outline_1",
    recordId: "record_l2_outline",
  });
  if (options?.withOutline === false) {
    return snap;
  }
  return {
    ...snap,
    semantic_outline: makeReadySemanticOutline({
      baseId,
      generation,
      status: options?.outlineStatus ?? "ready",
    }),
  };
}

export function makeL1PlusOutlineSnapshot(): Record<string, unknown> {
  const units: L1NavUnitSpec[] = [
    {
      unit_id: "u1",
      order_index: 1,
      unit_type: "body",
      label: null,
      text: tall("Lead body before headings"),
    },
    {
      unit_id: "u2",
      order_index: 2,
      unit_type: "heading",
      label: "Chapter One",
      text: "Chapter One",
    },
    {
      unit_id: "u3",
      order_index: 3,
      unit_type: "body",
      label: null,
      text: tall("Body under chapter one"),
    },
    {
      unit_id: "u4",
      order_index: 4,
      unit_type: "body",
      label: null,
      text: tall("More body under chapter one"),
    },
    {
      unit_id: "u5",
      order_index: 5,
      unit_type: "heading",
      label: "Chapter Two",
      text: "Chapter Two",
    },
    {
      unit_id: "u6",
      order_index: 6,
      unit_type: "body",
      label: null,
      text: tall("Body under chapter two"),
    },
  ];
  const snap = makeNavigationFixtureSnapshot({
    units,
    snapshotId: "snap_l1_l2",
    recordId: "record_l1_l2",
  });
  return {
    ...snap,
    semantic_outline: {
      schema_kind: "reader_semantic_outline",
      schema_version: 1,
      status: "ready",
      source_identity: { base_id: "base_1", generation: 1 },
      publication: {
        outline_revision: "olrev_l1l2",
        layer_id: "layer_ol_l1l2",
        published_at: "2026-07-17T00:00:00Z",
      },
      provenance: { kind: "llm", builder: "e2e", model: "test" },
      nodes: [
        {
          node_id: "ol_root",
          parent_node_id: null,
          depth: 1,
          title: "Semantic Whole",
          start_unit_id: "u1",
          end_unit_id: "u6",
          start_anchor_segment_id: null,
          end_anchor_segment_id: null,
          order_index: 1,
        },
      ],
      diagnostics: { drops: [], skipped_node_count: 0 },
    },
  };
}
