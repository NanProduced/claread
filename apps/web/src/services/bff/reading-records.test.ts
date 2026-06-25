import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

vi.mock("@/services/bff/session", () => ({
  getWebSession: vi.fn(),
}));

vi.mock("@/services/api/reading-records", () => ({
  listUpstreamReadingRecords: vi.fn(),
}));

import { getWebSession } from "@/services/bff/session";
import { listUpstreamReadingRecords } from "@/services/api/reading-records";
import { getReadingRecordListFromWeb } from "./reading-records";
import { appReadingRecordRoute } from "@/lib/routes";
import type { ReadingRecordListResponseDto } from "@/types/api/reading-records";

const mockSession = {
  kind: "authenticated" as const,
  sessionToken: "session-token",
  source: "cookie" as const,
};

function makeListResponse(): ReadingRecordListResponseDto {
  return {
    items: [
      {
        record_id: "reading_record_1",
        title: "First Reading",
        created_at: "2026-06-22T00:00:00Z",
        source_type: "text",
        source_metadata: { source_kind: "web" },
        product_state: "readable_enhancing",
        readiness_state: "article_ready",
        last_event_sequence: 3,
      },
      {
        record_id: "reading_record_2",
        title: null,
        created_at: "2026-06-21T00:00:00Z",
        source_type: "text",
        source_metadata: {},
        product_state: "processing",
        readiness_state: "submitted",
        last_event_sequence: 1,
      },
    ],
    total: 2,
    limit: 20,
  };
}

describe("reading-records BFF list", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getWebSession).mockResolvedValue(mockSession);
  });

  it("rejects anonymous sessions with auth_required", async () => {
    vi.mocked(getWebSession).mockResolvedValue({
      kind: "anonymous",
      source: "none",
    });

    const result = await getReadingRecordListFromWeb();

    expect(result).toMatchObject({ ok: false, status: 401, code: "auth_required" });
    expect(listUpstreamReadingRecords).not.toHaveBeenCalled();
  });

  it("rejects mock_phone sessions with auth_required", async () => {
    vi.mocked(getWebSession).mockResolvedValue({
      kind: "mock_phone",
      source: "mock",
      phone: "13800138000",
    });

    const result = await getReadingRecordListFromWeb();

    expect(result).toMatchObject({ ok: false, status: 401, code: "auth_required" });
    expect(listUpstreamReadingRecords).not.toHaveBeenCalled();
  });

  it("maps upstream 401 to upstream_auth_failed", async () => {
    vi.mocked(listUpstreamReadingRecords).mockResolvedValue({
      ok: false,
      status: 401,
      message: "token expired",
    });

    const result = await getReadingRecordListFromWeb();

    expect(result).toMatchObject({
      ok: false,
      status: 401,
      code: "upstream_auth_failed",
    });
  });

  it("maps upstream 500 to upstream_unavailable (503)", async () => {
    vi.mocked(listUpstreamReadingRecords).mockResolvedValue({
      ok: false,
      status: 500,
      message: "internal error",
    });

    const result = await getReadingRecordListFromWeb();

    expect(result).toMatchObject({
      ok: false,
      status: 503,
      code: "upstream_unavailable",
    });
  });

  it("returns ok with items using readingRecordId and appReadingRecordRoute", async () => {
    vi.mocked(listUpstreamReadingRecords).mockResolvedValue({
      ok: true,
      data: makeListResponse(),
    });

    const result = await getReadingRecordListFromWeb({
      limit: 10,
      query: "focus",
      productStates: ["processing", "failed"],
    });

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.total).toBe(2);
      expect(result.limit).toBe(20);
      expect(result.items).toHaveLength(2);

      const first = result.items[0];
      expect(first.readingRecordId).toBe("reading_record_1");
      expect(first.readerUrl).toBe(appReadingRecordRoute("reading_record_1"));
      expect(first.title).toBe("First Reading");
      expect(first.productState).toBe("readable_enhancing");
      expect(first.readinessState).toBe("article_ready");
      expect(first.lastEventSequence).toBe(3);

      const second = result.items[1];
      expect(second.readingRecordId).toBe("reading_record_2");
      expect(second.readerUrl).toBe(appReadingRecordRoute("reading_record_2"));
      expect(second.title).toBe("Untitled Reading");
      expect(second.productState).toBe("processing");
    }

    expect(vi.mocked(listUpstreamReadingRecords).mock.calls[0]).toEqual([
      "session-token",
      {
        limit: 10,
        query: "focus",
        productStates: ["processing", "failed"],
      },
    ]);
  });

  it("does not expose recordId or record_id in the web-facing shape", async () => {
    vi.mocked(listUpstreamReadingRecords).mockResolvedValue({
      ok: true,
      data: makeListResponse(),
    });

    const result = await getReadingRecordListFromWeb();

    expect(result.ok).toBe(true);
    if (result.ok) {
      for (const item of result.items) {
        expect("recordId" in item).toBe(false);
        expect("record_id" in item).toBe(false);
      }
    }
  });

  it("keeps the new reading-records BFF free of legacy reader routing", () => {
    const source = readFileSync(
      resolve(process.cwd(), "src/services/bff/reading-records.ts"),
      "utf-8",
    );

    expect(source).not.toContain("legacyAppReaderRoute");
    expect(source).not.toContain("/app/reader/");
    expect(source).not.toContain("analysis-tasks");
  });
});
