import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

function readSource(relativePath: string): string {
  return readFileSync(resolve(process.cwd(), relativePath), "utf-8");
}

describe("Web cutover entry source matrix", () => {
  it("uses the canonical records BFF for recent/search entries", () => {
    const dialogSource = readSource(
      "src/components/layout/command-palette/CommandPaletteDialog.tsx",
    );
    const recentSource = readSource(
      "src/components/layout/recent-reading-context.tsx",
    );

    expect(dialogSource).toContain("/api/web/reader/records");
    expect(recentSource).toContain("/api/web/reader/records");
    expect(dialogSource).not.toContain("/api/web/reading-records");
    expect(recentSource).not.toContain("/api/web/reading-records");
  });

  it("uses only the canonical Reader route in Library and vocabulary", () => {
    const sectionSource = readSource(
      "src/app/(private)/app/library/ReadingRecordSection.tsx",
    );
    const clientSource = readSource(
      "src/app/(private)/app/vocabulary/VocabularyClient.tsx",
    );

    expect(sectionSource).toContain("item.readerUrl");
    expect(sectionSource).not.toContain("/app/reader-record/");
    expect(clientSource).toContain("appReaderRoute");
    expect(clientSource).not.toContain("appReadingRecordRoute");
    expect(clientSource).not.toContain("legacyAppReaderRoute");
    expect(clientSource).not.toContain("sourceRecordId ??");
  });

  it("keeps the read submit flow on canonical Reader BFF and route", () => {
    const formSource = readSource(
      "src/app/(private)/app/read/AnalyzeSubmitForm.tsx",
    );
    const submitModeSource = readSource(
      "src/app/(private)/app/read/submit-mode.ts",
    );

    expect(formSource).toContain("appReaderRoute");
    expect(formSource).not.toContain("/api/web/reader-plate");
    expect(submitModeSource).toContain("/api/web/reader/records/input");
    expect(submitModeSource).not.toContain("/api/web/reader-plate");
    expect(submitModeSource).not.toContain("/api/web/analysis/submit");
  });

  it("keeps the Reading Record list BFF free of legacy routing", () => {
    const source = readSource("src/services/bff/reading-records.ts");

    expect(source).toContain("appReaderRoute");
    expect(source).not.toContain("appReadingRecordRoute");
    expect(source).not.toContain("legacyAppReaderRoute");
    expect(source).not.toContain("analysis-tasks");
  });
});
