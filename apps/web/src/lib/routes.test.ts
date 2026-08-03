import { describe, expect, it } from "vitest";

import {
  appReadResumeCandidateRoute,
  appReaderRoute,
  isAppReaderPath,
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

  it("builds the canonical Reader URL with an encoded record id", () => {
    expect(appReaderRoute("record 1")).toBe("/app/reader/record%201");
  });

  it("identifies only the canonical Reader route", () => {
    expect(isAppReaderPath("/app/reader/rec_1")).toBe(true);
    expect(isAppReaderPath("/app/reader")).toBe(true);
    expect(isAppReaderPath("/app/reader-record/rec_1")).toBe(false);
    expect(isAppReaderPath("/app/reader-plate")).toBe(false);
    expect(isAppReaderPath("/app/read")).toBe(false);
  });
});
