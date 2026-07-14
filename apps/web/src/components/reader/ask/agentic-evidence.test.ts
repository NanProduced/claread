import { describe, expect, it } from "vitest";
import type { ReaderAskAgenticEvidenceItemDto } from "@/types/api/reader-ask";
import {
  projectAgenticEvidenceForDisplay,
  type AgenticEvidenceDisplayItem,
} from "./agentic-evidence";

const COMPLETE_RAG_CITATION = {
  rag_substrate_id: "substrate-1",
  index_run_id: "index-run-1",
  index_version: "v1",
  plan_content_sha256: "plan-sha-abc",
  source_scope: "main_reading_text" as const,
  block_type: "paragraph",
  chunk_id: "chunk-1",
  content_sha256: "content-sha-def",
  canonical_text_start_utf16: 10,
  canonical_text_end_utf16: 42,
  snippet: "climate change impacts",
  score: 0.91,
  stable_document_id: "doc-stable-1",
  base_id: "base-1",
  record_generation: 1,
  block_ids: ["b1"],
  unit_ids: ["u1"],
  anchor_segment_ids: ["s1"],
};

function freezeSnapshot(items: AgenticEvidenceDisplayItem[]) {
  return JSON.parse(JSON.stringify(items)) as unknown;
}

describe("projectAgenticEvidenceForDisplay", () => {
  it("projects initial_anchor, read_range, and observation with stable titles and snippets", () => {
    const input: ReaderAskAgenticEvidenceItemDto[] = [
      {
        handle_id: "evh_anchor",
        kind: "initial_anchor",
        source_tool: "initial_anchor",
        snippet: "selected sentence",
        unit_id: "u1",
        anchor_segment_id: "s1",
      },
      {
        handle_id: "evh_range",
        kind: "read_range",
        source_tool: "read_range",
        snippet: "paragraph body",
        unit_id: "u2",
      },
      {
        handle_id: "evh_obs",
        kind: "observation",
        source_tool: "agent_observation",
        snippet: null,
      },
    ];

    const projected = projectAgenticEvidenceForDisplay(input);

    expect(projected).toHaveLength(3);
    expect(projected[0]).toEqual({
      handleId: "evh_anchor",
      kind: "initial_anchor",
      title: "初始选区",
      snippet: "selected sentence",
      sourceTool: "initial_anchor",
      ragNavigation: null,
    });
    expect(projected[1]).toEqual({
      handleId: "evh_range",
      kind: "read_range",
      title: "阅读范围",
      snippet: "paragraph body",
      sourceTool: "read_range",
      ragNavigation: null,
    });
    expect(projected[2]).toEqual({
      handleId: "evh_obs",
      kind: "observation",
      title: "观察结果",
      snippet: "",
      sourceTool: "agent_observation",
      ragNavigation: null,
    });
  });

  it("projects search_hit ragNavigation only when rag_citation is complete", () => {
    const input: ReaderAskAgenticEvidenceItemDto[] = [
      {
        handle_id: "evh_search",
        kind: "search_hit",
        source_tool: "search_current_article",
        snippet: "climate change impacts",
        unit_id: "u1",
        anchor_segment_id: "s1",
        rag_citation: COMPLETE_RAG_CITATION,
      },
    ];

    const projected = projectAgenticEvidenceForDisplay(input);

    expect(projected).toHaveLength(1);
    expect(projected[0].kind).toBe("search_hit");
    expect(projected[0].title).toBe("文章检索");
    expect(projected[0].snippet).toBe("climate change impacts");
    expect(projected[0].ragNavigation).toEqual({
      stableDocumentId: "doc-stable-1",
      baseId: "base-1",
      recordGeneration: 1,
      unitIds: ["u1"],
      anchorSegmentIds: ["s1"],
      canonicalTextStartUtf16: 10,
      canonicalTextEndUtf16: 42,
    });
  });

  it("keeps search_hit displayable when rag_citation is missing, with null navigation", () => {
    const input: ReaderAskAgenticEvidenceItemDto[] = [
      {
        handle_id: "evh_search_bare",
        kind: "search_hit",
        source_tool: "search_current_article",
        snippet: "hit without citation",
      },
    ];

    const projected = projectAgenticEvidenceForDisplay(input);

    expect(projected).toHaveLength(1);
    expect(projected[0]).toEqual({
      handleId: "evh_search_bare",
      kind: "search_hit",
      title: "文章检索",
      snippet: "hit without citation",
      sourceTool: "search_current_article",
      ragNavigation: null,
    });
  });

  it("does not invent navigation from partial rag_citation", () => {
    const input: ReaderAskAgenticEvidenceItemDto[] = [
      {
        handle_id: "evh_search_partial",
        kind: "search_hit",
        source_tool: "search_current_article",
        snippet: "partial citation",
        rag_citation: {
          ...COMPLETE_RAG_CITATION,
          // Drop identity required for navigation.
          stable_document_id: "",
        },
      },
    ];

    const projected = projectAgenticEvidenceForDisplay(input);
    expect(projected[0].ragNavigation).toBeNull();
  });


  it("rejects negative, fractional, and reversed UTF-16 navigation ranges", () => {
    const invalidRanges = [
      { start: -1, end: 5 },
      { start: 1.5, end: 5 },
      { start: 8, end: 7 },
      { start: 0, end: 2.5 },
    ];

    for (const range of invalidRanges) {
      const projected = projectAgenticEvidenceForDisplay([
        {
          handle_id: "evh_invalid_range",
          kind: "search_hit",
          source_tool: "search_current_article",
          snippet: "invalid range",
          rag_citation: {
            ...COMPLETE_RAG_CITATION,
            canonical_text_start_utf16: range.start,
            canonical_text_end_utf16: range.end,
          },
        },
      ]);

      expect(projected[0].ragNavigation).toBeNull();
    }
  });
  it("omits internal/debug fields from the projected output", () => {
    const input: ReaderAskAgenticEvidenceItemDto[] = [
      {
        handle_id: "evh_search",
        kind: "search_hit",
        source_tool: "search_current_article",
        snippet: "climate change impacts",
        rag_citation: COMPLETE_RAG_CITATION,
      },
    ];

    const projected = projectAgenticEvidenceForDisplay(input);
    const serialized = JSON.stringify(projected);

    expect(serialized).not.toContain("rag_substrate_id");
    expect(serialized).not.toContain("substrate-1");
    expect(serialized).not.toContain("index_run_id");
    expect(serialized).not.toContain("index-run-1");
    expect(serialized).not.toContain("plan_content_sha256");
    expect(serialized).not.toContain("plan-sha-abc");
    expect(serialized).not.toContain("content_sha256");
    expect(serialized).not.toContain("content-sha-def");
    expect(serialized).not.toContain("\"score\"");
    expect(serialized).not.toContain("0.91");
    expect(serialized).not.toContain("index_version");
    expect(serialized).not.toContain("source_scope");
    expect(serialized).not.toContain("block_type");
    expect(serialized).not.toContain("chunk_id");

    // Navigation is present but only with allowlisted fields.
    expect(projected[0].ragNavigation).not.toBeNull();
    expect(Object.keys(projected[0].ragNavigation!).sort()).toEqual(
      [
        "anchorSegmentIds",
        "baseId",
        "canonicalTextEndUtf16",
        "canonicalTextStartUtf16",
        "recordGeneration",
        "stableDocumentId",
        "unitIds",
      ].sort(),
    );
  });

  it("returns an empty array for empty input and preserves multi-item order", () => {
    expect(projectAgenticEvidenceForDisplay([])).toEqual([]);

    const input: ReaderAskAgenticEvidenceItemDto[] = [
      {
        handle_id: "evh_1",
        kind: "initial_anchor",
        source_tool: "initial_anchor",
        snippet: "first",
      },
      {
        handle_id: "evh_2",
        kind: "search_hit",
        source_tool: "search_current_article",
        snippet: "second",
        rag_citation: COMPLETE_RAG_CITATION,
      },
      {
        handle_id: "evh_3",
        kind: "read_range",
        source_tool: "read_range",
        snippet: "third",
      },
    ];

    const projected = projectAgenticEvidenceForDisplay(input);
    expect(projected.map((item) => item.handleId)).toEqual([
      "evh_1",
      "evh_2",
      "evh_3",
    ]);
    expect(projected.map((item) => item.kind)).toEqual([
      "initial_anchor",
      "search_hit",
      "read_range",
    ]);
  });

  it("does not mutate the input DTO", () => {
    const input: ReaderAskAgenticEvidenceItemDto[] = [
      {
        handle_id: "evh_search",
        kind: "search_hit",
        source_tool: "search_current_article",
        snippet: "climate change impacts",
        unit_id: "u1",
        rag_citation: {
          ...COMPLETE_RAG_CITATION,
          unit_ids: ["u1"],
          anchor_segment_ids: ["s1"],
        },
      },
    ];
    const before = freezeSnapshot(input as unknown as AgenticEvidenceDisplayItem[]);

    const projected = projectAgenticEvidenceForDisplay(input);

    // Mutating the projected navigation arrays must not touch input.
    projected[0].ragNavigation?.unitIds.push("mutated");
    projected[0].ragNavigation?.anchorSegmentIds.push("mutated");

    expect(freezeSnapshot(input as unknown as AgenticEvidenceDisplayItem[])).toEqual(
      before,
    );
    expect(input[0].rag_citation?.unit_ids).toEqual(["u1"]);
    expect(input[0].rag_citation?.anchor_segment_ids).toEqual(["s1"]);
    expect(input[0].snippet).toBe("climate change impacts");
  });
});
