import { describe, expect, it } from "vitest";
import type {
  ReaderStableDocumentAnchorSegmentDto,
  ReaderStableDocumentBaseDto,
  ReaderStableDocumentBlockDto,
  ReaderStableDocumentMetadataDto,
  ReaderStableDocumentResponseDto,
} from "@/types/api/reader-plate";
import {
  buildStableDocumentIndex,
  findStableAnchorSegmentById,
  findStableBlockById,
  getStableDocumentBaseText,
  resolveStableAnchorText,
  sliceStableTextByUtf16,
} from "./stable-document-resolver";

// --- Fixtures -------------------------------------------------------------

function makeBase(overrides: Partial<ReaderStableDocumentBaseDto> = {}): ReaderStableDocumentBaseDto {
  return {
    base_id: "base_1",
    content_sha256: "sha_1",
    content_utf16_length: 0,
    canonicalizer_version: "v1",
    builder_version: "v1",
    segmenter_version: "v1",
    language: "en",
    title_snapshot: null,
    navigation: {},
    text: "",
    ...overrides,
  };
}

function makeMeta(): ReaderStableDocumentMetadataDto {
  return {
    stable_document_id: "sd_1",
    document_version: 1,
    title: "Doc",
    language: "en",
    source_profile: {},
    content_sha256: "sha_1",
    status: "ready",
  };
}

function makeDocument(
  text: string,
  blocks: Array<Partial<ReaderStableDocumentBlockDto> & { block_id: string }>,
  segments: Array<Partial<ReaderStableDocumentAnchorSegmentDto> & { anchor_segment_id: string }>,
): ReaderStableDocumentResponseDto {
  return {
    reading_record_id: "rec_1",
    record_generation: 1,
    active_base_id: "base_1",
    base: makeBase({ text, content_utf16_length: text.length }),
    stable_document: makeMeta(),
    blocks: blocks.map((b) => ({
      parent_block_id: null,
      order_index: 0,
      block_type: "paragraph",
      text_content: null,
      payload: {},
      source_refs: {},
      quality: {},
      canonical_text_start_utf16: null,
      canonical_text_end_utf16: null,
      interpretation_policy: {},
      ...b,
    })),
    anchor_segments: segments.map((s) => ({
      unit_id: "unit_1",
      order_index: 0,
      segment_type: "sentence",
      base_start_utf16: 0,
      base_end_utf16: 0,
      text_hash: "hash_1",
      ...s,
    })),
  };
}

// --- Tests ----------------------------------------------------------------

describe("stable-document-resolver — index lookups", () => {
  it("buildStableDocumentIndex returns Map lookups for blocks and anchor segments", () => {
    const doc = makeDocument(
      "hello world",
      [{ block_id: "blk_1", text_content: "hello", canonical_text_start_utf16: 0, canonical_text_end_utf16: 5 }],
      [{ anchor_segment_id: "seg_1", base_start_utf16: 0, base_end_utf16: 5 }],
    );
    const index = buildStableDocumentIndex(doc);
    expect(index.blocksById.size).toBe(1);
    expect(index.anchorsById.size).toBe(1);
    expect(findStableBlockById(index, "blk_1")?.block_id).toBe("blk_1");
    expect(findStableAnchorSegmentById(index, "seg_1")?.anchor_segment_id).toBe("seg_1");
    expect(findStableBlockById(index, "missing")).toBeNull();
    expect(findStableAnchorSegmentById(index, "missing")).toBeNull();
  });

  it("findStableBlockById / findStableAnchorSegmentById reject empty ids", () => {
    const doc = makeDocument("hi", [], []);
    const index = buildStableDocumentIndex(doc);
    expect(findStableBlockById(index, "")).toBeNull();
    expect(findStableAnchorSegmentById(index, "")).toBeNull();
  });
});

describe("stable-document-resolver — UTF-16 slicing (strict integer checks)", () => {
  it("slices ASCII text correctly", () => {
    expect(sliceStableTextByUtf16("hello world", 0, 5)).toBe("hello");
    expect(sliceStableTextByUtf16("hello world", 6, 11)).toBe("world");
  });

  it("slices around an emoji using JS code-unit semantics", () => {
    const text = "ab😀cd";
    expect(text.length).toBe(6);
    expect(sliceStableTextByUtf16(text, 0, 1)).toBe("a");
    expect(sliceStableTextByUtf16(text, 1, 2)).toBe("b");
    expect(sliceStableTextByUtf16(text, 4, 5)).toBe("c");
    expect(sliceStableTextByUtf16(text, 5, 6)).toBe("d");
  });

  it("returns null for fractional offsets (not an integer)", () => {
    expect(sliceStableTextByUtf16("hello world", 0.5, 5)).toBeNull();
    expect(sliceStableTextByUtf16("hello world", 0, 5.5)).toBeNull();
    expect(sliceStableTextByUtf16("hello world", 0.1, 5.9)).toBeNull();
  });

  it("returns null for NaN / Infinity / -Infinity offsets", () => {
    expect(sliceStableTextByUtf16("hi", NaN, 2)).toBeNull();
    expect(sliceStableTextByUtf16("hi", 0, NaN)).toBeNull();
    expect(sliceStableTextByUtf16("hi", Infinity, 2)).toBeNull();
    expect(sliceStableTextByUtf16("hi", 0, Infinity)).toBeNull();
    expect(sliceStableTextByUtf16("hi", -Infinity, 2)).toBeNull();
  });

  it("returns null for negative offsets", () => {
    expect(sliceStableTextByUtf16("hi", -1, 2)).toBeNull();
    expect(sliceStableTextByUtf16("hi", 0, -1)).toBeNull();
  });

  it("returns null when start > end or end > text.length", () => {
    expect(sliceStableTextByUtf16("hi", 2, 1)).toBeNull();
    expect(sliceStableTextByUtf16("hi", 0, 5)).toBeNull();
    expect(sliceStableTextByUtf16("hi", 1, 2)).toBe("i");
  });
});

