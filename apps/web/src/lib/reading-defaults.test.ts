import { describe, expect, it } from "vitest";

import {
  DEFAULT_READING_DEFAULTS,
  READING_GOAL_OPTIONS,
  READING_VARIANT_OPTIONS,
  formatReadingPlanSummary,
  normalizeReadingDefaults,
  readReadingDefaultsFromSettings,
} from "./reading-defaults";

describe("reading plan contract", () => {
  it("exposes only Reader Orchestration goals and variants", () => {
    expect(READING_GOAL_OPTIONS.map((option) => option.value)).toEqual([
      "daily_reading",
      "exam",
    ]);
    expect(
      Object.values(READING_VARIANT_OPTIONS)
        .flat()
        .map((option) => option.value),
    ).not.toContain("academic_general");
  });

  it("normalizes retired academic defaults to the broad daily default", () => {
    expect(
      normalizeReadingDefaults({
        readingGoal: "academic" as never,
        readingVariant: "academic_general" as never,
      }),
    ).toEqual(DEFAULT_READING_DEFAULTS);

    expect(
      readReadingDefaultsFromSettings({
        default_reading_goal: "academic",
        default_reading_variant: "academic_general",
      }),
    ).toEqual(DEFAULT_READING_DEFAULTS);
  });

  it("formats one shared user-facing summary", () => {
    expect(formatReadingPlanSummary("daily_reading", "intermediate_reading")).toBe(
      "日常阅读 · 进阶",
    );
    expect(formatReadingPlanSummary("exam", "cet")).toBe("备考精读 · 四六级");
  });
});
