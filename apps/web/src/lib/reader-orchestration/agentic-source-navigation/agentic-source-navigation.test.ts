import { describe, expect, it, vi } from "vitest";

import type { AgenticEvidenceRagNavigation } from "@/components/reader/ask/agentic-evidence";
import type { ReaderAskAgenticEvidenceScopeDto } from "@/types/api/reader-ask";

import {
  createNavigateAgenticSource,
  type AgenticSourceDescriptor,
  type CurrentPageIdentity,
  type NavigateAgenticSource,
  type SourceNavigationResult,
} from "./agentic-source-navigation";
import type {
  DomNavigationCandidate,
  DomNavigationHit,
  ReaderDomNavigationAdapter,
} from "./reader-dom-navigation-adapter";

const SCOPE: ReaderAskAgenticEvidenceScopeDto = {
  reading_record_id: "record-1",
  base_id: "base-1",
  record_generation: 3,
  stable_document_id: "stable-1",
};

const SCOPE_NO_STABLE: ReaderAskAgenticEvidenceScopeDto = {
  reading_record_id: "record-1",
  base_id: "base-1",
  record_generation: 3,
  stable_document_id: null,
};

const PAGE_READY: CurrentPageIdentity = {
  readingRecordId: "record-1",
  baseId: "base-1",
  recordGeneration: 3,
  stableDocument: { status: "ready", stableDocumentId: "stable-1" },
};

const COMPLETE_RAG: AgenticEvidenceRagNavigation = {
  stableDocumentId: "stable-1",
  baseId: "base-1",
  recordGeneration: 3,
  unitIds: ["u1"],
  anchorSegmentIds: ["s1"],
  canonicalTextStartUtf16: 0,
  canonicalTextEndUtf16: 10,
};

function makeMemoryAdapter(
  resolver: (
    candidates: readonly DomNavigationCandidate[],
  ) => DomNavigationHit | null,
): ReaderDomNavigationAdapter & {
  calls: DomNavigationCandidate[][];
  scrollCount: number;
} {
  const calls: DomNavigationCandidate[][] = [];
  let scrollCount = 0;
  return {
    calls,
    get scrollCount() {
      return scrollCount;
    },
    resolveAndScroll(candidates) {
      calls.push([...candidates]);
      const hit = resolver(candidates);
      if (hit) scrollCount += 1;
      return hit;
    },
  };
}

function firstCandidateAdapter(): ReturnType<typeof makeMemoryAdapter> {
  return makeMemoryAdapter((candidates) =>
    candidates.length > 0
      ? { mode: candidates[0]!.mode, targetId: candidates[0]!.targetId }
      : null,
  );
}

function hitWhen(
  predicate: (c: DomNavigationCandidate) => boolean,
): ReturnType<typeof makeMemoryAdapter> {
  return makeMemoryAdapter((candidates) => {
    const found = candidates.find(predicate);
    return found
      ? { mode: found.mode, targetId: found.targetId }
      : null;
  });
}

function missAdapter(): ReturnType<typeof makeMemoryAdapter> {
  return makeMemoryAdapter(() => null);
}

function nav(
  load: () => CurrentPageIdentity | Promise<CurrentPageIdentity>,
  dom?: ReaderDomNavigationAdapter,
): NavigateAgenticSource {
  return createNavigateAgenticSource({
    loadCurrentPageIdentity: load,
    domAdapter: dom,
  });
}

function anchorSource(
  overrides: Partial<AgenticSourceDescriptor> = {},
): AgenticSourceDescriptor {
  return {
    handleId: "evh_anchor",
    kind: "initial_anchor",
    evidenceScope: SCOPE_NO_STABLE,
    unitId: "u1",
    anchorSegmentId: "s1",
    ragNavigation: null,
    ...overrides,
  };
}

function searchSource(
  overrides: Partial<AgenticSourceDescriptor> = {},
): AgenticSourceDescriptor {
  return {
    handleId: "evh_search",
    kind: "search_hit",
    evidenceScope: SCOPE,
    unitId: null,
    anchorSegmentId: null,
    ragNavigation: COMPLETE_RAG,
    ...overrides,
  };
}

function assertNoSecrets(result: SourceNavigationResult) {
  const raw = JSON.stringify(result);
  expect(raw).not.toContain("fingerprint");
  expect(raw).not.toContain("sha256");
  expect(raw).not.toContain("querySelector");
  expect(raw).not.toContain("snippet");
  expect(raw).not.toContain("envelope");
  expect(raw).not.toContain("data-");
}

