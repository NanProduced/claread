/** @vitest-environment jsdom */

/**
 * Workbench source-navigation seam (R3C-C fail-closed).
 *
 * Workbench remains display-only until it has a canonical DOM adapter
 * (`.reader-record-plate-document` + unit/segment attrs). It must not pass
 * onNavigateAgenticSource, or users get guaranteed target_not_found clicks.
 */

import { describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";

import {
  READER_PLATE_SNAPSHOT_SCHEMA_KIND,
  READER_TEXT_RANGE_HASH_ALGORITHM,
  type ReaderPlateSnapshotDto,
} from "@/types/api/reader-plate";

vi.mock("@/components/providers/appearance-provider", () => ({
  useAppearance: () => ({
    themePreference: "system",
    setThemePreference: vi.fn(),
  }),
}));

vi.mock("@/components/reader/plate/ImmersiveReaderSurface", () => ({
  ImmersiveReaderSurface: () => <div data-testid="immersive-stub" />,
}));

vi.mock("@/components/reader/plate/IntensiveReaderSurface", () => ({
  IntensiveReaderSurface: () => <div data-testid="intensive-stub" />,
}));

const panelPropsSpy = vi.fn();

vi.mock("@/components/reader/AiWorkspacePanel", () => ({
  AiWorkspacePanel: (props: Record<string, unknown>) => {
    panelPropsSpy(props);
    return <div data-testid="ai-workspace-panel-stub" />;
  },
}));

import { ReaderRecordWorkbenchSurface } from "./ReaderRecordWorkbenchSurface";

function minimalSnapshot(
  overrides: Partial<{
    record_id: string;
    base_id: string;
    generation: number;
  }> = {},
): ReaderPlateSnapshotDto {
  const recordId = overrides.record_id ?? "record-1";
  const baseId = overrides.base_id ?? "base-1";
  const generation = overrides.generation ?? 1;
  return {
    schema_kind: READER_PLATE_SNAPSHOT_SCHEMA_KIND,
    snapshot_id: "snap-1",
    snapshot_taken_at: "2026-07-16T00:00:00Z",
    last_event_sequence: 1,
    record_id: recordId,
    record: {
      title: "Title",
      display_title_zh: null,
      title_generation_status: "succeeded",
      title_generation_error_code: null,
      title_generation_error_message: null,
      reading_goal: "daily_reading",
      reading_variant: "intermediate_reading",
      created_at: "2026-07-16T00:00:00Z",
      source_type: "plain_text",
      source_metadata: {},
      generation,
      product_state: "readable_enhancing",
      readiness_state: "article_ready",
    },
    base: {
      base_id: baseId,
      content_sha256: "a".repeat(64),
      canonicalizer_version: "v1",
      builder_version: "v1",
      segmenter_version: "v1",
      text_length_utf16: 10,
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

describe("ReaderRecordWorkbenchSurface source navigation (display-only)", () => {
  it("does not pass onNavigateAgenticSource (Workbench remains display-only until canonical DOM adapter)", () => {
    panelPropsSpy.mockClear();
    render(<ReaderRecordWorkbenchSurface snapshot={minimalSnapshot()} />);
    expect(panelPropsSpy).toHaveBeenCalled();
    const props = panelPropsSpy.mock.calls[0]![0] as Record<string, unknown>;
    // Fail-closed: no navigation callback → Sources show without jump buttons.
    expect(props.onNavigateAgenticSource).toBeUndefined();
    expect(props).not.toHaveProperty("loadCurrentPageIdentity");
    expect(props).not.toHaveProperty("currentPageIdentity");
    expect(props).not.toHaveProperty("document");
    expect(props).not.toHaveProperty("domAdapter");
  });

  it("AiWorkspacePanel props exclude identity/DOM seams and navigation callback", () => {
    panelPropsSpy.mockClear();
    render(<ReaderRecordWorkbenchSurface snapshot={minimalSnapshot()} />);
    const props = panelPropsSpy.mock.calls[0]![0] as Record<string, unknown>;
    const keys = Object.keys(props);
    expect(keys).not.toContain("onNavigateAgenticSource");
    expect(keys).not.toContain("currentPageIdentity");
    expect(keys).not.toContain("loadCurrentPageIdentity");
    expect(keys).not.toContain("domAdapter");
    expect(keys).not.toContain("document");
    expect(keys).not.toContain("stableDocumentId");
  });
});
