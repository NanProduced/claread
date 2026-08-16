import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

vi.mock("@/services/api/upstream", () => ({
  fastApiFetch: vi.fn(),
}));

import { fastApiFetch } from "@/services/api/upstream";
import {
  deleteReaderRecord,
  hideReaderRecordFromRecent,
  listUpstreamReadingRecords,
} from "./reading-records";

const mockedFetch = vi.mocked(fastApiFetch);

describe("listUpstreamReadingRecords recentOnly mapping", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    mockedFetch.mockResolvedValue({
      ok: true,
      data: { items: [], total: 0, limit: 10 },
    });
  });

  it("maps recentOnly=true to recent_only=true", async () => {
    await listUpstreamReadingRecords("token", {
      limit: 10,
      recentOnly: true,
    });

    expect(mockedFetch).toHaveBeenCalledTimes(1);
    const [path] = mockedFetch.mock.calls[0];
    expect(path).toBe("/reader/records?limit=10&recent_only=true");
  });

  it("omits recent_only when recentOnly is false", async () => {
    await listUpstreamReadingRecords("token", {
      limit: 10,
      recentOnly: false,
    });

    const [path] = mockedFetch.mock.calls[0];
    expect(path).toBe("/reader/records?limit=10");
  });

  it("omits recent_only when recentOnly is undefined (default)", async () => {
    await listUpstreamReadingRecords("token", { limit: 10 });

    const [path] = mockedFetch.mock.calls[0];
    expect(path).toBe("/reader/records?limit=10");
  });
});

describe("hideReaderRecordFromRecent", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("uses DELETE with the /recent path and encodeURIComponent", async () => {
    mockedFetch.mockResolvedValue({
      ok: true,
      data: {
        record_id: "abc 123/ü",
        status: "removed_from_recent",
        recent_hidden_at: "2026-08-16T00:00:00Z",
      },
    });

    const result = await hideReaderRecordFromRecent("token", "abc 123/ü");

    expect(mockedFetch).toHaveBeenCalledTimes(1);
    const [path, options] = mockedFetch.mock.calls[0];
    expect(path).toBe(`/reader/records/${encodeURIComponent("abc 123/ü")}/recent`);
    expect(options).toMatchObject({ sessionToken: "token", method: "DELETE" });
    expect(result.ok).toBe(true);
  });
});

describe("deleteReaderRecord", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("uses DELETE with encodeURIComponent and maps the full DTO", async () => {
    mockedFetch.mockResolvedValue({
      ok: true,
      data: {
        record_id: "rec 1",
        status: "deleted",
        deleted_at: "2026-08-16T00:00:00Z",
        vector_gc_intent_recorded: true,
      },
    });

    const result = await deleteReaderRecord("token", "rec 1");

    expect(mockedFetch).toHaveBeenCalledTimes(1);
    const [path, options] = mockedFetch.mock.calls[0];
    expect(path).toBe(`/reader/records/${encodeURIComponent("rec 1")}`);
    expect(options).toMatchObject({ sessionToken: "token", method: "DELETE" });
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.data.vector_gc_intent_recorded).toBe(true);
    }
  });

  it("passes idempotent already_deleted through unchanged", async () => {
    mockedFetch.mockResolvedValue({
      ok: true,
      data: {
        record_id: "rec 2",
        status: "already_deleted",
        deleted_at: "2026-08-01T00:00:00Z",
        vector_gc_intent_recorded: true,
      },
    });

    const result = await deleteReaderRecord("token", "rec 2");

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.data.status).toBe("already_deleted");
    }
  });
});