describe("createNavigateAgenticSource — identity", () => {
  it("1. missing scope → legacy_scope_missing without loader/DOM", async () => {
    const load = vi.fn(() => PAGE_READY);
    const adapter = firstCandidateAdapter();
    const navigate = nav(load, adapter);
    const result = await navigate(
      searchSource({ evidenceScope: null }),
    );
    expect(result).toEqual({
      status: "unavailable",
      reason: "legacy_scope_missing",
    });
    expect(load).not.toHaveBeenCalled();
    expect(adapter.calls).toHaveLength(0);
  });

  it("2. reading record mismatch", async () => {
    const result = await nav(
      () => ({ ...PAGE_READY, readingRecordId: "other-record" }),
      firstCandidateAdapter(),
    )(anchorSource());
    expect(result).toEqual({
      status: "identity_mismatch",
      field: "reading_record",
    });
  });

  it("3. base mismatch", async () => {
    const result = await nav(
      () => ({ ...PAGE_READY, baseId: "other-base" }),
      firstCandidateAdapter(),
    )(anchorSource());
    expect(result).toEqual({
      status: "identity_mismatch",
      field: "base",
    });
  });

  it("4. generation mismatch only returns stale_generation", async () => {
    const result = await nav(
      () => ({ ...PAGE_READY, recordGeneration: 99 }),
      firstCandidateAdapter(),
    )(anchorSource());
    expect(result).toEqual({ status: "stale_generation" });
    expect(result).not.toMatchObject({ field: "generation" });
  });

  it("5. non-RAG stable=null still navigates when record/base/gen match", async () => {
    const adapter = firstCandidateAdapter();
    const result = await nav(
      () => ({
        ...PAGE_READY,
        stableDocument: { status: "not_ready", stableDocumentId: null },
      }),
      adapter,
    )(anchorSource({ evidenceScope: SCOPE_NO_STABLE }));
    expect(result).toEqual({
      status: "navigated",
      mode: "anchor_segment",
      targetId: "s1",
    });
  });

  it.each([
    ["loading", "loading" as const],
    ["not_ready", "not_ready" as const],
    ["stale", "stale" as const],
    ["failed", "failed" as const],
  ])(
    "6. search_hit page stable %s → page_identity_incomplete",
    async (_label, status) => {
      const result = await nav(
        () => ({
          ...PAGE_READY,
          stableDocument: { status, stableDocumentId: null },
        }),
        firstCandidateAdapter(),
      )(searchSource());
      expect(result).toEqual({
        status: "unavailable",
        reason: "page_identity_incomplete",
      });
    },
  );

  it("7. search_hit stable mismatch with page", async () => {
    const result = await nav(
      () => ({
        ...PAGE_READY,
        stableDocument: { status: "ready", stableDocumentId: "stable-OTHER" },
      }),
      firstCandidateAdapter(),
    )(searchSource());
    expect(result).toEqual({
      status: "identity_mismatch",
      field: "stable_document",
    });
  });

  it("8a. scope vs rag base mismatch → identity_mismatch.base", async () => {
    const result = await nav(
      () => PAGE_READY,
      firstCandidateAdapter(),
    )(
      searchSource({
        ragNavigation: { ...COMPLETE_RAG, baseId: "base-OTHER" },
      }),
    );
    expect(result).toEqual({
      status: "identity_mismatch",
      field: "base",
    });
  });

  it("8b. scope vs rag generation mismatch → stale_generation", async () => {
    const result = await nav(
      () => PAGE_READY,
      firstCandidateAdapter(),
    )(
      searchSource({
        ragNavigation: { ...COMPLETE_RAG, recordGeneration: 9 },
      }),
    );
    expect(result).toEqual({ status: "stale_generation" });
  });

  it("8c. scope vs rag stable mismatch → identity_mismatch.stable_document", async () => {
    const result = await nav(
      () => PAGE_READY,
      firstCandidateAdapter(),
    )(
      searchSource({
        ragNavigation: {
          ...COMPLETE_RAG,
          stableDocumentId: "stable-OTHER",
        },
      }),
    );
    expect(result).toEqual({
      status: "identity_mismatch",
      field: "stable_document",
    });
  });

  it("8d. search_hit with null scope.stable → page_identity_incomplete", async () => {
    const result = await nav(
      () => PAGE_READY,
      firstCandidateAdapter(),
    )(searchSource({ evidenceScope: SCOPE_NO_STABLE }));
    expect(result).toEqual({
      status: "unavailable",
      reason: "page_identity_incomplete",
    });
  });
});

