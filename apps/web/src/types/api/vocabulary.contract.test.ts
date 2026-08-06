import { describe, expect, it } from "vitest";

import type { VocabularyMasteryStatusDto } from "./vocabulary";

/**
 * The vocabulary mastery-status value domain has the PostgreSQL CHECK
 * (vocabulary_book_mastery_status_check) as its single source of truth:
 * new / learning / review / mastered / archived.
 *
 * The positive assertion proves all five values are assignable. Each
 * negative case is a `@ts-expect-error` enforced by `tsc --noEmit`: if
 * `reviewing` or a trailing `string` escape hatch ever returns, the
 * assignment stops erroring, the directive becomes unused, and typecheck
 * fails — the drift cannot be reintroduced silently. This mirrors the
 * OutlineItem role-contract guard in reader-outline-view.test.ts.
 */
describe("VocabularyMasteryStatusDto value domain", () => {
  it("assigns every value in the DB check domain", () => {
    const values: VocabularyMasteryStatusDto[] = [
      "new",
      "learning",
      "review",
      "mastered",
      "archived",
    ];
    expect(values).toHaveLength(5);
  });

  it("rejects the legacy reviewing drift value at the type level", () => {
    // @ts-expect-error reviewing is not in the DB-authoritative mastery domain
    const drift: VocabularyMasteryStatusDto = "reviewing";
    expect(drift).toBe("reviewing");
  });

  it("rejects unknown strings because the union is closed", () => {
    // @ts-expect-error arbitrary strings are not assignable to the closed union
    const unknown: VocabularyMasteryStatusDto = "not-a-mastery-status";
    expect(unknown).toBe("not-a-mastery-status");
  });
});
