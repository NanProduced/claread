import { describe, expect, it } from "vitest";
import { getLookupSaveState, getSaveActionCopy } from "./lookupSaveState";

describe("lookupSaveState", () => {
  it("returns not_saved when no matched vocabulary entry exists", () => {
    expect(getLookupSaveState(false, "s1", [])).toBe("not_saved");
    expect(getSaveActionCopy("not_saved")).toBe("加入生词本");
  });

  it("returns a neutral copy for unknown lookup states", () => {
    expect(getSaveActionCopy("unknown")).toBe("检查生词本");
  });

  it("returns already_saved_here when the current sentence is already in saved source refs", () => {
    expect(
      getLookupSaveState(true, "s1", [
        { source_sentence_id: "s1" },
      ]),
    ).toBe("already_saved_here");
    expect(getSaveActionCopy("already_saved_here")).toBe("已加入");
  });

  it("returns same_lemma_new_context for a new sentence on the same lemma", () => {
    expect(
      getLookupSaveState(true, "s2", [
        { source_sentence_id: "s1" },
      ]),
    ).toBe("same_lemma_new_context");
    expect(getSaveActionCopy("same_lemma_new_context")).toBe("加入当前语境");
  });

  it("returns multiple_contexts when the lemma already has more than one saved source", () => {
    expect(
      getLookupSaveState(true, "s9", [
        { source_sentence_id: "s1" },
        { source_sentence_id: "s2" },
      ]),
    ).toBe("multiple_contexts");
    expect(getSaveActionCopy("multiple_contexts", 2)).toBe("已加入 · 2个语境");
  });
});
