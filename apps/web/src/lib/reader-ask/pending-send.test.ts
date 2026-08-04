import { describe, expect, it } from "vitest";

import {
  clearPendingSendKeys,
  messageMatchesActiveAssistant,
  rekeyPendingSend,
  resolveActiveAssistantId,
  shouldOfferResendNotRetry,
} from "./pending-send";
import {
  classifyRetryTarget,
  classifyRetryTargetForRecovery,
  type PendingSendRequest,
} from "./retry-target";

const THREAD = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeee1";
const CLIENT_SUB = "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff";
const CANONICAL = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeee3";
const LOCAL = "local-assistant-1";

function makePending(localAssistantId: string): PendingSendRequest {
  return {
    content: "q",
    attachments: [],
    entryAction: null as unknown as PendingSendRequest["entryAction"],
    model: null,
    webSearchMode: "disabled",
    clientSubmissionId: CLIENT_SUB,
    localUserId: "local-user-1",
    localAssistantId,
    threadId: THREAD,
  };
}

describe("pending rekey + recovery target", () => {
  it("resolveActiveAssistantId prefers streaming UUID", () => {
    expect(resolveActiveAssistantId(CANONICAL, LOCAL)).toBe(CANONICAL);
    expect(resolveActiveAssistantId(null, LOCAL)).toBe(LOCAL);
  });

  it("rekeyPendingSend moves entry local→UUID without drop", () => {
    const map = new Map<string, PendingSendRequest>();
    map.set(LOCAL, makePending(LOCAL));
    const next = rekeyPendingSend(map, LOCAL, CANONICAL);
    expect(next?.clientSubmissionId).toBe(CLIENT_SUB);
    expect(map.has(LOCAL)).toBe(false);
    expect(map.has(CANONICAL)).toBe(true);
    expect(map.get(CANONICAL)?.localAssistantId).toBe(CANONICAL);
    expect(map.get(CANONICAL)?.clientSubmissionId).toBe(CLIENT_SUB);
  });

  it("clearPendingSendKeys removes temp + active + canonical", () => {
    const map = new Map<string, PendingSendRequest>();
    map.set(CANONICAL, makePending(CANONICAL));
    clearPendingSendKeys(map, LOCAL, CANONICAL, null);
    expect(map.size).toBe(0);
  });

  it("UUID with open pending → resend, not /retry", () => {
    const withPending = classifyRetryTargetForRecovery(CANONICAL, true);
    expect(withPending?.kind).toBe("pending_submission");
    expect(withPending?.ctaAction).toBe("resend");

    const without = classifyRetryTargetForRecovery(CANONICAL, false);
    expect(without?.kind).toBe("persisted_assistant");
    expect(without?.ctaAction).toBe("retry");
  });

  it("messageMatchesActiveAssistant covers rekeyed bubble", () => {
    expect(messageMatchesActiveAssistant(CANONICAL, CANONICAL, LOCAL)).toBe(
      true,
    );
    expect(messageMatchesActiveAssistant(LOCAL, CANONICAL, LOCAL)).toBe(true);
    expect(
      messageMatchesActiveAssistant("other-id", CANONICAL, LOCAL),
    ).toBe(false);
  });

  it("shouldOfferResendNotRetry true when pending open on UUID", () => {
    expect(shouldOfferResendNotRetry(CANONICAL, true)).toBe(true);
    expect(shouldOfferResendNotRetry(CANONICAL, false)).toBe(false);
    expect(shouldOfferResendNotRetry(LOCAL, false)).toBe(true);
  });

  it("base classify still treats bare UUID as regenerate", () => {
    expect(classifyRetryTarget(CANONICAL)?.ctaAction).toBe("retry");
  });
});
