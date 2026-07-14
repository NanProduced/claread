import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

function readSource(relativePath: string): string {
  return readFileSync(resolve(process.cwd(), relativePath), "utf-8");
}

describe("entry source matrix - Track D convergence", () => {
  it("AppShell only mounts the merged Reading Record activity indicator", () => {
    const source = readSource("src/components/layout/app-shell/index.tsx");

    expect(source).toContain("ReadingRecordActivityIndicator");
    expect(source).not.toContain("ActiveAnalysisTaskIndicator");
  });

  it("command palette uses Reading Record list as the only recent/search source", () => {
    const dialogSource = readSource(
      "src/components/layout/command-palette/CommandPaletteDialog.tsx",
    );
    const itemsSource = readSource(
      "src/components/layout/command-palette/command-palette-items.ts",
    );

    expect(dialogSource).toContain("/api/web/reading-records");
    expect(dialogSource).toContain("readerUrl");
    expect(dialogSource).not.toContain("/api/web/command-palette/records");
    expect(dialogSource).not.toContain("ReadingRecordCommandGroup");
    expect(dialogSource).not.toContain("legacyAppReaderRoute");

    expect(itemsSource).toContain("lastReaderUrl");
    expect(itemsSource).not.toContain("legacyAppReaderRoute");
  });

  it("Library is new Reading Record only", () => {
    const sectionSource = readSource(
      "src/app/(private)/app/library/ReadingRecordSection.tsx",
    );
    const clientSource = readSource(
      "src/app/(private)/app/library/LibraryClient.tsx",
    );

    expect(sectionSource).not.toContain("legacyAppReaderRoute");
    expect(sectionSource).not.toContain("/app/reader/");

    expect(clientSource).toContain("ReadingRecordSection");
    expect(clientSource).not.toContain("Legacy Records");
    expect(clientSource).not.toContain("legacyAppReaderRoute");
    expect(clientSource).not.toContain("/app/reader/");
  });

  it("Vocabulary source links prefer Reading Record ids with legacy fallback", () => {
    const clientSource = readSource(
      "src/app/(private)/app/vocabulary/VocabularyClient.tsx",
    );
    const bffSource = readSource("src/services/bff/vocabulary.ts");

    expect(clientSource).toContain("sourceReadingRecordId");
    expect(clientSource).toContain("appReadingRecordRoute");
    expect(clientSource).toContain("legacyAppReaderRoute");
    expect(clientSource).toContain("sourceHrefForItem");

    expect(bffSource).toContain("sourceReadingRecordId");
    expect(bffSource).toContain("reading_record_id");
    expect(bffSource).toContain("sourceRecordId");
    expect(bffSource).not.toContain("sourceReaderUrl");
  });

  it("read page submit flow is new-only and free of legacy analysis-task recovery", () => {
    const formSource = readSource(
      "src/app/(private)/app/read/AnalyzeSubmitForm.tsx",
    );
    const submitModeSource = readSource(
      "src/app/(private)/app/read/submit-mode.ts",
    );

    expect(formSource).not.toContain("fetchCurrentAnalysisTask");
    expect(formSource).not.toContain("fetchAnalysisTaskStatus");
    expect(formSource).not.toContain("legacyAppReaderRoute");
    expect(formSource).not.toContain("saveRecentReadingRecordForSubmitMode");
    expect(formSource).not.toContain("readRecentReadingRecord");
    expect(formSource).not.toContain("saveRecentReadingRecord");
    expect(formSource).not.toContain("intakeMethods");
    expect(formSource).not.toContain("上传图片");

    expect(submitModeSource).toContain('export type ReadPageSubmitMode = "reader-plate-input"');
    expect(submitModeSource).toContain("/api/web/reader-plate/input");
    expect(submitModeSource).not.toContain("/api/web/analysis/submit");
    expect(submitModeSource).not.toContain("/api/web/reading-record/submit");
  });

  it("the merged activity indicator stays legacy-only (new chain has no fixed overlay)", () => {
    const source = readSource(
      "src/components/layout/reading-record-activity-indicator.tsx",
    );

    expect(source).not.toContain("/api/web/reading-records");
    expect(source).not.toContain("productState");
    expect(source).toContain("fetchCurrentAnalysisTask");
    expect(source).toContain("fetchAnalysisTaskStatus");
    expect(source).toContain("legacyAppReaderRoute");
    expect(source).toContain("旧任务");
  });

  it("the Reading Record list BFF stays free of legacy reader routing", () => {
    const source = readSource("src/services/bff/reading-records.ts");

    expect(source).toContain("appReadingRecordRoute");
    expect(source).not.toContain("legacyAppReaderRoute");
    expect(source).not.toContain("/app/reader/");
    expect(source).not.toContain("analysis-tasks");
  });
});
