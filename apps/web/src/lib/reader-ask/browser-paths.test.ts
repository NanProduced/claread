import { describe, expect, it } from "vitest";

import {
  browserAskRetryPath,
  browserAskStreamPath,
  browserAskSubmissionPath,
  isLocalOptimisticMessageId,
  isPersistedAssistantMessageId,
} from "./browser-paths";

describe("browser Ask paths (ASK-RETRY-CONTRACT-R0/R4)", () => {
  const recordId = "record-1";

  it("builds stream path without upstream suffixes", () => {
    expect(browserAskStreamPath(recordId, "thread-1")).toBe(
      "/api/web/reader/records/record-1/ask/threads/thread-1/messages/stream",
    );
  });

  it("builds retry path as /retry only — never /retry/stream", () => {
    const path = browserAskRetryPath(
      recordId,
      "thread-1",
      "11111111-1111-4111-8111-111111111111",
    );
    expect(path).toBe(
      "/api/web/reader/records/record-1/ask/threads/thread-1/messages/11111111-1111-4111-8111-111111111111/retry",
    );
    expect(path).not.toContain("/retry/stream");
    expect(path.endsWith("/retry")).toBe(true);
  });

  it("builds submission reconcile path", () => {
    expect(
      browserAskSubmissionPath(
        recordId,
        "thread-1",
        "22222222-2222-4222-8222-222222222222",
      ),
    ).toBe(
      "/api/web/reader/records/record-1/ask/threads/thread-1/submissions/22222222-2222-4222-8222-222222222222",
    );
  });

  it("strict UUID for persisted assistant; rejects msg-assistant-1", () => {
    expect(
      isPersistedAssistantMessageId("11111111-1111-4111-8111-111111111111"),
    ).toBe(true);
    expect(isPersistedAssistantMessageId("msg-assistant-1")).toBe(false);
    expect(isPersistedAssistantMessageId("local-assistant-123")).toBe(false);
    expect(isLocalOptimisticMessageId("local-assistant-123")).toBe(true);
    expect(isLocalOptimisticMessageId("local-user-123")).toBe(true);
  });
});
