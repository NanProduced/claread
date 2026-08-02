import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

function readSource(relativePath: string): string {
  return readFileSync(resolve(process.cwd(), relativePath), "utf-8");
}

const FINAL_ROUTE = "src/app/(private)/app/reader/[recordId]/plate-page.tsx";
const FINAL_PAGE = "src/app/(private)/app/reader/[recordId]/page.tsx";
const PLATE_SURFACE =
  "src/components/reader/plate/ReaderRecordPlateSurface.tsx";

describe("canonical Reader route boundary", () => {
  it("mounts Plate directly and has no Workbench/surface switch", () => {
    const page = readSource(FINAL_PAGE);
    const route = readSource(FINAL_ROUTE);

    expect(page).toContain('from "./plate-page"');
    expect(route).toContain("ReaderRecordPlateSurface");
    expect(route).not.toContain("ReaderRecordWorkbenchSurface");
    expect(route).not.toContain("getReaderRecordSurfaceMode");
    expect(route).not.toContain("reader-record-surface-mode");
  });

  it("uses only canonical Reader BFF paths in the final page", () => {
    const route = readSource(FINAL_ROUTE);

    expect(route).toContain("/api/web/reader/records/");
    expect(route).not.toContain("/api/web/reader-plate");
    expect(route).not.toContain("/api/web/reader-record");
    expect(route).not.toContain("/api/web/reader-ask");
  });

  it("keeps the Plate main chain on record-nested assets and v2 Ask", () => {
    const surface = readSource(PLATE_SURFACE);

    expect(surface).toContain("AiWorkspacePanel");
    expect(surface).toContain("/api/web/reader/records/");
    expect(surface).not.toContain("recordScope=");
    expect(surface).not.toContain("/api/web/reader-record");
    expect(surface).not.toContain("/api/web/reader-plate");
    expect(surface).not.toContain("ReaderRecordWorkbenchSurface");
  });
});
