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
}));

vi.mock("@/services/api/reader-notes", () => ({
  createReaderNote: vi.fn(),
}));

import { getWebSession } from "@/services/bff/session";
import { createUserAnnotation } from "@/services/api/annotations";
import { createReaderNote } from "@/services/api/reader-notes";

import {
  createReadingRecordHighlight,
  createReadingRecordNote,
} from "./reading-record-user-assets";

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

describe("reading-record user asset BFF", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getWebSession).mockResolvedValue(mockSession);
  });

  it("rejects non-stable highlight anchors before calling upstream", async () => {
    const result = await createReadingRecordHighlight({
      anchor: makeAnchor({ scope: "translation" }),
      selectedText: "memory",
      color: "soft_green",
    });

    expect(result).toMatchObject({
      ok: false,
      status: "invalid_request",
      httpStatus: 400,
    });
    expect(createUserAnnotation).not.toHaveBeenCalled();
  });

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
