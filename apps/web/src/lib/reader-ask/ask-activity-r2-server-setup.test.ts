import { describe, expect, it } from "vitest";

import {
  ASK_ACTIVITY_R2_DIST_INCLUDE_ENTRIES,
  CleanupConflictError,
  mergeRunnerOwnedNextEnv,
  mergeRunnerOwnedTsconfigInclude,
} from "../../../tests/e2e/ask-activity-r2-server-setup";

const nextEnvBaseline = `/// <reference types="next" />
/// <reference types="next/image-types/global" />
import "./.next/dev/types/routes.d.ts";

// NOTE: This file should not be edited
`;

const tsconfigBaseline = `{
  "compilerOptions": {
    "strict": true
  },
  "include": [
    "next-env.d.ts",
    "**/*.ts",
    ".next-e2e-gate-test/dev/types/**/*.ts"
  ]
}
`;

describe("ask-activity-r2 cleanup merge", () => {
  it("removes only this runner's new dist includes and keeps concurrent fields/includes", () => {
    const current = `{
  "compilerOptions": {
    "strict": true,
    "noUncheckedIndexedAccess": true
  },
  "include": [
    "next-env.d.ts",
    "**/*.ts",
    ".next-e2e-gate-test/dev/types/**/*.ts",
    ".next-e2e-spike-test/dev/types/**/*.ts",
    "${ASK_ACTIVITY_R2_DIST_INCLUDE_ENTRIES[0]}",
    "${ASK_ACTIVITY_R2_DIST_INCLUDE_ENTRIES[1]}"
  ]
}
`;

    const merged = mergeRunnerOwnedTsconfigInclude(tsconfigBaseline, current);
    const parsed = JSON.parse(merged) as {
      compilerOptions: Record<string, unknown>;
      include: string[];
    };

    expect(parsed.compilerOptions.noUncheckedIndexedAccess).toBe(true);
    expect(parsed.include).toContain(".next-e2e-gate-test/dev/types/**/*.ts");
    expect(parsed.include).toContain(".next-e2e-spike-test/dev/types/**/*.ts");
    expect(parsed.include).not.toEqual(
      expect.arrayContaining([...ASK_ACTIVITY_R2_DIST_INCLUDE_ENTRIES]),
    );
  });

  it("does not remove a runner include that already existed in the baseline", () => {
    const baseline = `{
  "include": [
    "next-env.d.ts",
    "${ASK_ACTIVITY_R2_DIST_INCLUDE_ENTRIES[0]}"
  ]
}
`;
    const current = `{
  "include": [
    "next-env.d.ts",
    "${ASK_ACTIVITY_R2_DIST_INCLUDE_ENTRIES[0]}",
    ".next-e2e-gate-test/dev/types/**/*.ts"
  ]
}
`;

    expect(mergeRunnerOwnedTsconfigInclude(baseline, current)).toBe(current);
  });

  it("is idempotent after its own include entries are removed", () => {
    const current = `{
  "include": [
    "next-env.d.ts",
    "${ASK_ACTIVITY_R2_DIST_INCLUDE_ENTRIES[0]}",
    "${ASK_ACTIVITY_R2_DIST_INCLUDE_ENTRIES[1]}"
  ]
}
`;

    const baseline = "{\n  \"include\": []\n}\n";
    const once = mergeRunnerOwnedTsconfigInclude(baseline, current);
    expect(mergeRunnerOwnedTsconfigInclude(baseline, once)).toBe(once);
  });

  it("restores next-env only for an exact own generated replacement", () => {
    const generated = nextEnvBaseline.replace(
      './.next/dev/types/routes.d.ts',
      './.next-e2e-ask-activity-r2-test/dev/types/routes.d.ts',
    );

    const restored = mergeRunnerOwnedNextEnv(nextEnvBaseline, generated);
    expect(restored).toBe(nextEnvBaseline);
    expect(mergeRunnerOwnedNextEnv(nextEnvBaseline, restored)).toBe(restored);
  });

  it("fails closed when next-env contains an own replacement plus a concurrent change", () => {
    const generatedWithConcurrentChange = `${nextEnvBaseline.replace(
      './.next/dev/types/routes.d.ts',
      './.next-e2e-ask-activity-r2-test/dev/types/routes.d.ts',
    )}// concurrent runner change\n`;

    expect(() => mergeRunnerOwnedNextEnv(nextEnvBaseline, generatedWithConcurrentChange)).toThrow(
      CleanupConflictError,
    );
  });

  it("preserves a concurrent next-env change that contains no own generated reference", () => {
    const concurrent = `${nextEnvBaseline}// concurrent runner change\n`;
    expect(mergeRunnerOwnedNextEnv(nextEnvBaseline, concurrent)).toBe(concurrent);
  });
});