describe("stable-document-resolver — anchor segment resolution (no clamp)", () => {
  const SEG_TEXT = "institutional memory shapes policy";

  function makeDoc() {
    return makeDocument(SEG_TEXT, [], [
      { anchor_segment_id: "seg_mem", base_start_utf16: 0, base_end_utf16: 13 },
    ]);
  }

  it("returns the segment's full range when no overrides are provided", () => {
    expect(resolveStableAnchorText(makeDoc(), { anchorSegmentId: "seg_mem" })).toBe(
      "institutional",
    );
  });

  it("accepts a valid partial override (only start)", () => {
    // Override start inside segment, no end override → use segment end
    expect(
      resolveStableAnchorText(makeDoc(), { anchorSegmentId: "seg_mem", startUtf16: 1 }),
    ).toBe("nstitutional");
  });

  it("accepts a valid partial override (only end)", () => {
    // Override end inside segment, no start override → use segment start
    expect(
      resolveStableAnchorText(makeDoc(), { anchorSegmentId: "seg_mem", endUtf16: 12 }),
    ).toBe("institutiona");
  });

  it("accepts a full override inside the segment range", () => {
    expect(
      resolveStableAnchorText(makeDoc(), { anchorSegmentId: "seg_mem", startUtf16: 1, endUtf16: 13 }),
    ).toBe("nstitutional memory".slice(0, 12));
  });

  it("rejects overrides that fall outside the segment range (no clamping)", () => {
    // end override beyond segment end → null
    expect(
      resolveStableAnchorText(makeDoc(), { anchorSegmentId: "seg_mem", startUtf16: 0, endUtf16: 100 }),
    ).toBeNull();
    // start override before segment start → null
    expect(
      resolveStableAnchorText(makeDoc(), { anchorSegmentId: "seg_mem", startUtf16: -5, endUtf16: 5 }),
    ).toBeNull();
  });

  it("rejects fractional overrides (no clamping to integer boundary)", () => {
    expect(
      resolveStableAnchorText(makeDoc(), {
        anchorSegmentId: "seg_mem",
        startUtf16: 0.5,
        endUtf16: 13,
      }),
    ).toBeNull();
    expect(
      resolveStableAnchorText(makeDoc(), {
        anchorSegmentId: "seg_mem",
        startUtf16: 0,
        endUtf16: 12.9,
      }),
    ).toBeNull();
  });

  it("returns null for an unknown anchorSegmentId even when a valid blockId is also given (no fallback)", () => {
    const doc = makeDocument(
      SEG_TEXT,
      [{ block_id: "blk_a", text_content: "institutional", canonical_text_start_utf16: 0, canonical_text_end_utf16: 13 }],
      [],
    );
    expect(
      resolveStableAnchorText(doc, {
        anchorSegmentId: "seg_missing",
        blockId: "blk_a",
      }),
    ).toBeNull();
  });

  it("returns null for an empty anchorSegmentId even when a valid blockId is also given (no fallback)", () => {
    const doc = makeDocument(
      SEG_TEXT,
      [{ block_id: "blk_a", text_content: "institutional", canonical_text_start_utf16: 0, canonical_text_end_utf16: 13 }],
      [],
    );
    expect(
      resolveStableAnchorText(doc, {
        anchorSegmentId: "",
        blockId: "blk_a",
      }),
    ).toBeNull();
  });
});

