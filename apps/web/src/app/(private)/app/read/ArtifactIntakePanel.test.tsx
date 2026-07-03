/** @vitest-environment jsdom */

import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const navigationMock = vi.hoisted(() => ({
  push: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => navigationMock,
}));

import { ArtifactIntakePanel } from "./ArtifactIntakePanel";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function makeFile(name = "article.pdf", type = "application/pdf"): File {
  return new File([new Uint8Array([0x25, 0x50, 0x44, 0x46])], name, { type });
}

/**
 * The panel exposes a `__testStartArtifactFlow` prop that hands the internal
 * `startArtifactFlow(file)` function out to tests. We use that to drive the
 * pipeline without depending on jsdom's broken FileList event plumbing for
 * `<input type="file">`.
 *
 * The test hook is opt-in: production callers (ReadPageIntake) don't pass
 * the prop, so production code is unaffected.
 */
async function renderAndCaptureStart() {
  let captured: ((file: File) => Promise<void>) | null = null;
  render(
    <ArtifactIntakePanel
      readingGoal="daily_reading"
      readingVariant="intermediate_reading"
      onUseTextMode={() => undefined}
      __testStartArtifactFlow={(start) => {
        captured = start;
      }}
    />,
  );
  await waitFor(() => {
    expect(captured).not.toBeNull();
  });
  return (file: File) => {
    if (!captured) throw new Error("startArtifactFlow not captured");
    return captured(file);
  };
}

// --- Flat BFF response fixtures ---

function makeInitResponse(artifactId = "art_1") {
  return {
    ok: true as const,
    artifact_id: artifactId,
    presigned_url: `https://oss.example.com/${artifactId}?sig=x`,
    presigned_method: "PUT",
    headers: {},
  };
}

function makeCompleteResponse(artifactId = "art_1") {
  return {
    ok: true as const,
    artifact_id: artifactId,
    upload_completed: true,
  };
}

function makeSubmitResponse(readingRecordId = "rec_artifact_stable") {
  return {
    ok: true as const,
    reading_record_id: readingRecordId,
  };
}

function makePipelineStableResponse(readingRecordId = "rec_artifact_stable") {
  return {
    ok: true as const,
    artifact: {
      artifact_id: "art_1",
      status: "available",
      artifact_kind: "original_upload",
      storage_provider: "oss",
      bucket: "claread",
      endpoint: "https://oss.example.com",
      object_key: "artifacts/art_1.bin",
      content_type: "application/pdf",
      byte_size: 4,
      content_sha256: "abc",
      source_filename: "article.pdf",
      reading_record_id: readingRecordId,
      original_input_id: "inp_1",
    },
    record: {
      reading_record_id: readingRecordId,
      generation: 1,
      product_state: "processing",
      readiness_state: "submitted",
      active_base_id: null,
      source_type: "pdf_text",
      title: null,
      language: null,
    },
    original_input: null,
    extraction_job: null,
    materialization_job: null,
    candidate_document: null,
    stable_document: null,
    outcome: "stable_document_ready",
    next_action: "open_reader",
  };
}

interface FetchHandler {
  matches: (url: string) => boolean;
  respond: () => Response | Promise<Response>;
}

function buildFetchMock(handlers: FetchHandler[]) {
  const mock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    for (const handler of handlers) {
      if (handler.matches(url)) {
        return handler.respond();
      }
    }
    return new Response(null, { status: 200 });
  });
  return mock;
}

const STABLE_PIPELINE_HANDLERS: FetchHandler[] = [
  { matches: (u) => u.endsWith("/init-upload"), respond: () => jsonResponse(makeInitResponse()) },
  { matches: (u) => u.includes("/complete-upload"), respond: () => jsonResponse(makeCompleteResponse()) },
  { matches: (u) => u.includes("/submit-input"), respond: () => jsonResponse(makeSubmitResponse()) },
  {
    matches: (u) => u.endsWith("/pipeline-status"),
    respond: () => jsonResponse(makePipelineStableResponse()),
  },
];

describe("ArtifactIntakePanel — stable happy path (real DOM behavior)", () => {
  beforeEach(() => {
    navigationMock.push.mockReset();
    vi.stubGlobal("fetch", buildFetchMock(STABLE_PIPELINE_HANDLERS));
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("captures the startArtifactFlow handle via the test hook", async () => {
    const start = await renderAndCaptureStart();
    expect(typeof start).toBe("function");
  });

  it("runs init-upload → presigned PUT → complete-upload → submit-input → pipeline-status and pushes /app/reader-record/{id}", async () => {
    const start = await renderAndCaptureStart();
    await start(makeFile("article.pdf"));

    await waitFor(() => {
      expect(navigationMock.push).toHaveBeenCalledWith(
        "/app/reader-record/rec_artifact_stable",
      );
    });

    const fetchMock = vi.mocked(global.fetch as unknown as ReturnType<typeof vi.fn>);
    const calls = fetchMock.mock.calls;

    const isUrlOfKind = (u: string, kind: "init" | "complete" | "submit" | "pipeline" | "presigned") => {
      switch (kind) {
        case "init":
          return u.endsWith("/init-upload");
        case "complete":
          return u.includes("/complete-upload");
        case "submit":
          return u.includes("/submit-input");
        case "pipeline":
          return u.endsWith("/pipeline-status");
        case "presigned":
          return u.startsWith("https://oss.example.com/");
      }
    };

    const initIdx = calls.findIndex((c) => isUrlOfKind(String(c[0]), "init"));
    const presignedIdx = calls.findIndex((c) => isUrlOfKind(String(c[0]), "presigned"));
    const completeIdx = calls.findIndex((c) => isUrlOfKind(String(c[0]), "complete"));
    const submitIdx = calls.findIndex((c) => isUrlOfKind(String(c[0]), "submit"));
    const pipelineIdx = calls.findIndex((c) => isUrlOfKind(String(c[0]), "pipeline"));

    expect(initIdx).toBeGreaterThanOrEqual(0);
    expect(presignedIdx).toBeGreaterThan(initIdx);
    expect(completeIdx).toBeGreaterThan(presignedIdx);
    expect(submitIdx).toBeGreaterThan(completeIdx);
    expect(pipelineIdx).toBeGreaterThan(submitIdx);

    const submitCall = calls.find(
      (c) => isUrlOfKind(String(c[0]), "submit") && c[1]?.method === "POST",
    );
    const submitBody = JSON.parse(String(submitCall?.[1]?.body ?? "{}"));
    expect(submitBody).toMatchObject({
      readingGoal: "daily_reading",
      readingVariant: "intermediate_reading",
      title: "article.pdf",
      language: "en",
    });
  });
});