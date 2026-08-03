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
import { appReaderRoute } from "@/lib/routes";
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
        product_state: "readable_enhancing",
        readiness_state: "article_ready",
        last_event_sequence: 3,
        last_opened_at: "2026-06-22T10:00:00Z",
        display_title: "First Reading",
        source_label: "粘贴文本",
      },
      {
        record_id: "reading_record_2",
        title: null,
        created_at: "2026-06-21T00:00:00Z",
        source_type: "text",
        product_state: "processing",
        readiness_state: "submitted",
        last_event_sequence: 1,
        last_opened_at: null,
        display_title: "未命名解读",
        source_label: "粘贴文本",
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

  it("rejects mock_phone sessions with limited_debug", async () => {
    vi.mocked(getWebSession).mockResolvedValue({
      kind: "mock_phone",
      source: "mock",
      phone: "13800138000",
    });

    const result = await getReadingRecordListFromWeb();

    expect(result).toMatchObject({ ok: false, status: 401, code: "limited_debug" });
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

  it("returns ok with items using readingRecordId and appReaderRoute", async () => {
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
      expect(first.readerUrl).toBe(appReaderRoute("reading_record_1"));
      // S2.5: title is mapped from display_title, not the raw title field
      expect(first.title).toBe("First Reading");
      expect(first.sourceLabel).toBe("粘贴文本");
      expect(first.productState).toBe("readable_enhancing");
      expect(first.readinessState).toBe("article_ready");
      expect(first.lastEventSequence).toBe(3);

      const second = result.items[1];
      expect(second.readingRecordId).toBe("reading_record_2");
      expect(second.readerUrl).toBe(appReaderRoute("reading_record_2"));
      // S2.5: title is mapped from display_title ("未命名解读"), not raw
      // title (null) — the BFF must NOT apply its own "未命名解读" fallback
      expect(second.title).toBe("未命名解读");
      expect(second.sourceLabel).toBe("粘贴文本");
      expect(second.productState).toBe("processing");
      expect(second.lastOpenedAt).toBeNull();
      expect(first.lastOpenedAt).toBe("2026-06-22T10:00:00Z");
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

  it("does not leak raw source_metadata to the browser VM (P1-2)", async () => {
    // The upstream API may still return source_metadata for backward
    // compat, but the BFF VM must NOT include it. Simulate an upstream
    // response with secret metadata and verify the VM is clean.
    const upstreamWithSecret = {
      items: [
        {
          record_id: "rr_secret",
          title: "Secret Record",
          created_at: "2026-07-01T00:00:00Z",
          source_type: "text",
          product_state: "readable_enhancing" as const,
          readiness_state: "article_ready" as const,
          last_event_sequence: 1,
          last_opened_at: null,
          display_title: "Secret Record",
          source_label: "粘贴文本",
          // Extra field not in DTO — simulates upstream still returning it
          source_metadata: {
            secret_api_key: "sk-1234567890abcdef",
            internal_url: "https://internal.example.com/secret",
            nested: { deep: "hidden_value" },
          },
        },
      ],
      total: 1,
      limit: 20,
    };
    vi.mocked(listUpstreamReadingRecords).mockResolvedValue({
      ok: true,
      data: upstreamWithSecret as unknown as ReadingRecordListResponseDto,
    });

    const result = await getReadingRecordListFromWeb();

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.items).toHaveLength(1);
      const vm = result.items[0];

      // VM must not have sourceMetadata field
      expect("sourceMetadata" in vm).toBe(false);
      expect("source_metadata" in vm).toBe(false);

      // No VM value should contain the secret keys or values
      const vmValues = Object.values(vm as unknown as Record<string, unknown>);
      for (const value of vmValues) {
        const str = typeof value === "string" ? value : JSON.stringify(value);
        expect(str).not.toContain("secret_api_key");
        expect(str).not.toContain("sk-1234567890abcdef");
        expect(str).not.toContain("internal_url");
        expect(str).not.toContain("internal.example.com");
        expect(str).not.toContain("nested");
        expect(str).not.toContain("hidden_value");
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

  it("does not impersonate lastOpenedAt from updatedAt/createdAt/lastEventSequence", async () => {
    vi.mocked(listUpstreamReadingRecords).mockResolvedValue({
      ok: true,
      data: {
        items: [
          {
            record_id: "reading_record_x",
            title: "X",
            created_at: "2026-06-20T00:00:00Z",
            source_type: "text",
            product_state: "processing",
            readiness_state: "submitted",
            last_event_sequence: 5,
            last_opened_at: null,
            display_title: "X",
            source_label: "粘贴文本",
          },
        ],
        total: 1,
        limit: 20,
      },
    });
    const result = await getReadingRecordListFromWeb();
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.items[0].lastOpenedAt).toBeNull();
    }
  });
});
