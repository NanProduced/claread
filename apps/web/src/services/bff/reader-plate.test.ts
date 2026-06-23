import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

vi.mock("@/services/bff/session", () => ({
  getWebSession: vi.fn(),
}));

vi.mock("@/services/api/reader-plate", () => ({
  submitUpstreamReaderPlainText: vi.fn(),
  getUpstreamReaderPlateSnapshot: vi.fn(),
  pollUpstreamReaderEvents: vi.fn(),
}));

import { getWebSession } from "@/services/bff/session";
import {
  getUpstreamReaderPlateSnapshot,
  pollUpstreamReaderEvents,
  submitUpstreamReaderPlainText,
} from "@/services/api/reader-plate";
import {
  getReaderPlateSnapshotFromWeb,
  pollReaderEventsFromWeb,
  submitReadingRecordPlainTextFromWeb,
  submitReaderPlainTextFromWeb,
} from "./reader-plate";
import { appReadingRecordRoute } from "@/lib/routes";
import type { ReaderPlateSnapshotDto } from "@/types/api/reader-plate";

const mockSession = {
  kind: "authenticated" as const,
  sessionToken: "session-token",
  source: "cookie" as const,
};

function makeSnapshot(): ReaderPlateSnapshotDto {
  return {
    schema_kind: "reader_plate_snapshot",
    snapshot_id: "reader_snapshot_abc",
    snapshot_taken_at: "2026-06-21T00:00:00Z",
    last_event_sequence: 1,
    record_id: "rec_1",
    record: {
      title: "Reader Plate BFF Fixture",
      created_at: "2026-06-21T00:00:00Z",
      source_type: "plain_text",
      source_metadata: {},
      product_state: "readable_enhancing",
      readiness_state: "article_ready",
    },
    base: {
      base_id: "base_1",
      content_sha256: "a".repeat(64),
      canonicalizer_version: "1.0.0",
      builder_version: "1.0.0",
      segmenter_version: "1.0.0",
      text_length_utf16: 12,
      hash_algorithm: "fnv1a32-utf16",
    },
    navigation: { units: [] },
    anchor_segments: [],
    enhancement_layers: [],
    ask_supplements: [],
    user_assets: [],
    parsed_decisions: [],
    value: [],
  };
}

describe("reader-plate BFF submit", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getWebSession).mockResolvedValue(mockSession);
  });

  it("rejects empty text with empty_text before hitting session or upstream", async () => {
    const result = await submitReaderPlainTextFromWeb({ plainText: "   " });

    expect(result).toMatchObject({
      ok: false,
      status: 400,
      code: "empty_text",
    });
    expect(getWebSession).not.toHaveBeenCalled();
    expect(submitUpstreamReaderPlainText).not.toHaveBeenCalled();
  });

  it("rejects non-string plainText as empty_text", async () => {
    const result = await submitReaderPlainTextFromWeb({ plainText: undefined });

    expect(result).toMatchObject({ ok: false, status: 400, code: "empty_text" });
  });

  it("rejects anonymous sessions with auth_required", async () => {
    vi.mocked(getWebSession).mockResolvedValue({
      kind: "anonymous",
      source: "none",
    });

    const result = await submitReaderPlainTextFromWeb({ plainText: "Hello." });

    expect(result).toMatchObject({ ok: false, status: 401, code: "auth_required" });
    expect(submitUpstreamReaderPlainText).not.toHaveBeenCalled();
  });

  it("rejects mock_phone sessions with auth_required", async () => {
    vi.mocked(getWebSession).mockResolvedValue({
      kind: "mock_phone",
      source: "mock",
      phone: "13800138000",
    });

    const result = await submitReaderPlainTextFromWeb({ plainText: "Hello." });

    expect(result).toMatchObject({ ok: false, status: 401, code: "auth_required" });
    expect(submitUpstreamReaderPlainText).not.toHaveBeenCalled();
  });

  it("maps upstream 401 to upstream_auth_failed", async () => {
    vi.mocked(submitUpstreamReaderPlainText).mockResolvedValue({
      ok: false,
      status: 401,
      message: "token expired",
    });

    const result = await submitReaderPlainTextFromWeb({ plainText: "Hello." });

    expect(result).toMatchObject({
      ok: false,
      status: 401,
      code: "upstream_auth_failed",
    });
  });

  it("maps upstream 409 to upstream_error with original status", async () => {
    vi.mocked(submitUpstreamReaderPlainText).mockResolvedValue({
      ok: false,
      status: 409,
      message: "client_record_id already exists for this user",
    });

    const result = await submitReaderPlainTextFromWeb({ plainText: "Hello." });

    expect(result).toMatchObject({
      ok: false,
      status: 409,
      code: "upstream_error",
    });
  });

  it("maps upstream 500 to upstream_unavailable (503)", async () => {
    vi.mocked(submitUpstreamReaderPlainText).mockResolvedValue({
      ok: false,
      status: 500,
      message: "internal error",
    });

    const result = await submitReaderPlainTextFromWeb({ plainText: "Hello." });

    expect(result).toMatchObject({
      ok: false,
      status: 503,
      code: "upstream_unavailable",
    });
  });

  it("maps upstream network failure (status 0) to upstream_unavailable", async () => {
    vi.mocked(submitUpstreamReaderPlainText).mockResolvedValue({
      ok: false,
      status: 0,
      message: "fetch failed",
    });

    const result = await submitReaderPlainTextFromWeb({ plainText: "Hello." });

    expect(result).toMatchObject({
      ok: false,
      status: 503,
      code: "upstream_unavailable",
    });
  });

  it("returns ok with snapshot on successful submit", async () => {
    const snapshot = makeSnapshot();
    vi.mocked(submitUpstreamReaderPlainText).mockResolvedValue({
      ok: true,
      data: {
        record_id: "rec_1",
        base_id: "base_1",
        article_ready_sequence: 1,
        snapshot,
      },
    });

    const result = await submitReaderPlainTextFromWeb({ plainText: "Hello." });

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.record_id).toBe("rec_1");
      expect(result.snapshot.schema_kind).toBe("reader_plate_snapshot");
      expect(result.article_ready_sequence).toBe(1);
    }
    expect(vi.mocked(submitUpstreamReaderPlainText).mock.calls[0][0]).toMatchObject({
      plain_text: "Hello.",
      client_record_id: expect.stringMatching(/^web-plate-/),
    });
  });

  it("returns a product submit contract with Reading Record id semantics", async () => {
    const snapshot = makeSnapshot();
    vi.mocked(submitUpstreamReaderPlainText).mockResolvedValue({
      ok: true,
      data: {
        record_id: "reading_record_1",
        base_id: "base_1",
        article_ready_sequence: 1,
        snapshot,
      },
    });

    const result = await submitReadingRecordPlainTextFromWeb({
      plainText: "Hello.",
      title: "Test",
      language: "en",
    });

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result).toMatchObject({
        message: "阅读记录已创建，正在打开 Reader。",
        readingRecordId: "reading_record_1",
        readerUrl: appReadingRecordRoute("reading_record_1"),
        baseId: "base_1",
        articleReadySequence: 1,
      });
      expect("recordId" in result).toBe(false);
      expect("record_id" in result).toBe(false);
    }
  });

  it("keeps the new product submit BFF free of legacy analysis routing", () => {
    const source = readFileSync(
      resolve(process.cwd(), "src/services/bff/reader-plate.ts"),
      "utf-8",
    );

    expect(source).not.toContain("legacyAppReaderRoute");
    expect(source).not.toContain("/app/reader/");
    expect(source).not.toContain("analysis-tasks");
  });
});

