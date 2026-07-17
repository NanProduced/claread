/** @vitest-environment jsdom */

/**
 * Default Plate source-navigation seam (R3C-C formal path).
 * Mocks construction seams; does not re-implement identity/selector policy.
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { render } from "@testing-library/react";

import {
  READER_PLATE_SNAPSHOT_SCHEMA_KIND,
  READER_TEXT_RANGE_HASH_ALGORITHM,
  type ReaderPlateSnapshotDto,
} from "@/types/api/reader-plate";

const loadFactoryCalls: Array<{
  readingRecordId: string;
  baseId: string;
  recordGeneration: number;
}> = [];

const navigateFactoryResultA = Object.assign(vi.fn(async () => ({
  status: "target_not_found" as const,
  attemptedModes: ["unit" as const],
})), { __navId: "nav-a" });

const navigateFactoryResultB = Object.assign(vi.fn(async () => ({
  status: "target_not_found" as const,
  attemptedModes: ["unit" as const],
})), { __navId: "nav-b" });

let navigateCallCount = 0;

vi.mock(
  "@/lib/reader-orchestration/agentic-source-navigation/current-page-identity-loader",
  () => ({
    createCurrentPageIdentityLoader: (input: {
      readingRecordId: string;
      baseId: string;
      recordGeneration: number;
    }) => {
      loadFactoryCalls.push({ ...input });
      return async () => ({
        readingRecordId: input.readingRecordId,
        baseId: input.baseId,
        recordGeneration: input.recordGeneration,
        stableDocument: {
          status: "not_ready" as const,
          stableDocumentId: null,
        },
      });
    },
  }),
);

vi.mock(
  "@/lib/reader-orchestration/agentic-source-navigation/agentic-source-navigation",
  () => ({
    createNavigateAgenticSource: () => {
      navigateCallCount += 1;
      return navigateCallCount === 1
        ? navigateFactoryResultA
        : navigateFactoryResultB;
    },
  }),
);

vi.mock("@/components/providers/appearance-provider", () => ({
  useAppearance: () => ({
    themePreference: "system",
    setThemePreference: vi.fn(),
  }),
}));

vi.mock("@/components/layout/app-shell", () => ({
  useAppShellLayout: () => ({
    isWorkspaceShell: false,
    releaseSidebarForReadingTool: vi.fn(),
    sidebarMode: "expanded",
  }),
}));

vi.mock("@/components/reader/plate/ReaderRecordNavigationRail", () => ({
  ReaderRecordNavigationRail: () => <div data-testid="nav-rail-stub" />,
}));

vi.mock("@/components/reader/plate/useReaderAskPresentation", () => ({
  useReaderAskPresentation: () => ({
    askOpen: true,
    askSurface: "sidecar",
    setAskSurface: vi.fn(),
    setAskOpen: vi.fn(),
    effectiveSurface: "sidecar",
    askSidecarOpen: true,
    showCapacityDowngradeNotice: false,
    setCapacityDowngradeDismissed: vi.fn(),
  }),
  readerAskPresentationCssVars: () => ({}),
}));

const panelPropsSpy = vi.fn();

vi.mock("@/components/reader/AiWorkspacePanel", () => ({
  AiWorkspacePanel: (props: Record<string, unknown>) => {
    panelPropsSpy(props);
    return <div data-testid="ai-workspace-panel-stub" />;
  },
}));

// Heavy plate internals — keep surface mount light.
vi.mock("@claread/contracts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@claread/contracts")>();
  return actual;
});

import { ReaderRecordPlateSurface } from "./ReaderRecordPlateSurface";

function minimalSnapshot(
  overrides: Partial<{
    record_id: string;
    base_id: string;
    generation: number;
  }> = {},
): ReaderPlateSnapshotDto {
  const recordId = overrides.record_id ?? "record-plate-1";
  const baseId = overrides.base_id ?? "base-plate-1";
  const generation = overrides.generation ?? 2;
  return {
    schema_kind: READER_PLATE_SNAPSHOT_SCHEMA_KIND,
    snapshot_id: "snap-plate-1",
    snapshot_taken_at: "2026-07-17T00:00:00Z",
    last_event_sequence: 1,
    record_id: recordId,
    record: {
      title: "Plate Title",
      display_title_zh: null,
      title_generation_status: "succeeded",
      title_generation_error_code: null,
      title_generation_error_message: null,
      reading_goal: "daily_reading",
      reading_variant: "intermediate_reading",
      created_at: "2026-07-17T00:00:00Z",
      source_type: "plain_text",
      source_metadata: {},
      generation,
      product_state: "readable_enhancing",
      readiness_state: "article_ready",
    },
    base: {
      base_id: baseId,
      content_sha256: "b".repeat(64),
      canonicalizer_version: "v1",
      builder_version: "v1",
      segmenter_version: "v1",
      text_length_utf16: 4,
      hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
    },
    navigation: { units: [] },
    anchor_segments: [],
    enhancement_layers: [],
    ask_supplements: [],
    user_assets: [],
    parsed_decisions: [],
    value: [],
  } as ReaderPlateSnapshotDto;
}

describe("ReaderRecordPlateSurface source navigation (formal path)", () => {
  beforeEach(() => {
    panelPropsSpy.mockClear();
    loadFactoryCalls.length = 0;
    navigateCallCount = 0;
  });

  it("1–2. passes function onNavigateAgenticSource without identity/DOM props", () => {
    render(<ReaderRecordPlateSurface snapshot={minimalSnapshot()} />);
    expect(panelPropsSpy).toHaveBeenCalled();
    const props = panelPropsSpy.mock.calls[0]![0] as Record<string, unknown>;
    expect(typeof props.onNavigateAgenticSource).toBe("function");
    expect(props.onNavigateAgenticSource).toBe(navigateFactoryResultA);
    expect(props).not.toHaveProperty("loadCurrentPageIdentity");
    expect(props).not.toHaveProperty("currentPageIdentity");
    expect(props).not.toHaveProperty("document");
    expect(props).not.toHaveProperty("domAdapter");
    expect(props).not.toHaveProperty("stableDocumentId");
  });

  it("3. loader is built from snapshot record/base/generation", () => {
    render(
      <ReaderRecordPlateSurface
        snapshot={minimalSnapshot({
          record_id: "rec-x",
          base_id: "base-y",
          generation: 7,
        })}
      />,
    );
    expect(loadFactoryCalls).toEqual([
      {
        readingRecordId: "rec-x",
        baseId: "base-y",
        recordGeneration: 7,
      },
    ]);
  });

  it("4. generation change rebuilds navigation callback identity", () => {
    const { rerender } = render(
      <ReaderRecordPlateSurface snapshot={minimalSnapshot({ generation: 1 })} />,
    );
    const first = (panelPropsSpy.mock.calls[0]![0] as {
      onNavigateAgenticSource: unknown;
    }).onNavigateAgenticSource;

    rerender(
      <ReaderRecordPlateSurface snapshot={minimalSnapshot({ generation: 2 })} />,
    );
    const second = (panelPropsSpy.mock.calls.at(-1)![0] as {
      onNavigateAgenticSource: unknown;
    }).onNavigateAgenticSource;

    expect(first).toBe(navigateFactoryResultA);
    expect(second).toBe(navigateFactoryResultB);
    expect(second).not.toBe(first);
  });

  it("5. same identity re-render does not rebuild navigation callback", () => {
    const snap = minimalSnapshot({ generation: 3 });
    const { rerender } = render(<ReaderRecordPlateSurface snapshot={snap} />);
    const first = (panelPropsSpy.mock.calls[0]![0] as {
      onNavigateAgenticSource: unknown;
    }).onNavigateAgenticSource;
    const factoriesAfterFirst = navigateCallCount;

    // New object, same fence identity fields
    rerender(
      <ReaderRecordPlateSurface
        snapshot={{ ...snap, snapshot_id: "snap-plate-1-rerender" }}
      />,
    );
    const second = (panelPropsSpy.mock.calls.at(-1)![0] as {
      onNavigateAgenticSource: unknown;
    }).onNavigateAgenticSource;

    expect(second).toBe(first);
    expect(navigateCallCount).toBe(factoriesAfterFirst);
  });

  it("6. callback is createNavigateAgenticSource return value (not inline DOM ops)", () => {
    render(<ReaderRecordPlateSurface snapshot={minimalSnapshot()} />);
    const props = panelPropsSpy.mock.calls[0]![0] as {
      onNavigateAgenticSource: { __navId?: string };
    };
    expect(props.onNavigateAgenticSource.__navId).toBe("nav-a");
    expect(navigateCallCount).toBeGreaterThanOrEqual(1);
  });
});
