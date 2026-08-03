import { beforeEach, describe, expect, it, vi } from "vitest";
import { computeUtf16FNV1a } from "@claread/contracts";

vi.mock("server-only", () => ({}));

vi.mock("@/services/bff/session", () => ({
  getWebSession: vi.fn(),
  projectSession: vi.fn((session: typeof mockSession) => ({
    state: "signed_in",
    source: session.source,
    hasAppAccess: true,
  })),
}));

vi.mock("@/services/api/annotations", () => ({
  createUserAnnotation: vi.fn(),
  deleteUserAnnotation: vi.fn(),
  updateUserAnnotation: vi.fn(),
}));

vi.mock("@/services/api/reader-notes", () => ({
  createReaderNote: vi.fn(),
  deleteReaderNote: vi.fn(),
  updateReaderNote: vi.fn(),
}));

import { getWebSession } from "@/services/bff/session";
import { createUserAnnotation, updateUserAnnotation } from "@/services/api/annotations";
import { createReaderNote } from "@/services/api/reader-notes";

import {
  createReadingRecordHighlight,
  createReadingRecordNote,
  updateReadingRecordHighlight,
} from "./reading-record-user-assets";
import type { UserAnnotationResponseDto } from "@/types/api/annotations";

const mockSession = {
  kind: "authenticated",
  sessionToken: "session-token",
  source: "cookie",
} as const;

function makeAnchor(overrides: Record<string, unknown> = {}) {
  const selectedText = "memory";
  return {
    record_id: "record_1",
    base_id: "base_1",
    generation: 1,
    unit_id: "unit_1",
    anchor_segment_id: "seg_1",
    scope: "stable_source",
    offset_unit: "utf16",
    start_offset: 14,
    end_offset: 20,
    selected_text: selectedText,
    text_hash: computeUtf16FNV1a(selectedText),
    hash_algorithm: "fnv1a32-utf16",
    ...overrides,
  };
}

function makeHighlightResponse(
  color: "warm_yellow" | "soft_mint" | "soft_rose",
): UserAnnotationResponseDto {
  return {
    id: "annotation_1",
    anchor_type: "text_range",
    target_key: "reading-record:record_1:range:14:20",
    paragraph_id: null,
    sentence_id: null,
    selected_text: "memory",
    start_offset: null,
    end_offset: null,
    text_hash: computeUtf16FNV1a("memory"),
    segments: [],
    color,
    payload_json: {},
    created_at: "2026-07-01T00:00:00Z",
    updated_at: "2026-07-01T00:00:00Z",
    superseded_ids: [],
    reading_record_id: "record_1",
    base_id: "base_1",
    generation: 1,
    unit_id: "unit_1",
    anchor_segment_id: "seg_1",
    unit_start_utf16: 14,
    unit_end_utf16: 20,
  };
}

describe("reading-record user asset BFF", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getWebSession).mockResolvedValue(mockSession);
  });

  it("rejects non-stable highlight anchors before calling upstream", async () => {
    const result = await createReadingRecordHighlight({
      anchor: makeAnchor({ scope: "translation" }),
      selectedText: "memory",
      color: "warm_yellow",
    });

    expect(result).toMatchObject({
      ok: false,
      status: "invalid_request",
      httpStatus: 400,
    });
    expect(createUserAnnotation).not.toHaveBeenCalled();
  });

  it.each(["warm_yellow", "soft_mint", "soft_rose"] as const)(
    "forwards supported highlight color %s",
    async (color) => {
      vi.mocked(createUserAnnotation).mockResolvedValue({
        ok: true,
        data: makeHighlightResponse(color),
      });

      const result = await createReadingRecordHighlight({
        anchor: makeAnchor(),
        selectedText: "memory",
        color,
      });

      expect(result).toMatchObject({
        ok: true,
        status: "created",
      });
      expect(createUserAnnotation).toHaveBeenCalledWith(
        "session-token",
        expect.objectContaining({ color }),
      );
    },
  );

  it.each(["soft_green", "sage_green", "soft_blue", "soft_purple"])(
    "rejects legacy highlight color %s",
    async (color) => {
      const result = await createReadingRecordHighlight({
        anchor: makeAnchor(),
        selectedText: "memory",
        color,
      });

      expect(result).toMatchObject({
        ok: false,
        status: "invalid_request",
        httpStatus: 400,
      });
      expect(createUserAnnotation).not.toHaveBeenCalled();
    },
  );

  it.each(["warm_yellow", "soft_mint", "soft_rose"] as const)(
    "forwards supported highlight update color %s",
    async (color) => {
      vi.mocked(updateUserAnnotation).mockResolvedValue({
        ok: true,
        data: makeHighlightResponse(color),
      });

      const result = await updateReadingRecordHighlight("annotation_1", {
        color,
      });

      expect(result).toMatchObject({
        ok: true,
        status: "updated",
      });
      expect(updateUserAnnotation).toHaveBeenCalledWith(
        "session-token",
        "annotation_1",
        { color },
      );
    },
  );

  it.each(["soft_green", "sage_green", "soft_blue", "soft_purple"])(
    "rejects legacy highlight update color %s",
    async (color) => {
      const result = await updateReadingRecordHighlight("annotation_1", {
        color,
      });

      expect(result).toMatchObject({
        ok: false,
        status: "invalid_request",
        httpStatus: 400,
      });
      expect(updateUserAnnotation).not.toHaveBeenCalled();
    },
  );

  it("rejects non-stable note anchors before calling upstream", async () => {
    const result = await createReadingRecordNote({
      anchor: makeAnchor({ scope: "system_ai_layer" }),
      selectedText: "memory",
      noteText: "Remember this.",
    });

    expect(result).toMatchObject({
      ok: false,
      status: "invalid_request",
      httpStatus: 400,
    });
    expect(createReaderNote).not.toHaveBeenCalled();
  });
});
