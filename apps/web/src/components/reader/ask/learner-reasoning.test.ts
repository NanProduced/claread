import { describe, expect, it } from "vitest";

import {
  EMPTY_LEARNER_REASONING_STATE,
  isLearnerReasoningSnapshotPayload,
  reduceLearnerReasoningSnapshot,
  settleLearnerReasoning,
  type LearnerReasoningSnapshotPayload,
  type LearnerReasoningState,
} from "./learner-reasoning";

const active = {
  messageId: "m1",
  threadId: "t1",
  turnRunId: "r1",
};

const SNAP_BASE = {
  execution_version: "reader_record_ask_agentic_v2",
  message_id: "m1",
  thread_id: "t1",
  turn_run_id: "r1",
  sequence: 1,
  revision: 1,
  generation_id: 0,
  stage: "analyzing",
  text: "正在梳理问题要点",
  policy_version: "learner_reasoning_v1",
} as const satisfies LearnerReasoningSnapshotPayload;

function snap(
  overrides: Partial<LearnerReasoningSnapshotPayload> = {}
): LearnerReasoningSnapshotPayload {
  return { ...SNAP_BASE, ...overrides };
}

/** Invalid wire shapes for negative type-guard tests only. */
function rawSnap(
  overrides: Record<string, unknown> = {}
): Record<string, unknown> {
  return { ...SNAP_BASE, ...overrides };
}

describe("learner-reasoning reducer", () => {
  it("replaces text instead of appending", () => {
    let state: LearnerReasoningState = EMPTY_LEARNER_REASONING_STATE;
    state = reduceLearnerReasoningSnapshot(state, snap({ sequence: 1 }), active);
    state = reduceLearnerReasoningSnapshot(
      state,
      snap({ sequence: 2, revision: 2, text: "结合证据核对结论", stage: "article" }),
      active
    );
    expect(state.text).toBe("结合证据核对结论");
    expect(state.text).not.toContain("正在梳理");
    expect(state.sequence).toBe(2);
  });

  it("requires activeRunIdentity", () => {
    const state = reduceLearnerReasoningSnapshot(
      EMPTY_LEARNER_REASONING_STATE,
      snap(),
      null
    );
    expect(state.text).toBeNull();
  });

  it("drops foreign identity frames", () => {
    const state = reduceLearnerReasoningSnapshot(
      EMPTY_LEARNER_REASONING_STATE,
      snap({ message_id: "other" }),
      active
    );
    expect(state.text).toBeNull();
  });

  it("drops duplicate and out-of-order sequences", () => {
    const state = reduceLearnerReasoningSnapshot(
      EMPTY_LEARNER_REASONING_STATE,
      snap({ sequence: 2, revision: 2 }),
      active
    );
    const afterDup = reduceLearnerReasoningSnapshot(
      state,
      snap({ sequence: 2, revision: 3, text: "重复序号摘要文本" }),
      active
    );
    expect(afterDup.text).toBe(state.text);
    const afterOld = reduceLearnerReasoningSnapshot(
      state,
      snap({ sequence: 1, revision: 1, text: "乱序旧帧摘要文本" }),
      active
    );
    expect(afterOld.text).toBe(state.text);
  });

  it("rejects frames after settle", () => {
    let state = reduceLearnerReasoningSnapshot(
      EMPTY_LEARNER_REASONING_STATE,
      snap(),
      active
    );
    state = settleLearnerReasoning(state);
    const late = reduceLearnerReasoningSnapshot(
      state,
      snap({ sequence: 3, revision: 3, text: "迟到的摘要内容啊" }),
      active
    );
    expect(late.text).toBe("正在梳理问题要点");
    expect(late.status).toBe("completed");
  });

  it("rejects evil payloads", () => {
    expect(
      isLearnerReasoningSnapshotPayload(
        rawSnap({ text: "见 https://evil.example" })
      )
    ).toBe(false);
    expect(
      isLearnerReasoningSnapshotPayload(
        rawSnap({ text: "Bearer abcdefghijklmnop 摘要" })
      )
    ).toBe(false);
    expect(
      isLearnerReasoningSnapshotPayload(
        rawSnap({ text: "[点击](/api/private)继续" })
      )
    ).toBe(false);
    expect(
      isLearnerReasoningSnapshotPayload(rawSnap({ text: "<b>注入</b>内容" }))
    ).toBe(false);
    expect(
      isLearnerReasoningSnapshotPayload(rawSnap({ stage: "hacking" }))
    ).toBe(false);
    expect(
      isLearnerReasoningSnapshotPayload(
        rawSnap({ policy_version: "reasoning_projection_v1" })
      )
    ).toBe(false);
  });

  it("requires non-negative integer generation_id", () => {
    expect(isLearnerReasoningSnapshotPayload(snap({ generation_id: 0 }))).toBe(
      true
    );
    expect(
      isLearnerReasoningSnapshotPayload(rawSnap({ generation_id: -1 }))
    ).toBe(false);
    expect(
      isLearnerReasoningSnapshotPayload(rawSnap({ generation_id: 1.5 }))
    ).toBe(false);
    const missing = rawSnap();
    delete missing.generation_id;
    expect(isLearnerReasoningSnapshotPayload(missing)).toBe(false);
  });
});