describe("createNavigateAgenticSource — evidence kinds", () => {
  it("9. observation always observation_only (even with locators)", async () => {
    const load = vi.fn(() => PAGE_READY);
    const adapter = firstCandidateAdapter();
    const result = await nav(load, adapter)({
      handleId: "evh_obs",
      kind: "observation",
      evidenceScope: SCOPE,
      unitId: "u1",
      anchorSegmentId: "s1",
      ragNavigation: COMPLETE_RAG,
    });
    expect(result).toEqual({
      status: "unavailable",
      reason: "observation_only",
    });
    expect(load).not.toHaveBeenCalled();
    expect(adapter.calls).toHaveLength(0);
  });

  it("10. initial_anchor segment hit", async () => {
    const result = await nav(
      () => PAGE_READY,
      firstCandidateAdapter(),
    )(anchorSource());
    expect(result).toEqual({
      status: "navigated",
      mode: "anchor_segment",
      targetId: "s1",
    });
  });

  it("11. initial_anchor segment miss → unit fallback", async () => {
    const adapter = hitWhen((c) => c.mode === "unit");
    const result = await nav(() => PAGE_READY, adapter)(anchorSource());
    expect(result).toEqual({
      status: "navigated",
      mode: "unit",
      targetId: "u1",
    });
    expect(adapter.calls[0]?.map((c) => c.mode)).toEqual([
      "anchor_segment",
      "unit",
    ]);
  });

  it("12. read_range unit-only", async () => {
    const result = await nav(
      () => PAGE_READY,
      firstCandidateAdapter(),
    )({
      handleId: "evh_range",
      kind: "read_range",
      evidenceScope: SCOPE_NO_STABLE,
      unitId: "u2",
      anchorSegmentId: null,
      ragNavigation: null,
    });
    expect(result).toEqual({
      status: "navigated",
      mode: "unit",
      targetId: "u2",
    });
  });

  it("13. non-RAG no locator", async () => {
    const load = vi.fn(() => PAGE_READY);
    const result = await nav(load, firstCandidateAdapter())(
      anchorSource({ unitId: null, anchorSegmentId: null }),
    );
    expect(result).toEqual({
      status: "unavailable",
      reason: "no_locator",
    });
    expect(load).not.toHaveBeenCalled();
  });

  it("14. search_hit partial citation", async () => {
    const load = vi.fn(() => PAGE_READY);
    const result = await nav(load, firstCandidateAdapter())(
      searchSource({ ragNavigation: null }),
    );
    expect(result).toEqual({
      status: "unavailable",
      reason: "partial_citation",
    });
    expect(load).not.toHaveBeenCalled();
  });

  it("15. search_hit canonical range only → canonical_range_unsupported", async () => {
    const load = vi.fn(() => PAGE_READY);
    const result = await nav(load, firstCandidateAdapter())(
      searchSource({
        ragNavigation: {
          ...COMPLETE_RAG,
          unitIds: [],
          anchorSegmentIds: [],
          canonicalTextStartUtf16: 0,
          canonicalTextEndUtf16: 42,
        },
      }),
    );
    expect(result).toEqual({
      status: "unavailable",
      reason: "canonical_range_unsupported",
    });
    expect(load).not.toHaveBeenCalled();
  });

  it("16. search_hit complete locator navigates", async () => {
    const result = await nav(
      () => PAGE_READY,
      firstCandidateAdapter(),
    )(searchSource());
    expect(result).toEqual({
      status: "navigated",
      mode: "anchor_segment",
      targetId: "s1",
    });
  });
});

