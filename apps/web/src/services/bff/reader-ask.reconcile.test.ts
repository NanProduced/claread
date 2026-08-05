import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

vi.mock("@/services/bff/session", () => ({
  getWebSession: vi.fn(),
}));

vi.mock("@/services/api/reader-ask", () => ({
  getUpstreamReadingRecordAskSubmission: vi.fn(),
  retryUpstreamReaderAskMessage: vi.fn(),
  retryUpstreamReadingRecordAskMessage: vi.fn(),
}));

import { getWebSession } from "@/services/bff/session";
import { getUpstreamReadingRecordAskSubmission } from "@/services/api/reader-ask";
import { reconcileReaderAskSubmissionForWeb } from "./reader-ask";

const mockSession = {
  kind: "authenticated" as const,
  sessionToken: "session-token",
  source: "cookie" as const,
};

const SID = "22222222-2222-4222-8222-222222222222";

describe("reconcileReaderAskSubmissionForWeb", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getWebSession).mockResolvedValue(mockSession);
  });

  it("proxies RR reconcile to FastAPI", async () => {
    vi.mocked(getUpstreamReadingRecordAskSubmission).mockResolvedValue({
      ok: true,
      data: {
        client_submission_id: SID,
        thread_id: "thread-1",
        status: "completed",
        user_message_id: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
        assistant_message_id: "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff",
      },
    });

    const res = await reconcileReaderAskSubmissionForWeb(
      "rr-1",
      "thread-1",
      SID,
    );
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.status).toBe("completed");
    expect(getUpstreamReadingRecordAskSubmission).toHaveBeenCalledWith(
      "rr-1",
      "thread-1",
      SID,
      "session-token",
    );
  });

  it("rejects non-UUID client_submission_id", async () => {
    const res = await reconcileReaderAskSubmissionForWeb(
      "rr-1",
      "thread-1",
      "not-uuid",
    );
    expect(res.status).toBe(400);
    expect(getUpstreamReadingRecordAskSubmission).not.toHaveBeenCalled();
  });
});
