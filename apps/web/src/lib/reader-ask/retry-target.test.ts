import { describe, expect, it } from "vitest";

import { classifyRetryTarget } from "./retry-target";

describe("classifyRetryTarget", () => {
  it("classifies UUID assistants as regenerate targets", () => {
    const target = classifyRetryTarget(
      "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
    );
    expect(target).toEqual({
      kind: "persisted_assistant",
      assistantMessageId: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
      ctaLabel: "重新生成",
      ctaAction: "retry",
    });
  });

  it("classifies local-assistant as resend targets", () => {
    const target = classifyRetryTarget("local-assistant-1710000000");
    expect(target).toEqual({
      kind: "pending_submission",
      localAssistantId: "local-assistant-1710000000",
      ctaLabel: "重新发送",
      ctaAction: "resend",
    });
  });

  it("never treats local-user as a regenerate target", () => {
    expect(classifyRetryTarget("local-user-1")).toBeNull();
  });

  it("fails closed on non-UUID non-local ids (msg-assistant-1)", () => {
    expect(classifyRetryTarget("msg-assistant-1")).toBeNull();
    expect(classifyRetryTarget("")).toBeNull();
    expect(classifyRetryTarget("not-a-uuid")).toBeNull();
  });
});