describe("createNavigateAgenticSource — candidates", () => {
  it("17. multi segment tries in order; first hit stops", async () => {
    const tried: string[] = [];
    const adapter = makeMemoryAdapter((candidates) => {
      for (const c of candidates) {
        if (c.mode === "anchor_segment") {
          tried.push(c.targetId);
          if (c.targetId === "s2") {
            return { mode: c.mode, targetId: c.targetId };
          }
        }
      }
      return null;
    });
    const result = await nav(
      () => PAGE_READY,
      adapter,
    )(
      searchSource({
        ragNavigation: {
          ...COMPLETE_RAG,
          anchorSegmentIds: ["s1", "s2", "s3"],
          unitIds: ["u9"],
        },
      }),
    );
    expect(result).toEqual({
      status: "navigated",
      mode: "anchor_segment",
      targetId: "s2",
    });
    // Adapter receives full ordered list; first success is s2
    expect(adapter.calls[0]?.map((c) => c.targetId)).toEqual([
      "s1",
      "s2",
      "s3",
      "u9",
    ]);
  });

  it("18. duplicate segment/unit deduped preserving order", async () => {
    const adapter = firstCandidateAdapter();
    await nav(
      () => PAGE_READY,
      adapter,
    )(
      searchSource({
        ragNavigation: {
          ...COMPLETE_RAG,
          anchorSegmentIds: ["s1", "s1", "s2"],
          unitIds: ["u1", "u1"],
        },
      }),
    );
    expect(adapter.calls[0]).toEqual([
      { mode: "anchor_segment", targetId: "s1" },
      { mode: "anchor_segment", targetId: "s2" },
      { mode: "unit", targetId: "u1" },
    ]);
  });

  it("19. all segments miss then unit hit", async () => {
    const adapter = hitWhen((c) => c.mode === "unit" && c.targetId === "u1");
    const result = await nav(
      () => PAGE_READY,
      adapter,
    )(
      searchSource({
        ragNavigation: {
          ...COMPLETE_RAG,
          anchorSegmentIds: ["s-missing"],
          unitIds: ["u1"],
        },
      }),
    );
    expect(result).toEqual({
      status: "navigated",
      mode: "unit",
      targetId: "u1",
    });
  });

  it("20. all miss → target_not_found + attemptedModes", async () => {
    const result = await nav(
      () => PAGE_READY,
      missAdapter(),
    )(searchSource());
    expect(result).toEqual({
      status: "target_not_found",
      attemptedModes: ["anchor_segment", "unit"],
    });
  });

  it("21. does not use snippet for navigation (descriptor has no snippet field)", async () => {
    const source = searchSource();
    expect(source).not.toHaveProperty("snippet");
    const result = await nav(
      () => PAGE_READY,
      firstCandidateAdapter(),
    )(source);
    assertNoSecrets(result);
  });
});

// ---------------------------------------------------------------------------
// R4-A1: article_seed source navigation
//
// article_seed is a non-RAG evidence kind. When it carries a valid
// unit_id / anchor_segment_id, it navigates the same way as initial_anchor
// and read_range. When it has no locator, it is display-only (no_locator).
// It must not be confused with search_hit (no ragNavigation expected).
// ---------------------------------------------------------------------------

describe("createNavigateAgenticSource — article_seed (R4-A1)", () => {
  function seedSource(
    overrides: Partial<AgenticSourceDescriptor> = {},
  ): AgenticSourceDescriptor {
    return {
      handleId: "evh_seed",
      kind: "article_seed",
      evidenceScope: SCOPE_NO_STABLE,
      unitId: "u1",
      anchorSegmentId: "s1",
      ragNavigation: null,
      ...overrides,
    };
  }

  it("22. article_seed with anchor_segment locator navigates", async () => {
    const adapter = firstCandidateAdapter();
    const result = await nav(() => PAGE_READY, adapter)(seedSource());
    expect(result).toEqual({
      status: "navigated",
      mode: "anchor_segment",
      targetId: "s1",
    });
  });

  it("23. article_seed with unit-only locator navigates", async () => {
    const adapter = firstCandidateAdapter();
    const result = await nav(
      () => PAGE_READY,
      adapter,
    )(seedSource({ anchorSegmentId: null }));
    expect(result).toEqual({
      status: "navigated",
      mode: "unit",
      targetId: "u1",
    });
  });

  it("24. article_seed without locator → no_locator (display-only)", async () => {
    const load = vi.fn(() => PAGE_READY);
    const adapter = firstCandidateAdapter();
    const result = await nav(load, adapter)(
      seedSource({ unitId: null, anchorSegmentId: null }),
    );
    expect(result).toEqual({
      status: "unavailable",
      reason: "no_locator",
    });
    expect(load).not.toHaveBeenCalled();
    expect(adapter.calls).toHaveLength(0);
  });

  it("25. article_seed missing scope → legacy_scope_missing (display-only)", async () => {
    const load = vi.fn(() => PAGE_READY);
    const adapter = firstCandidateAdapter();
    const result = await nav(load, adapter)(
      seedSource({ evidenceScope: null }),
    );
    expect(result).toEqual({
      status: "unavailable",
      reason: "legacy_scope_missing",
    });
    expect(load).not.toHaveBeenCalled();
    expect(adapter.calls).toHaveLength(0);
  });

  it("26. article_seed identity mismatch → identity_mismatch.reading_record", async () => {
    const result = await nav(
      () => ({ ...PAGE_READY, readingRecordId: "other-record" }),
      firstCandidateAdapter(),
    )(seedSource());
    expect(result).toEqual({
      status: "identity_mismatch",
      field: "reading_record",
    });
  });

  it("27. article_seed stale generation → stale_generation", async () => {
    const result = await nav(
      () => ({ ...PAGE_READY, recordGeneration: 99 }),
      firstCandidateAdapter(),
    )(seedSource());
    expect(result).toEqual({ status: "stale_generation" });
  });

  it("28. article_seed non-RAG stable=null still navigates when record/base/gen match", async () => {
    const adapter = firstCandidateAdapter();
    const result = await nav(
      () => ({
        ...PAGE_READY,
        stableDocument: { status: "not_ready", stableDocumentId: null },
      }),
      adapter,
    )(seedSource({ evidenceScope: SCOPE_NO_STABLE }));
    expect(result).toEqual({
      status: "navigated",
      mode: "anchor_segment",
      targetId: "s1",
    });
  });

  it("29. article_seed all candidates miss → target_not_found", async () => {
    const result = await nav(
      () => PAGE_READY,
      missAdapter(),
    )(seedSource());
    expect(result).toEqual({
      status: "target_not_found",
      attemptedModes: ["anchor_segment", "unit"],
    });
  });

  it("30. article_seed does not leak internal fields in result", async () => {
    const result = await nav(
      () => PAGE_READY,
      firstCandidateAdapter(),
    )(seedSource());
    assertNoSecrets(result);
  });
});

