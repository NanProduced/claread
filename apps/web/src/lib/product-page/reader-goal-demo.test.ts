import { describe, expect, it } from "vitest";

import {
  READING_GOAL_OPTIONS,
  READING_VARIANT_OPTIONS,
} from "@/lib/reading-defaults";
import { readerGoalDemoSections } from "./reader-goal-demo";

describe("readerGoalDemoSections reading-plan parity", () => {
  it("mirrors the current Reader goals, variants, and descriptions", () => {
    expect(readerGoalDemoSections.map((section) => section.id)).toEqual(
      READING_GOAL_OPTIONS.map((option) => option.value),
    );

    for (const section of readerGoalDemoSections) {
      const goalOption = READING_GOAL_OPTIONS.find(
        (option) => option.value === section.id,
      );
      expect(section.title).toBe(goalOption?.label);
      expect(section.description).toBe(goalOption?.description);
      expect(
        section.variants.map(({ id, label, description }) => ({
          id,
          label,
          description,
        })),
      ).toEqual(
        READING_VARIANT_OPTIONS[section.id].map(
          ({ value, label, description }) => ({
            id: value,
            label,
            description,
          }),
        ),
      );
    }
  });

  it("does not expose the retired academic goal", () => {
    expect(
      readerGoalDemoSections.some((section) => section.id === ("academic" as never)),
    ).toBe(false);
  });
});
