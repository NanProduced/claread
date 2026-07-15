import { describe, expect, it } from "vitest";

import {
  appReadResumeCandidateRoute,
  appReadingRecordRoute,
  appReaderPlateRoute,
  appReaderRoute,
  isAppReaderPlatePath,
  isAppReadingRecordPath,
  legacyAppReaderRoute,
} from "./routes";

describe("reader route helpers", () => {
  it("builds the candidate resume URL with an encoded record id", () => {
    expect(appReadResumeCandidateRoute("record 1")).toBe(
      "/app/read?resume_candidate=record%201",
    );
    expect(appReadResumeCandidateRoute("record/a?b")).toBe(
      "/app/read?resume_candidate=record%2Fa%3Fb",
    );
  });

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

  it("keeps legacy analysis record routes and new Reading Record routes disjoint", () => {
    const readingRecordId = "reading record 1";

    expect(appReadingRecordRoute(readingRecordId)).toBe(
      "/app/reader-record/reading%20record%201",
    );
    expect(legacyAppReaderRoute(readingRecordId)).toBe(
      "/app/reader/reading%20record%201",
    );
    expect(appReadingRecordRoute(readingRecordId)).not.toBe(
      legacyAppReaderRoute(readingRecordId),
    );
  });

  it("identifies Reading Record and reader-plate app routes for shell gating", () => {
    expect(isAppReadingRecordPath("/app/reader-record/rec_1")).toBe(true);
    expect(isAppReaderPlatePath("/app/reader-plate")).toBe(true);
    expect(isAppReaderPlatePath("/app/reader-plate?record_id=rec_1")).toBe(
      true,
    );
    expect(isAppReadingRecordPath("/app/reader/rec_1")).toBe(false);
    expect(isAppReaderPlatePath("/app/read")).toBe(false);
  });
});
