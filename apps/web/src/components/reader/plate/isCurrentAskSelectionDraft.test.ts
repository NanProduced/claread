/** @vitest-environment node */
import { describe, expect, it } from "vitest";

import { isCurrentAskSelectionDraft } from "./ReaderRecordPlateSurface";

/**
 * Host-side Ask selection identity fence.
 *
 * Covers the race where snapshot base/generation advances while
 * `activeSelection` still holds a draft stamped for the previous identity
 * (clear runs in an effect one render later). The host must reject that
 * draft before it reaches useAskComposerContext — otherwise the composer
 * identity fence clears slots and immediately re-ingests the stale range.
 */
describe("isCurrentAskSelectionDraft", () => {
  const current = {
    recordId: "record-1",
    baseId: "base-1",
    generation: 2,
  };

  it("accepts a draft stamped for the current record/base/generation", () => {
    expect(
      isCurrentAskSelectionDraft(
        {
          record_id: "record-1",
          base_id: "base-1",
          generation: 2,
        },
        current,
      ),
    ).toBe(true);
  });

  it("rejects a draft whose generation lags the current snapshot", () => {
    // First render after generation advance: activeSelection still holds the
    // old draft; host fence must return null so the composer never re-ingests.
    expect(
      isCurrentAskSelectionDraft(
        {
          record_id: "record-1",
          base_id: "base-1",
          generation: 1,
        },
        current,
      ),
    ).toBe(false);
  });

  it("rejects a draft whose base_id lags the current snapshot", () => {
    expect(
      isCurrentAskSelectionDraft(
        {
          record_id: "record-1",
          base_id: "base-old",
          generation: 2,
        },
        current,
      ),
    ).toBe(false);
  });

  it("rejects a draft whose record_id does not match the current snapshot", () => {
    expect(
      isCurrentAskSelectionDraft(
        {
          record_id: "record-other",
          base_id: "base-1",
          generation: 2,
        },
        current,
      ),
    ).toBe(false);
  });
});