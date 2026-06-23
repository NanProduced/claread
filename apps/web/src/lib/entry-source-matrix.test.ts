import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

/**
 * W3-D3 Entry Source Matrix static guards.
 *
 * These tests lock down the current cutover boundary so that legacy record id
 * surfaces do not silently start emitting new Reading Record routes (or vice
 * versa) before the corresponding data source has been migrated.
 *
 * The matrix is documented in
 * docs/initiatives/reader-agentic-orchestration/modules/cutover-and-old-workflow.md.
 * Update that doc together with these guards when an entry point is migrated.
 */

function readSource(relativePath: string): string {
  return readFileSync(resolve(process.cwd(), relativePath), "utf-8");
}

const LEGACY_ROUTE_HELPER = "legacyAppReaderRoute";
const NEW_ROUTE_HELPER = "appReadingRecordRoute";
const LEGACY_READER_PATH = "/app/reader/";
const NEW_READER_RECORD_PATH = "/app/reader-record/";
const ANALYSIS_TASKS_WIRING = "analysis-tasks";

describe("entry source matrix - legacy surfaces must not reference the new Reading Record route", () => {
  it("active-analysis-task-indicator only uses the legacy reader route helper", () => {
    const source = readSource(
      "src/components/layout/active-analysis-task-indicator.tsx",
    );

    expect(source).toContain(LEGACY_ROUTE_HELPER);
    expect(source).not.toContain(NEW_ROUTE_HELPER);
  });

  it("command palette dialog only uses the legacy reader route helper", () => {
    const source = readSource(
      "src/components/layout/command-palette/CommandPaletteDialog.tsx",
    );

    expect(source).toContain(LEGACY_ROUTE_HELPER);
    expect(source).not.toContain(NEW_ROUTE_HELPER);
  });

  it("command palette items only use the legacy reader route helper", () => {
    const source = readSource(
      "src/components/layout/command-palette/command-palette-items.ts",
    );

    expect(source).toContain(LEGACY_ROUTE_HELPER);
    expect(source).not.toContain(NEW_ROUTE_HELPER);
  });

  it("Library record links only use the legacy reader route helper", () => {
    const source = readSource(
      "src/app/(private)/app/library/LibraryClient.tsx",
    );

    expect(source).toContain(LEGACY_ROUTE_HELPER);
    expect(source).not.toContain(NEW_ROUTE_HELPER);
  });

  it("Vocabulary source links only use the legacy reader route helper", () => {
    const source = readSource(
      "src/app/(private)/app/vocabulary/VocabularyClient.tsx",
    );

    expect(source).toContain(LEGACY_ROUTE_HELPER);
    expect(source).not.toContain(NEW_ROUTE_HELPER);
  });

  it("services/bff/analysis.ts readerUrl projection only uses the legacy reader route helper", () => {
    const source = readSource("src/services/bff/analysis.ts");

    expect(source).toContain(LEGACY_ROUTE_HELPER);
    expect(source).not.toContain(NEW_ROUTE_HELPER);
  });

  it("services/bff/records.ts does not project new Reading Record routes", () => {
    const source = readSource("src/services/bff/records.ts");

    expect(source).not.toContain(NEW_ROUTE_HELPER);
    expect(source).not.toContain(NEW_READER_RECORD_PATH);
  });
});

describe("entry source matrix - the new Reading Record recovery entry must not reference legacy wiring", () => {
  it("recent-reading-record helper is free of legacy reader route, legacy path and analysis-tasks wiring", () => {
    const source = readSource(
      "src/app/(private)/app/read/recent-reading-record.ts",
    );

    expect(source).not.toContain(LEGACY_ROUTE_HELPER);
    expect(source).not.toContain(LEGACY_READER_PATH);
    expect(source).not.toContain(ANALYSIS_TASKS_WIRING);
    expect(source).toContain(NEW_READER_RECORD_PATH);
  });
});

describe("entry source matrix - the new Reading Record list BFF source", () => {
  it("reading-records BFF uses the new Reading Record route helper and is free of legacy wiring", () => {
    const source = readSource("src/services/bff/reading-records.ts");

    expect(source).toContain(NEW_ROUTE_HELPER);
    expect(source).not.toContain(LEGACY_ROUTE_HELPER);
    expect(source).not.toContain(LEGACY_READER_PATH);
    expect(source).not.toContain(ANALYSIS_TASKS_WIRING);
  });
});

describe("entry source matrix - Library new Reading Record section (W3-D5)", () => {
  it("ReadingRecordSection is free of legacy reader route, legacy path and analysis-tasks wiring", () => {
    const source = readSource(
      "src/app/(private)/app/library/ReadingRecordSection.tsx",
    );

    expect(source).not.toContain(LEGACY_ROUTE_HELPER);
    expect(source).not.toContain(LEGACY_READER_PATH);
    expect(source).not.toContain(ANALYSIS_TASKS_WIRING);
  });

  it("Library old record list still uses the legacy reader route helper", () => {
    const source = readSource(
      "src/app/(private)/app/library/LibraryClient.tsx",
    );

    expect(source).toContain(LEGACY_ROUTE_HELPER);
  });
});