describe("reader-plate BFF snapshot", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getWebSession).mockResolvedValue(mockSession);
  });

  it("rejects anonymous sessions", async () => {
    vi.mocked(getWebSession).mockResolvedValue({
      kind: "anonymous",
      source: "none",
    });

    const result = await getReaderPlateSnapshotFromWeb("rec_1");

    expect(result).toMatchObject({ ok: false, status: 401, code: "auth_required" });
  });

  it("maps upstream 404 to record_not_found", async () => {
    vi.mocked(getUpstreamReaderPlateSnapshot).mockResolvedValue({
      ok: false,
      status: 404,
      message: "Reader record not found",
    });

    const result = await getReaderPlateSnapshotFromWeb("rec_1");

    expect(result).toMatchObject({
      ok: false,
      status: 404,
      code: "record_not_found",
    });
  });

  it("maps upstream 409 to upstream_error", async () => {
    vi.mocked(getUpstreamReaderPlateSnapshot).mockResolvedValue({
      ok: false,
      status: 409,
      message: "snapshot stale",
    });

    const result = await getReaderPlateSnapshotFromWeb("rec_1");

    expect(result).toMatchObject({
      ok: false,
      status: 409,
      code: "upstream_error",
    });
  });

  it("returns ok with snapshot on success", async () => {
    const snapshot = makeSnapshot();
    vi.mocked(getUpstreamReaderPlateSnapshot).mockResolvedValue({
      ok: true,
      data: snapshot,
    });

    const result = await getReaderPlateSnapshotFromWeb("rec_1");

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.snapshot_id).toBe("reader_snapshot_abc");
      expect(result.last_event_sequence).toBe(1);
    }
  });
});

describe("reader-plate BFF events polling", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getWebSession).mockResolvedValue(mockSession);
  });

  it("rejects mock_phone sessions", async () => {
    vi.mocked(getWebSession).mockResolvedValue({
      kind: "mock_phone",
      source: "mock",
      phone: "13800138000",
    });

    const result = await pollReaderEventsFromWeb("rec_1", { afterSequence: 0 });

    expect(result).toMatchObject({ ok: false, status: 401, code: "auth_required" });
  });

  it("maps upstream 500 to upstream_unavailable", async () => {
    vi.mocked(pollUpstreamReaderEvents).mockResolvedValue({
      ok: false,
      status: 502,
      message: "bad gateway",
    });

    const result = await pollReaderEventsFromWeb("rec_1", { afterSequence: 0 });

    expect(result).toMatchObject({
      ok: false,
      status: 503,
      code: "upstream_unavailable",
    });
  });

  it("returns ok with poll response on success", async () => {
    vi.mocked(pollUpstreamReaderEvents).mockResolvedValue({
      ok: true,
      data: {
        reading_record_id: "rec_1",
        after_sequence: 0,
        next_after_sequence: 0,
        last_event_sequence: 0,
        has_more: false,
        truncated: false,
        reload_required: false,
        reload_reason: null,
        events: [],
      },
    });

    const result = await pollReaderEventsFromWeb("rec_1", {
      afterSequence: 0,
      limit: 50,
    });

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.events).toEqual([]);
      expect(result.reload_required).toBe(false);
    }
    expect(vi.mocked(pollUpstreamReaderEvents).mock.calls[0]).toEqual([
      "rec_1",
      "session-token",
      { afterSequence: 0, limit: 50 },
    ]);
  });
});