describe("stable-document-resolver — block text resolution (no clamp)", () => {
  function makeDoc() {
    return makeDocument(
      "institutional memory",
      [{ block_id: "blk_a", text_content: "institutional memory", canonical_text_start_utf16: 0, canonical_text_end_utf16: 20 }],
      [],
    );
  }

  it("returns the block's full canonical range when no overrides are provided", () => {
    expect(resolveStableAnchorText(makeDoc(), { blockId: "blk_a" })).toBe(
      "institutional memory",
    );
  });

  it("accepts a valid partial override (only start)", () => {
    // Override start inside block, no end override → use block end (20).
    // Expected slice: "institutional memory".slice(1, 20) = "nstitutional memory" (19 chars).
    const expected = "institutional memory".slice(1, 20);
    expect(
      resolveStableAnchorText(makeDoc(), { blockId: "blk_a", startUtf16: 1 }),
    ).toBe(expected);
  });

  it("accepts a valid partial override (only end)", () => {
    // Override end inside block, no start override → use block start (0).
    // Expected slice: "institutional memory".slice(0, 20) = "institutional memory" (20 chars).
    expect(
      resolveStableAnchorText(makeDoc(), { blockId: "blk_a", endUtf16: 20 }),
    ).toBe("institutional memory");
  });

  it("rejects overrides that fall outside the block range (no clamping)", () => {
    const doc = makeDocument(
      "institutional memory",
      [{ block_id: "blk_a", text_content: "institutional memory", canonical_text_start_utf16: 5, canonical_text_end_utf16: 20 }],
      [],
    );
    // start override < block start → null
    expect(
      resolveStableAnchorText(doc, { blockId: "blk_a", startUtf16: 0, endUtf16: 10 }),
    ).toBeNull();
    // end override > block end → null
    expect(
      resolveStableAnchorText(doc, { blockId: "blk_a", startUtf16: 6, endUtf16: 30 }),
    ).toBeNull();
  });

  it("rejects fractional block overrides", () => {
    expect(
      resolveStableAnchorText(makeDoc(), { blockId: "blk_a", startUtf16: 0.5 }),
    ).toBeNull();
  });

  it("returns null if block has no text_content (no fallback to payload)", () => {
    const doc = makeDocument(
      "institutional memory",
      [
        {
          block_id: "blk_empty",
          text_content: null,
          canonical_text_start_utf16: 0,
          canonical_text_end_utf16: 12,
        },
      ],
      [],
    );
    expect(resolveStableAnchorText(doc, { blockId: "blk_empty" })).toBeNull();
  });
});

describe("stable-document-resolver — null on invalid inputs", () => {
  it("returns null when blockId is unknown", () => {
    const doc = makeDocument("hi", [], []);
    expect(resolveStableAnchorText(doc, { blockId: "missing" })).toBeNull();
  });

  it("returns null when neither blockId nor anchorSegmentId is given", () => {
    const doc = makeDocument("hi", [], []);
    expect(
      resolveStableAnchorText(doc, { startUtf16: 0, endUtf16: 1 }),
    ).toBeNull();
  });

  it("returns null when override start > override end", () => {
    const doc = makeDocument("hello world", [], []);
    expect(
      resolveStableAnchorText(doc, { startUtf16: 5, endUtf16: 2 }),
    ).toBeNull();
  });

  it("returns null if segment offsets point past the base text length", () => {
    const doc = makeDocument(
      "short",
      [],
      [{ anchor_segment_id: "seg_oob", base_start_utf16: 0, base_end_utf16: 9999 }],
    );
    expect(resolveStableAnchorText(doc, { anchorSegmentId: "seg_oob" })).toBeNull();
  });
});

describe("stable-document-resolver — base text passthrough", () => {
  it("returns the canonical text verbatim", () => {
    const doc = makeDocument("canonical text", [], []);
    expect(getStableDocumentBaseText(doc)).toBe("canonical text");
  });
});

describe("stable-document-resolver — does NOT reach for forbidden truth sources", () => {
  it("source file contains no references to Plate JSON, Slate path, DOM selection, or original_inputs.source_text", async () => {
    const { readFileSync } = await import("node:fs");
    const { resolve: pathResolve } = await import("node:path");
    const source = readFileSync(
      pathResolve(process.cwd(), "src/lib/reader-orchestration/stable-document-resolver.ts"),
      "utf-8",
    );
    // Strip comments so the docstring's mention of forbidden sources is
    // excluded from the source-vs-source check.
    const stripComments = (s: string) =>
      s.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
    const stripped = stripComments(source);
    expect(stripped).not.toMatch(/plate\.json|adaptReaderPlate|ReaderPlate[A-Z]/);
    expect(stripped).not.toMatch(/Slate|\.slate/);
    expect(stripped).not.toMatch(/window\.getSelection|Range\.getClientRects/);
    expect(stripped).not.toMatch(/original_inputs/);
    expect(stripped).not.toMatch(/\bsource_text\b/);
    expect(stripped).not.toMatch(/parseMarkdown|\.md`/);
  });

  it("stable-document Web route guard: calls getReaderStableDocumentFromWeb and no legacy analysis", async () => {
    const { readFileSync } = await import("node:fs");
    const { resolve: pathResolve } = await import("node:path");
    const routeSource = readFileSync(
      pathResolve(
        process.cwd(),
        "src/app/api/web/reader-plate/records/[recordId]/stable-document/route.ts",
      ),
      "utf-8",
    );
    expect(routeSource).toContain("getReaderStableDocumentFromWeb");
    expect(routeSource).toContain("recordId");
    expect(routeSource).not.toContain("submitAnalysisFromWeb");
    expect(routeSource).not.toContain("legacyAppReaderRoute");
    expect(routeSource).not.toContain("analysis-tasks");
    expect(routeSource).not.toMatch(/slice|text_content|anchorSegments/);
  });
});