describe("createNavigateAgenticSource — privacy / interface", () => {
  it("31. results never contain fingerprint/hash/selector/snippet", async () => {
    const cases: SourceNavigationResult[] = [
      await nav(() => PAGE_READY, firstCandidateAdapter())(searchSource()),
      await nav(() => PAGE_READY, missAdapter())(searchSource()),
      await nav(() => PAGE_READY, firstCandidateAdapter())(
        searchSource({ evidenceScope: null }),
      ),
      await nav(
        () => ({ ...PAGE_READY, readingRecordId: "x" }),
        firstCandidateAdapter(),
      )(anchorSource()),
    ];
    for (const r of cases) assertNoSecrets(r);
  });

  it("32. Ask-facing callback accepts only AgenticSourceDescriptor (no Element/Document/identity)", async () => {
    const navigate = nav(() => PAGE_READY, firstCandidateAdapter());
    // Type-level: NavigateAgenticSource is (source) => Promise<result>
    // Runtime: only one argument used.
    const result = await navigate(searchSource());
    expect(result.status).toBe("navigated");
    expect(navigate.length).toBe(1);
  });
});

describe("createNavigateAgenticSource — SSR / loader resilience", () => {
  it("P0: factory without domAdapter is safe under Node (no document at construct)", async () => {
    // This file uses the default vitest environment (node) — no jsdom.
    // Constructing without injecting domAdapter must not throw ReferenceError.
    let navigate!: NavigateAgenticSource;
    expect(() => {
      navigate = createNavigateAgenticSource({
        loadCurrentPageIdentity: () => PAGE_READY,
        // intentionally omit domAdapter
      });
    }).not.toThrow();

    // Navigation resolves via lazy default adapter which fail-closes without document.
    const result = await navigate(anchorSource());
    expect(result).toEqual({
      status: "target_not_found",
      attemptedModes: ["anchor_segment", "unit"],
    });
  });

  it("P1: identity loader rejection → page_identity_incomplete; DOM not called; no leak", async () => {
    const secret = "SECRET_LOADER_STACK_TRACE_xyz";
    const adapter = firstCandidateAdapter();
    const navigate = createNavigateAgenticSource({
      loadCurrentPageIdentity: async () => {
        throw new Error(secret);
      },
      domAdapter: adapter,
    });

    const result = await navigate(anchorSource());
    expect(result).toEqual({
      status: "unavailable",
      reason: "page_identity_incomplete",
    });
    expect(adapter.calls).toHaveLength(0);
    expect(adapter.scrollCount).toBe(0);
    assertNoSecrets(result);
    expect(JSON.stringify(result)).not.toContain(secret);
    expect(JSON.stringify(result)).not.toContain("Error");
  });
});
