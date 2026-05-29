import { describe, expect, it } from "vitest";
import type { RecordListItemVm } from "@/types/view/RecordListItemVm";
import {
  countLibraryGoals,
  filterLibraryRecords,
  filterRecordsByFavorite,
  filterRecordsByText,
  findMostRecentRecord,
  normalizeLibraryQuery,
  sortLibraryRecords,
} from "./search";

const records: RecordListItemVm[] = [
  {
    id: "1",
    title: "Academic Climate Notes",
    sourceText: "A full source text about climate policy and research.",
    sourceTextExcerpt: "climate policy and research",
    sourceType: "user_input",
    readingGoal: "academic",
    readingVariant: "academic_general",
    analysisStatus: "ready",
    lastOpenedAt: "2026-05-28T10:00:00Z",
    createdAt: "2026-05-27T10:00:00Z",
    updatedAt: "2026-05-28T10:00:00Z",
    wordCount: 100,
    noteCount: 1,
    vocabularyCount: 1,
    isFavorited: true,
  },
  {
    id: "2",
    title: "Exam Strategy",
    sourceText: "Full exam prep source with gaokao tactics.",
    sourceTextExcerpt: "",
    sourceType: "imported",
    readingGoal: "exam",
    readingVariant: "gaokao",
    analysisStatus: "ready",
    lastOpenedAt: null,
    createdAt: "2026-05-29T10:00:00Z",
    updatedAt: "2026-05-29T10:00:00Z",
    wordCount: 120,
    noteCount: 0,
    vocabularyCount: 0,
    isFavorited: false,
  },
  {
    id: "3",
    title: "Daily News",
    sourceText: "Everyday reading for commuters.",
    sourceTextExcerpt: "commuter digest",
    sourceType: "daily_article",
    readingGoal: "daily_reading",
    readingVariant: "intermediate_reading",
    analysisStatus: "ready",
    lastOpenedAt: "2026-05-20T10:00:00Z",
    createdAt: "2026-05-19T10:00:00Z",
    updatedAt: "2026-05-20T10:00:00Z",
    wordCount: 90,
    noteCount: 0,
    vocabularyCount: 0,
    isFavorited: false,
  },
];

describe("library search helpers", () => {
  it("normalizes query by trimming and lowering case", () => {
    expect(normalizeLibraryQuery("  AcAdEmIc  ")).toBe("academic");
  });

  it("matches titles", () => {
    const result = filterRecordsByText(records, "strategy");
    expect(result.map((record) => record.id)).toEqual(["2"]);
  });

  it("prefers excerpt and falls back to source text when excerpt is missing", () => {
    expect(filterRecordsByText(records, "commuter").map((record) => record.id)).toEqual(["3"]);
    expect(filterRecordsByText(records, "gaokao").map((record) => record.id)).toEqual(["2"]);
  });

  it("matches goal and variant labels", () => {
    expect(filterRecordsByText(records, "学术").map((record) => record.id)).toEqual(["1"]);
    expect(filterRecordsByText(records, "高考").map((record) => record.id)).toEqual(["2"]);
  });

  it("combines query and goal filter", () => {
    const result = filterLibraryRecords(records, {
      normalizedQuery: "reading",
      favoriteFilter: "all",
      goalFilter: "daily_reading",
      sortOption: "last_opened",
    });

    expect(result.map((record) => record.id)).toEqual(["3"]);
  });

  it("filters favorited records", () => {
    expect(filterRecordsByFavorite(records, "favorited").map((record) => record.id)).toEqual(["1"]);
  });

  it("counts goal buckets from the current text result set", () => {
    const textMatched = filterRecordsByText(records, "reading");
    expect(countLibraryGoals(textMatched)).toEqual({
      all: 1,
      daily_reading: 1,
      exam: 0,
      academic: 0,
    });
  });

  it("finds the most recent record by last opened or created time", () => {
    expect(findMostRecentRecord(records)?.id).toBe("2");
  });

  it("sorts by created time when requested", () => {
    expect(sortLibraryRecords(records, "created_at").map((record) => record.id)).toEqual([
      "2",
      "1",
      "3",
    ]);
  });
});
