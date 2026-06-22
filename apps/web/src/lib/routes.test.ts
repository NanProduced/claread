import { describe, expect, it } from "vitest";

import {
  appReadingRecordRoute,
  appReaderPlateRoute,
  appReaderRoute,
  legacyAppReaderRoute,
} from "./routes";

describe("reader route helpers", () => {
  it("keeps the legacy reader workbench URL unchanged", () => {
    expect(legacyAppReaderRoute("record 1")).toBe("/app/reader/record%201");
    expect(appReaderRoute("record 1")).toBe(legacyAppReaderRoute("record 1"));
  });

  it("builds the reader-plate validation URL with and without a record id", () => {
    expect(appReaderPlateRoute()).toBe("/app/reader-plate");
    expect(appReaderPlateRoute("rec 1")).toBe("/app/reader-plate?record_id=rec%201");
  });

  it("builds the new reading-record product route", () => {
    expect(appReadingRecordRoute("rec 1")).toBe("/app/reader-record/rec%201");
  });
});
