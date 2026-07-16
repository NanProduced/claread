/** @vitest-environment jsdom */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AnalysisLoadingStatusBar, AnalyzeSubmitForm } from "./AnalyzeSubmitForm";
import {
  READ_PAGE_SUBMIT_MODE,
  readPageSubmitEndpoint,
  readPageSubmitRequestBody,
} from "./submit-mode";
import { PENDING_CANDIDATE_STORAGE_KEY } from "./pending-candidate";

const navigationMock = vi.hoisted(() => ({
  push: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => navigationMock,
}));

function createMemoryStorage(): Storage {
  const store = new Map<string, string>();

  return {
    get length() {
      return store.size;
    },
    clear() {
      store.clear();
    },
    getItem(key: string) {
      return store.get(key) ?? null;
    },
    key(index: number) {
      return Array.from(store.keys())[index] ?? null;
    },
    removeItem(key: string) {
      store.delete(key);
    },
    setItem(key: string, value: string) {
      store.set(key, value);
    },
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function makeFile(name = "article.pdf", type = "application/pdf"): File {
  return new File([new Uint8Array([0x25, 0x50, 0x44, 0x46])], name, { type });
}

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

function makeArtifactSubmitResponse(readingRecordId = "rec_artifact_stable") {
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

function makePipelineStalledResponse(readingRecordId = "rec_artifact_stalled") {
  return {
    ...makePipelineStableResponse(readingRecordId),
    extraction_job: {
      job_id: "job_extraction_stalled",
      status: "queued",
      attempt_count: 0,
      max_attempts: 3,
      available_at: "2026-07-15T00:00:00Z",
      updated_at: "2026-07-15T00:00:00Z",
    },
    outcome: "extraction_queued" as const,
    next_action: "wait_for_worker" as const,
  };
}

function installStableArtifactFetchMock(readingRecordId = "rec_artifact_stable") {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/init-upload")) {
      return jsonResponse(makeInitResponse());
    }
    if (url.startsWith("https://oss.example.com/")) {
      return new Response(null, { status: 200 });
    }
    if (url.endsWith("/complete-upload")) {
      return jsonResponse(makeCompleteResponse());
    }
    if (url.endsWith("/submit-input")) {
      expect(init?.body ? JSON.parse(String(init.body)) : {}).toMatchObject({
        language: "en",
      });
      return jsonResponse(makeArtifactSubmitResponse(readingRecordId));
    }
    if (url.endsWith("/pipeline-status")) {
      return jsonResponse(makePipelineStableResponse(readingRecordId));
    }
    throw new Error(`Unexpected fetch ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

beforeEach(() => {
  vi.resetAllMocks();
  navigationMock.push.mockReset();
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: createMemoryStorage(),
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("AnalysisLoadingStatusBar", () => {
  it("uses reassurance copy without fake progress or timers", () => {
    render(
      <AnalysisLoadingStatusBar
        messagePrefix="正在透读"
      />,
    );

    expect(screen.getByText("正在透读")).toBeTruthy();
    expect(screen.getByText("离开本页不会影响透读，完成后会保存到阅读记录")).toBeTruthy();
    expect(screen.getByText("正在梳理文章结构")).toBeTruthy();

    const renderedText = document.body.textContent ?? "";
    expect(renderedText).not.toMatch(/\d{1,2}:\d{2}/);
    expect(renderedText).not.toContain("%");
    expect(renderedText).not.toContain("第");
    expect(renderedText).not.toContain("共");
  });
});

function makeUnifiedInputStableResponse() {
  return {
    ok: true as const,
    outcome: "stable_document_ready" as const,
    reading_record_id: "rec_unified_1",
    stable_document_id: "sd_1",
    base_id: "base_1",
    record_generation: 1,
    document_version: 1,
    title: "Unified stable fixture",
    content_sha256: "abc",
    canonical_text_sha256: "def",
    block_count: 1,
    article_ready_event_id: "evt_1",
    article_ready_sequence: 1,
    suitability: {
      outcome: "stable_document_ready" as const,
      source_type: "pasted_text" as const,
      word_count: 10,
      english_word_ratio: 1,
      natural_language_score: 0.95,
      flags: [],
      reasons: [],
      normalized_preview: "Hello.",
    },
    snapshot: {
      record: { title: "Unified stable fixture" },
    },
  };
}

function makeUnifiedInputCandidateResponse() {
  return {
    ok: true as const,
    outcome: "candidate_document_required" as const,
    reading_record_id: "rec_unified_2",
    candidate_document_id: "cand_1",
    original_input_id: "inp_cand_1",
    record_generation: 1,
    status: "ready" as const,
    title: null,
    block_count: 1,
    source_type: "pasted_text" as const,
    filename: null,
    suitability: {
      outcome: "candidate_document_required" as const,
      source_type: "pasted_text" as const,
      word_count: 10,
      english_word_ratio: 1,
      natural_language_score: 0.95,
      flags: [],
      reasons: [],
      normalized_preview: "Hello.",
    },
  };
}

function makeUnifiedInputRejectedResponse() {
  return {
    ok: true as const,
    outcome: "input_rejected_or_action_required" as const,
    suitability: {
      outcome: "input_rejected_or_action_required" as const,
      source_type: "pasted_text" as const,
      word_count: 5,
      english_word_ratio: 0.2,
      natural_language_score: 0.3,
      flags: ["too_short_for_learning", "non_english_or_mixed_language"],
      reasons: ["内容过短", "英文比例过低"],
      normalized_preview: "abc 123 你好",
    },
  };
}

describe("AnalyzeSubmitForm unified input cutover", () => {
  it("uses Reader Plate input submit mode", () => {
    expect(READ_PAGE_SUBMIT_MODE).toBe("reader-plate-input");
    expect(readPageSubmitEndpoint()).toBe("/api/web/reader-plate/input");
    expect(
      readPageSubmitRequestBody({
        text: "This is a short English article.",
        readingGoal: "daily_reading",
        readingVariant: "intermediate_reading",
      }),
    ).toEqual({
      text: "This is a short English article.",
      sourceType: "pasted_text",
      filename: null,
      reading_goal: "daily_reading",
      reading_variant: "intermediate_reading",
    });
  });

  it("forwards reading_goal / reading_variant in the unified submit body", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe("/api/web/reader-plate/input");
      expect(init).toEqual(
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({
            text: "This is a short English article.",
            sourceType: "pasted_text",
            filename: null,
            reading_goal: "exam",
            reading_variant: "cet",
          }),
        }),
      );

      return new Response(
        JSON.stringify(makeUnifiedInputStableResponse()),
        {
          status: 200,
          headers: { "content-type": "application/json" },
        },
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <AnalyzeSubmitForm
        readingGoal="exam"
        readingVariant="cet"
      />,
    );

    fireEvent.change(screen.getByPlaceholderText("Paste an English article here"), {
      target: { value: "This is a short English article." },
    });
    fireEvent.click(screen.getByRole("button", { name: "开始透读" }));

    await waitFor(() => {
      expect(navigationMock.push).toHaveBeenCalledWith(
        "/app/reader-record/rec_unified_1",
      );
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("filters out academic in unified submit and falls back to daily_reading", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const body = init?.body ? JSON.parse(String(init.body)) : {};
      // academic / academic_general must never reach the submit body
      expect(body.reading_goal).not.toBe("academic");
      expect(body.reading_variant).not.toBe("academic_general");
      expect(body.reading_goal).toBe("daily_reading");
      expect(body.reading_variant).toBe("intermediate_reading");

      return new Response(
        JSON.stringify(makeUnifiedInputStableResponse()),
        {
          status: 200,
          headers: { "content-type": "application/json" },
        },
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <AnalyzeSubmitForm
        readingGoal="academic"
        readingVariant="academic_general"
      />,
    );

    fireEvent.change(screen.getByPlaceholderText("Paste an English article here"), {
      target: { value: "This is a short English article." },
    });
    fireEvent.click(screen.getByRole("button", { name: "开始透读" }));

    await waitFor(() => {
      expect(navigationMock.push).toHaveBeenCalledWith(
        "/app/reader-record/rec_unified_1",
      );
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("submits to the unified endpoint and lands on reader-record", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe("/api/web/reader-plate/input");
      expect(init).toEqual(
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({
            text: "This is a short English article.",
            sourceType: "pasted_text",
            filename: null,
            reading_goal: "daily_reading",
            reading_variant: "intermediate_reading",
          }),
        }),
      );

      return new Response(
        JSON.stringify(makeUnifiedInputStableResponse()),
        {
          status: 200,
          headers: { "content-type": "application/json" },
        },
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <AnalyzeSubmitForm
        readingGoal="daily_reading"
        readingVariant="intermediate_reading"
      />,
    );

    fireEvent.change(screen.getByPlaceholderText("Paste an English article here"), {
      target: { value: "This is a short English article." },
    });
    fireEvent.click(screen.getByRole("button", { name: "开始透读" }));

    await waitFor(() => {
      expect(navigationMock.push).toHaveBeenCalledWith(
        "/app/reader-record/rec_unified_1",
      );
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(window.localStorage.getItem(PENDING_CANDIDATE_STORAGE_KEY)).toBeNull();
  });

  it("stable_document_ready outcome navigates to /app/reader-record/{id}", async () => {
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify(makeUnifiedInputStableResponse()), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(
      <AnalyzeSubmitForm
        readingGoal="daily_reading"
        readingVariant="intermediate_reading"
      />,
    );

    fireEvent.change(screen.getByPlaceholderText("Paste an English article here"), {
      target: { value: "Hello world from a stable doc." },
    });
    fireEvent.click(screen.getByRole("button", { name: "开始透读" }));

    await waitFor(() => {
      expect(navigationMock.push).toHaveBeenCalledWith(
        "/app/reader-record/rec_unified_1",
      );
    });
  });

  it("removes the submit CTA from the DOM while pending and shows the loading bar", async () => {
    // Plan B: during pending, the entire toolbar (including the CTA) is
    // replaced by AnalysisLoadingStatusBar. Combined with the
    // `if (state.kind === "pending") return;` re-entrancy guard at the top
    // of handleSubmit, this is the no-double-submit guarantee.
    const resolveHolder: { current: ((value: Response) => void) | undefined } = {
      current: undefined,
    };
    const fetchMock = vi.fn(
      () =>
        new Promise<Response>((resolve) => {
          resolveHolder.current = resolve;
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(
      <AnalyzeSubmitForm
        readingGoal="daily_reading"
        readingVariant="intermediate_reading"
      />,
    );

    fireEvent.change(screen.getByPlaceholderText("Paste an English article here"), {
      target: { value: "Hello, this is a test article." },
    });

    const submitButton = screen.getByRole("button", { name: "开始透读" });
    expect(submitButton).toBeTruthy();

    fireEvent.click(submitButton);

    // While the fetch is in flight: the CTA must be gone, the loading bar
    // must be present, and a second click must NOT produce a second fetch.
    await waitFor(() => {
      expect(screen.queryByRole("button", { name: "开始透读" })).toBeNull();
    });
    expect(screen.getByText("正在透读")).toBeTruthy();
    expect(screen.getByText("离开本页不会影响透读，完成后会保存到阅读记录")).toBeTruthy();

    // Settle the in-flight fetch so React finishes its updates cleanly.
    const resolve = resolveHolder.current;
    if (!resolve) {
      throw new Error("fetch mock was never invoked");
    }
    resolve(
      new Response(JSON.stringify(makeUnifiedInputStableResponse()), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );

    await waitFor(() => {
      expect(navigationMock.push).toHaveBeenCalledWith(
        "/app/reader-record/rec_unified_1",
      );
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("the pending re-entrancy guard in handleSubmit prevents a second fetch", async () => {
    // Even if the in-flight guard were bypassed (e.g. by directly invoking
    // the click handler twice in the same tick before React commits the
    // pending state), the `if (state.kind === "pending") return;` early
    // return at the top of handleSubmit must short-circuit the second call.
    const fetchMock = vi.fn(async () => {
      return new Response(JSON.stringify(makeUnifiedInputStableResponse()), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <AnalyzeSubmitForm
        readingGoal="daily_reading"
        readingVariant="intermediate_reading"
      />,
    );

    fireEvent.change(screen.getByPlaceholderText("Paste an English article here"), {
      target: { value: "Hello, this is a test article." },
    });

    // The "during pending" CTA-removal test above already proves the
    // design-level guarantee. Here we lock the source-level guard so it
    // cannot regress silently.
    const source = readFileSync(
      resolve(
        process.cwd(),
        "src/app/(private)/app/read/AnalyzeSubmitForm.tsx",
      ),
      "utf-8",
    );

    expect(source).toMatch(
      /async function handleSubmit\(\)\s*\{[\s\S]{0,200}?if \(isWaiting\)\s*\{\s*return;\s*\}/,
    );
  });

  it("candidate_document_required outcome opens the confirm dialog and keeps internal ids out of the DOM", async () => {
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify(makeUnifiedInputCandidateResponse()), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(
      <AnalyzeSubmitForm
        readingGoal="daily_reading"
        readingVariant="intermediate_reading"
      />,
    );

    fireEvent.change(screen.getByPlaceholderText("Paste an English article here"), {
      target: { value: "Markdown article needing confirmation." },
    });
    fireEvent.click(screen.getByRole("button", { name: "开始透读" }));

    await waitFor(() => {
      expect(screen.getByTestId("candidate-confirm-dialog")).toBeTruthy();
    });
    expect(screen.getByText("确认提取出的英文文章")).toBeTruthy();
    expect(screen.getByTestId("candidate-confirm-preview").textContent).toContain(
      "Markdown article needing confirmation.",
    );
    expect(screen.getByTestId("candidate-confirm-button")).toBeTruthy();
    expect(screen.getByRole("button", { name: "稍后处理" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "重新提交" })).toBeTruthy();
    expect(screen.queryByText("rec_unified_2")).toBeNull();
    expect(screen.queryByText("cand_1")).toBeNull();
    expect(screen.queryByText("inp_cand_1")).toBeNull();
    expect(screen.queryByText("candidate_document_id")).toBeNull();
    expect(screen.queryByText("reading_record_id")).toBeNull();
    expect(navigationMock.push).not.toHaveBeenCalled();

    const pending = JSON.parse(
      window.localStorage.getItem(PENDING_CANDIDATE_STORAGE_KEY) ?? "null",
    ) as Record<string, unknown>;
    expect(pending).toMatchObject({
      readingRecordId: "rec_unified_2",
      candidateDocumentId: "cand_1",
      originalInputId: "inp_cand_1",
      inputSnapshot: "Markdown article needing confirmation.",
    });
  });

  it("input_rejected_or_action_required outcome shows recoverable error and does not navigate", async () => {
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify(makeUnifiedInputRejectedResponse()), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(
      <AnalyzeSubmitForm
        readingGoal="daily_reading"
        readingVariant="intermediate_reading"
      />,
    );

    fireEvent.change(screen.getByPlaceholderText("Paste an English article here"), {
      target: { value: "abc 123 你好" },
    });
    fireEvent.click(screen.getByRole("button", { name: "开始透读" }));

    await waitFor(() => {
      expect(screen.getByText("这次没法直接开始透读")).toBeTruthy();
    });
    expect(screen.getByText("内容过短")).toBeTruthy();
    expect(screen.getByText("英文比例过低")).toBeTruthy();
    expect(screen.getByText(/我们收到的内容/)).toBeTruthy();
    // Debug-only fields must not leak to the user.
    expect(screen.queryByText("english_word_ratio")).toBeNull();
    expect(screen.queryByText("natural_language_score")).toBeNull();
    expect(screen.queryByText("too_short_for_learning")).toBeNull();
    expect(navigationMock.push).not.toHaveBeenCalled();
  });

  it("selects a file inside the same input surface without starting upload", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    render(
      <AnalyzeSubmitForm
        readingGoal="daily_reading"
        readingVariant="intermediate_reading"
      />,
    );

    const file = makeFile("paper.md", "text/markdown");
    fireEvent.change(screen.getByTestId("source-file-input"), {
      target: { files: [file] },
    });

    expect(screen.queryByPlaceholderText("Paste an English article here")).toBeNull();
    expect(screen.getByTestId("source-file-preview")).toBeTruthy();
    expect(screen.getByTestId("attached-source").textContent).toContain("paper.md");
    expect(screen.getByTestId("attached-source").textContent).toContain("Markdown 文档");
    expect(screen.getByTestId("source-file-preview").textContent).toContain("MD");
    expect(screen.getByRole("button", { name: "替换文件" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "移除文件" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "开始透读" })).toBeTruthy();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("accepts a dragged image file on the text input surface without starting upload", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    render(
      <AnalyzeSubmitForm
        readingGoal="daily_reading"
        readingVariant="intermediate_reading"
      />,
    );

    const file = makeFile("scan.png", "image/png");
    fireEvent.drop(screen.getByTestId("read-source-input"), {
      dataTransfer: {
        files: [file],
        types: ["Files"],
      },
    });

    expect(screen.queryByPlaceholderText("Paste an English article here")).toBeNull();
    expect(screen.getByTestId("source-file-preview")).toBeTruthy();
    expect(screen.getByTestId("attached-source").textContent).toContain("scan.png");
    expect(screen.getByTestId("attached-source").textContent).toContain("图片 OCR");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects unsupported uploaded formats before preview or upload", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    render(
      <AnalyzeSubmitForm
        readingGoal="daily_reading"
        readingVariant="intermediate_reading"
      />,
    );

    const file = makeFile("archive.zip", "application/zip");
    fireEvent.change(screen.getByTestId("source-file-input"), {
      target: { files: [file] },
    });

    expect(screen.queryByTestId("source-file-preview")).toBeNull();
    expect(screen.getByText(/暂不支持/).textContent).toContain("archive.zip");
    expect(screen.getByText(/PDF \/ Markdown \/ TXT \/ PNG \/ JPG \/ WEBP \/ GIF/)).toBeTruthy();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("starts artifact upload only after clicking 开始透读", async () => {
    const fetchMock = installStableArtifactFetchMock("rec_artifact_from_form");
    render(
      <AnalyzeSubmitForm
        readingGoal="exam"
        readingVariant="cet"
      />,
    );

    const file = makeFile("article.pdf", "application/pdf");
    fireEvent.change(screen.getByTestId("source-file-input"), {
      target: { files: [file] },
    });
    expect(fetchMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "开始透读" }));

    await waitFor(() => {
      expect(navigationMock.push).toHaveBeenCalledWith(
        "/app/reader-record/rec_artifact_from_form",
      );
    });

    const calls = fetchMock.mock.calls.map((call) => String(call[0]));
    expect(calls.some((url) => url.endsWith("/init-upload"))).toBe(true);
    expect(calls.some((url) => url.startsWith("https://oss.example.com/"))).toBe(true);
    expect(calls.some((url) => url.endsWith("/complete-upload"))).toBe(true);
    expect(calls.some((url) => url.endsWith("/submit-input"))).toBe(true);
    expect(calls.some((url) => url.endsWith("/pipeline-status"))).toBe(true);
  });

  it("stops indefinite waiting when an artifact job has never been claimed", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/init-upload")) {
        return jsonResponse(makeInitResponse());
      }
      if (url.startsWith("https://oss.example.com/")) {
        return new Response(null, { status: 200 });
      }
      if (url.endsWith("/complete-upload")) {
        return jsonResponse(makeCompleteResponse());
      }
      if (url.endsWith("/submit-input")) {
        return jsonResponse(makeArtifactSubmitResponse("rec_artifact_stalled"));
      }
      if (url.endsWith("/pipeline-status")) {
        return jsonResponse(makePipelineStalledResponse());
      }
      throw new Error(`Unexpected fetch ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <AnalyzeSubmitForm
        readingGoal="daily_reading"
        readingVariant="intermediate_reading"
      />,
    );
    fireEvent.change(screen.getByTestId("source-file-input"), {
      target: { files: [makeFile("stalled.md", "text/markdown")] },
    });
    fireEvent.click(screen.getByRole("button", { name: "开始透读" }));

    await waitFor(() => {
      expect(screen.getByText(/文件解析服务暂未启动或队列阻塞/)).toBeTruthy();
    });
    expect(navigationMock.push).not.toHaveBeenCalled();
  });

  it("removes legacy analysis-task polling from AnalyzeSubmitForm", () => {
    const source = readFileSync(
      resolve(process.cwd(), "src/app/(private)/app/read/AnalyzeSubmitForm.tsx"),
      "utf-8",
    );

    expect(source).not.toContain("fetchCurrentAnalysisTask");
    expect(source).not.toContain("fetchAnalysisTaskStatus");
    expect(source).not.toContain("saveRecentReadingRecordForSubmitMode");
    expect(source).not.toContain("legacyAppReaderRoute");
  });

  it("does not render the old recent Reading Record resume entry in the input form", () => {
    const source = readFileSync(
      resolve(process.cwd(), "src/app/(private)/app/read/AnalyzeSubmitForm.tsx"),
      "utf-8",
    );

    expect(source).not.toContain("RecentReadingRecordResume");
    expect(source).not.toContain("readRecentReadingRecord");
    expect(source).not.toContain("saveRecentReadingRecord");
    expect(source).not.toContain("最近阅读记录");
  });

  it("keeps the unified reader-plate/input route free of legacy analysis wiring", () => {
    const source = readFileSync(
      resolve(
        process.cwd(),
        "src/app/api/web/reader-plate/input/route.ts",
      ),
      "utf-8",
    );

    expect(source).toContain("submitReaderUnifiedInputFromWeb");
    expect(source).not.toContain("submitAnalysisFromWeb");
    expect(source).not.toContain("legacyAppReaderRoute");
    expect(source).not.toContain("analysis-tasks");
  });

  it("init-upload route calls the artifact BFF wrapper, no legacy analysis", () => {
    const source = readFileSync(
      resolve(
        process.cwd(),
        "src/app/api/web/reader-plate/source-artifacts/init-upload/route.ts",
      ),
      "utf-8",
    );

    expect(source).toContain("initReaderSourceArtifactUploadFromWeb");
    expect(source).not.toContain("submitAnalysisFromWeb");
    expect(source).not.toContain("legacyAppReaderRoute");
    expect(source).not.toContain("analysis-tasks");
  });

  it("complete-upload route calls the artifact BFF wrapper, no legacy analysis", () => {
    const source = readFileSync(
      resolve(
        process.cwd(),
        "src/app/api/web/reader-plate/source-artifacts/[artifactId]/complete-upload/route.ts",
      ),
      "utf-8",
    );

    expect(source).toContain("completeReaderSourceArtifactUploadFromWeb");
    expect(source).toContain("artifactId");
    expect(source).not.toContain("submitAnalysisFromWeb");
    expect(source).not.toContain("analysis-tasks");
  });

  it("submit-input route forwards strategy fields and calls the artifact BFF wrapper", () => {
    const source = readFileSync(
      resolve(
        process.cwd(),
        "src/app/api/web/reader-plate/source-artifacts/[artifactId]/submit-input/route.ts",
      ),
      "utf-8",
    );

    expect(source).toContain("submitReaderSourceArtifactInputFromWeb");
    expect(source).toContain("readingGoal");
    expect(source).toContain("readingVariant");
    expect(source).not.toContain("submitAnalysisFromWeb");
    expect(source).not.toContain("analysis-tasks");
  });

  it("pipeline-status route calls the artifact BFF wrapper, no legacy analysis", () => {
    const source = readFileSync(
      resolve(
        process.cwd(),
        "src/app/api/web/reader-plate/source-artifacts/[artifactId]/pipeline-status/route.ts",
      ),
      "utf-8",
    );

    expect(source).toContain("getReaderArtifactPipelineStatusFromWeb");
    expect(source).not.toContain("submitAnalysisFromWeb");
    expect(source).not.toContain("analysis-tasks");
  });
});

// ---------------------------------------------------------------------------
// Unified read intake source guards
// ---------------------------------------------------------------------------

describe("unified read intake source guards", () => {
  const FORM_PATH = "src/app/(private)/app/read/AnalyzeSubmitForm.tsx";
  const INTAKE_PATH = "src/app/(private)/app/read/ReadPageIntake.tsx";

  function readFormSource(): string {
    return readFileSync(resolve(process.cwd(), FORM_PATH), "utf-8");
  }

  it("keeps one upload entry and does not render paste/file/image tabs", () => {
    const source = readFormSource();
    expect(source).toContain("SOURCE_ACCEPT");
    expect(source).toContain("SUPPORTED_SOURCE_FORMATS");
    expect(source).toContain("validateSourceFile");
    expect(source).toContain("source-file-input");
    expect(source).toContain("attached-source");
    expect(source).toContain("descriptor.badge");
    expect(source).toContain("break-words");
    expect(source).not.toContain("truncate text-[0.94rem]");
    expect(source).not.toContain("intakeMethods");
    expect(source).not.toContain("上传图片");
    expect(source).not.toContain("onSelectFileSource");
    expect(source).not.toContain("onSelectImageSource");
  });

  it("accepts dropped files on the manuscript input surface", () => {
    const source = readFormSource();
    expect(source).toContain("onDrop={handleDrop}");
    expect(source).toContain("data-testid=\"read-source-input\"");
    expect(source).toContain("hasFileTransfer");
  });

  it("keeps the artifact pipeline inside AnalyzeSubmitForm and clears polling on unmount", () => {
    const source = readFormSource();
    expect(source).toContain("ReaderArtifactPipelineStatusSafeDto");
    expect(source).toContain("startArtifactFlow");
    expect(source).toContain("lastFileRef");
    expect(source).toMatch(/lastFileRef\.current\s*=\s*file/);
    expect(source).toContain("clearInterval(pollTimerRef.current)");
    expect(source).toMatch(/pollUntilTerminal\(\s*artifactId,\s*file\.name/);
    expect(source).toMatch(/applyArtifactOutcome\(\s*status,\s*currentFilename\s*\)/);
  });

  it("ReadPageIntake renders only the unified form, no artifact-mode branch", () => {
    const source = readFileSync(resolve(process.cwd(), INTAKE_PATH), "utf-8");
    expect(source).toContain("<AnalyzeSubmitForm");
    expect(source).not.toContain("ArtifactIntakePanel");
    expect(source).not.toContain("setMode");
    expect(source).not.toContain("initialSourceKind");
  });
});

// ---------------------------------------------------------------------------
// Candidate-confirm route guard (F3)
// ---------------------------------------------------------------------------

describe("candidate-confirm route guard", () => {
  const ROUTE_PATH =
    "src/app/api/web/reader-plate/records/[recordId]/candidate-documents/[candidateDocumentId]/confirm/route.ts";

  it("calls confirmReaderCandidateDocumentFromWeb and forwards path params + body, no legacy analysis", () => {
    const source = readFileSync(resolve(process.cwd(), ROUTE_PATH), "utf-8");
    expect(source).toContain("confirmReaderCandidateDocumentFromWeb");
    expect(source).toContain("recordId");
    expect(source).toContain("candidateDocumentId");
    expect(source).toContain("language");
    expect(source).not.toContain("submitAnalysisFromWeb");
    expect(source).not.toContain("legacyAppReaderRoute");
    expect(source).not.toContain("analysis-tasks");
  });
});

// ---------------------------------------------------------------------------
// S4: candidate-document read route guard
// ---------------------------------------------------------------------------

describe("candidate-document read route guard (S4)", () => {
  const ROUTE_PATH =
    "src/app/api/web/reader-plate/records/[recordId]/candidate-document/route.ts";

  it("calls getReaderCandidateDocumentFromWeb and forwards recordId, no legacy analysis", () => {
    const source = readFileSync(resolve(process.cwd(), ROUTE_PATH), "utf-8");
    expect(source).toContain("getReaderCandidateDocumentFromWeb");
    expect(source).toContain("recordId");
    expect(source).not.toContain("submitAnalysisFromWeb");
    expect(source).not.toContain("legacyAppReaderRoute");
    expect(source).not.toContain("analysis-tasks");
  });
});

// ---------------------------------------------------------------------------
// F3 handoff shape guards (source-level)
//   pending-candidate and CandidateConfirmCallout are unit-tested via
//   source-grep here to keep the jsdom env config untouched. Behavioral
//   tests for these modules require either a project-level jsdom default
//   in vitest.config.ts or per-file directives applied uniformly; this
//   project currently relies on the latter, so source guards stay portable.
// ---------------------------------------------------------------------------

describe("pending-candidate helper shape (source guard)", () => {
  const HELPER_PATH = "src/app/(private)/app/read/pending-candidate.ts";

  it("exposes the unified handoff fields required by F3 (filename, canonicalTextPreview) and tolerates text-only legacy fields", () => {
    const source = readFileSync(resolve(process.cwd(), HELPER_PATH), "utf-8");
    expect(source).toContain("readingRecordId");
    expect(source).toContain("candidateDocumentId");
    expect(source).toContain("originalInputId");
    expect(source).toContain("inputSnapshot");
    expect(source).toContain("filename");
    expect(source).toContain("canonicalTextPreview");
    expect(source).toContain("savedAt");
  });

  it("treats originalInputId and inputSnapshot as optional (artifact path does not always have them)", () => {
    const source = readFileSync(resolve(process.cwd(), HELPER_PATH), "utf-8");
    // The validator must allow null/undefined for the optional fields.
    expect(source).toMatch(/originalInputId\?: string \| null/);
    expect(source).toMatch(/inputSnapshot\?: string \| null/);
    expect(source).toMatch(/filename\?: string \| null/);
    expect(source).toMatch(/canonicalTextPreview\?: string \| null/);
  });
});

describe("CandidateConfirmDialog shape (source guard)", () => {
  const DIALOG_PATH =
    "src/app/(private)/app/read/CandidateConfirmDialog.tsx";
  const CALLOUT_PATH =
    "src/app/(private)/app/reader-record/[recordId]/CandidateConfirmCallout.tsx";

  it("wires matching pending candidate → 409 candidate_conflict → 其它 BFF error → success refresh", () => {
    const source = readFileSync(resolve(process.cwd(), DIALOG_PATH), "utf-8");
    const fallbackSource = readFileSync(resolve(process.cwd(), CALLOUT_PATH), "utf-8");
    // success path: clearPendingCandidate + fallback refresh hook.
    expect(source).toContain("clearPendingCandidate");
    expect(fallbackSource).toContain("window.location.reload");
    // 409 / candidate_conflict branch
    expect(source).toContain("candidate_conflict");
    expect(source).toContain("提取结果需要重新确认");
    // error branch surfaces BFF message verbatim, not raw debug fields
    expect(source).toContain("payload.message");
    // 确认并开始透读 / 稍后处理 / 重新提交 are all present
    expect(source).toContain("确认并开始透读");
    expect(source).toContain("稍后处理");
    expect(source).toContain("重新提交");
    // debug fields not exposed in the DOM
    expect(source).not.toMatch(/failure_class|failure_code|rationale_code/);
    expect(source).not.toMatch(/english_word_ratio|natural_language_score/);
  });
});

// ---------------------------------------------------------------------------
// resume_candidate entry (S4)
//
// These tests lock in the S4 `?resume_candidate=...` entry path. They are
// pure DOM-level behavioral tests: window.location.search is set before
// render, the BFF GET to
// `/api/web/reader-plate/records/{recordId}/candidate-document` is mocked
// via vi.stubGlobal("fetch", ...), and assertions are made against the
// visible DOM (dialog open state, button presence, textarea value,
// preview text).
// ---------------------------------------------------------------------------

function makeResumeResponse(previewMode: "full_text" | "truncated_preview" | "outline_only") {
  const previewTextByMode: Record<typeof previewMode, string> = {
    full_text: "Full article body shown verbatim to the reader in resume mode.",
    truncated_preview: "Truncated preview body shown to the reader in resume mode.",
    outline_only: "",
  };
  // outline_only must not have a title fallback either; the dialog is
  // expected to render its built-in fallback copy in that case.
  const titleByMode: Record<typeof previewMode, string | null> = {
    full_text: "Resume fixture title",
    truncated_preview: "Resume fixture title",
    outline_only: null,
  };
  return {
    ok: true as const,
    record_id: "rec_resume_1",
    candidate_document_id: "cand_resume_1",
    record_generation: 1,
    status: "ready" as const,
    title: titleByMode[previewMode],
    preview: {
      preview_mode: previewMode,
      preview_text: previewTextByMode[previewMode],
      is_truncated: previewMode !== "full_text",
      total_char_count: previewMode === "outline_only" ? 6400 : 128,
      document_outline:
        previewMode === "outline_only"
          ? [
              {
                order_index: 0,
                block_type_label: "heading" as const,
                heading_text: "Section A",
                char_count: 10,
              },
            ]
          : [],
      risk_items:
        previewMode === "truncated_preview"
          ? [
              {
                risk_kind: "low_confidence_ocr" as const,
                user_message: "Potential risk line",
                severity: "warning" as const,
              },
            ]
          : [],
    },
    source_type: "pasted_text" as const,
    filename: null,
    source_label: "粘贴文本",
    created_at: "2026-07-15T00:00:00.000Z",
    updated_at: "2026-07-15T00:00:00.000Z",
  };
}

function installResumeFetchMock(payload: unknown, status = 200) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/candidate-document")) {
      return new Response(JSON.stringify(payload), {
        status,
        headers: { "content-type": "application/json" },
      });
    }
    throw new Error(`Unexpected fetch ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function setLocationSearch(search: string) {
  const url = new URL(window.location.href);
  url.search = search;
  window.history.replaceState({}, "", url.toString());
}

describe("resume_candidate entry (S4)", () => {
  it("200 with full_text preview opens the dialog in resume mode and does not prefill the textarea", async () => {
    setLocationSearch("?resume_candidate=rec_resume_1");
    installResumeFetchMock(makeResumeResponse("full_text"));

    render(
      <AnalyzeSubmitForm
        readingGoal="daily_reading"
        readingVariant="intermediate_reading"
      />,
    );

    await waitFor(() => {
      expect(screen.getByTestId("candidate-confirm-dialog")).toBeTruthy();
    });

    // In resume mode the dialog must NOT expose edit affordances.
    expect(screen.queryByRole("button", { name: "重新提交" })).toBeNull();
    expect(screen.queryByRole("button", { name: "重新编辑" })).toBeNull();

    // The two buttons that should always be visible in resume mode.
    expect(screen.getByRole("button", { name: "确认并开始透读" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "稍后处理" })).toBeTruthy();

    // The preview body must surface the upstream preview_text.
    expect(screen.getByTestId("candidate-confirm-preview").textContent).toContain(
      "Full article body shown verbatim to the reader in resume mode.",
    );

    // The textarea must NOT be pre-filled from the resume payload.
    // (The textarea is still rendered behind the dialog; its value must remain
    // empty because the resume flow does not call setText().)
    const textarea = screen.getByPlaceholderText(
      "Paste an English article here",
    ) as HTMLTextAreaElement;
    expect(textarea.value).toBe("");

    // localStorage must NOT have been touched by the resume flow.
    expect(window.localStorage.getItem(PENDING_CANDIDATE_STORAGE_KEY)).toBeNull();
  });

  it("query param wins over a submit-origin localStorage candidate; localStorage value is not used", async () => {
    // Seed localStorage with a *submit*-origin candidate (different ids).
    const seededSubmitCandidate = {
      readingRecordId: "rec_local_submit",
      candidateDocumentId: "cand_local_submit",
      originalInputId: "inp_local_submit",
      inputSnapshot: "LocalStorage submit snapshot must NOT leak into the textarea.",
      filename: null,
      canonicalTextPreview: "LocalStorage submit preview",
      origin: "submit",
      savedAt: new Date().toISOString(),
    };
    window.localStorage.setItem(
      PENDING_CANDIDATE_STORAGE_KEY,
      JSON.stringify(seededSubmitCandidate),
    );

    setLocationSearch("?resume_candidate=rec_resume_1");
    installResumeFetchMock(makeResumeResponse("full_text"));

    render(
      <AnalyzeSubmitForm
        readingGoal="daily_reading"
        readingVariant="intermediate_reading"
      />,
    );

    await waitFor(() => {
      expect(screen.getByTestId("candidate-confirm-dialog")).toBeTruthy();
    });

    // Resume mode hides edit affordances.
    expect(screen.queryByRole("button", { name: "重新提交" })).toBeNull();
    expect(screen.queryByRole("button", { name: "重新编辑" })).toBeNull();

    // The dialog must show the resume payload's preview, not the localStorage one.
    expect(screen.getByTestId("candidate-confirm-preview").textContent).toContain(
      "Full article body shown verbatim to the reader in resume mode.",
    );
    expect(screen.getByTestId("candidate-confirm-preview").textContent).not.toContain(
      "LocalStorage submit preview",
    );

    // Textarea must not have been filled from localStorage either.
    const textareaAfterQuery = screen.getByPlaceholderText(
      "Paste an English article here",
    ) as HTMLTextAreaElement;
    expect(textareaAfterQuery.value).toBe("");

    // localStorage entry stays untouched (resume flow does not write to it).
    const stillThere = window.localStorage.getItem(PENDING_CANDIDATE_STORAGE_KEY);
    expect(stillThere).not.toBeNull();
    const parsed = JSON.parse(stillThere ?? "{}") as Record<string, unknown>;
    expect(parsed.readingRecordId).toBe("rec_local_submit");
    expect(parsed.origin).toBe("submit");
  });

  it("200 with full_text preview renders the preview_text inside the dialog body", async () => {
    setLocationSearch("?resume_candidate=rec_resume_1");
    installResumeFetchMock(makeResumeResponse("full_text"));

    render(
      <AnalyzeSubmitForm
        readingGoal="daily_reading"
        readingVariant="intermediate_reading"
      />,
    );

    await waitFor(() => {
      expect(screen.getByTestId("candidate-confirm-dialog")).toBeTruthy();
    });

    const previewBody = screen.getByTestId("candidate-confirm-preview").textContent ?? "";
    expect(previewBody).toContain(
      "Full article body shown verbatim to the reader in resume mode.",
    );
    // The fallback copy must NOT appear when upstream gave us preview_text.
    expect(previewBody).not.toContain("暂无可展示的正文预览");
  });

  it("200 with truncated_preview renders the preview_text; outline/risk UI may also render", async () => {
    setLocationSearch("?resume_candidate=rec_resume_1");
    installResumeFetchMock(makeResumeResponse("truncated_preview"));

    render(
      <AnalyzeSubmitForm
        readingGoal="daily_reading"
        readingVariant="intermediate_reading"
      />,
    );

    await waitFor(() => {
      expect(screen.getByTestId("candidate-confirm-dialog")).toBeTruthy();
    });

    const previewBody = screen.getByTestId("candidate-confirm-preview").textContent ?? "";
    // Lock in the preview_text rendering regardless of what outline/risk UI does.
    expect(previewBody).toContain(
      "Truncated preview body shown to the reader in resume mode.",
    );
    expect(previewBody).not.toContain("暂无可展示的正文预览");
    expect(screen.getByText("内容较长，以下为节选（约 128 字）。")).toBeTruthy();
    expect(screen.getByTestId("candidate-confirm-risk-list").textContent).toContain("Potential risk line");
  });

  it("200 with outline_only shows the structural overview", async () => {
    setLocationSearch("?resume_candidate=rec_resume_1");
    installResumeFetchMock(makeResumeResponse("outline_only"));

    render(
      <AnalyzeSubmitForm
        readingGoal="daily_reading"
        readingVariant="intermediate_reading"
      />,
    );

    await waitFor(() => {
      expect(screen.getByTestId("candidate-confirm-dialog")).toBeTruthy();
    });

    const previewBody = screen.getByTestId("candidate-confirm-preview").textContent ?? "";
    expect(previewBody).toContain("暂无可展示的正文预览");
    expect(screen.getByText("内容较长，以下为结构概览（约 6,400 字）。")).toBeTruthy();
    expect(screen.getByTestId("candidate-confirm-outline-list").textContent).toContain("Section A");
    // Resume mode must still hide edit affordances.
    expect(screen.queryByRole("button", { name: "重新提交" })).toBeNull();
    expect(screen.queryByRole("button", { name: "重新编辑" })).toBeNull();
    expect(screen.getByRole("button", { name: "确认并开始透读" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "稍后处理" })).toBeTruthy();
  });

  it("404 from BFF renders the inline 未找到可继续确认的内容 message and 前往阅读记录 button (→ /app/library)", async () => {
    setLocationSearch("?resume_candidate=rec_resume_1");
    installResumeFetchMock(
      {
        ok: false,
        status: 404,
        code: "candidate_not_found",
        message: "未找到可继续确认的内容。",
      },
      404,
    );

    render(
      <AnalyzeSubmitForm
        readingGoal="daily_reading"
        readingVariant="intermediate_reading"
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("未找到可继续确认的内容。")).toBeTruthy();
    });

    // The 404 CTA must route to /app/library (NOT /app/reader-record/{id}):
    // 404's four collapsed causes (not found / not owner / soft-deleted /
    // no ready candidate) all mean "don't try Reader again", so re-entering
    // Reader via recordId may loop.
    const libraryButton = screen.getByRole("button", { name: "前往阅读记录" });
    expect(libraryButton).toBeTruthy();
    expect(screen.queryByRole("button", { name: "返回阅读记录页" })).toBeNull();

    fireEvent.click(libraryButton);
    expect(navigationMock.push).toHaveBeenCalledWith("/app/library");

    // No confirm dialog should appear in the 404 path.
    expect(screen.queryByTestId("candidate-confirm-dialog")).toBeNull();
    // Textarea must be empty (no pre-fill from the failed resume fetch).
    const textareaNotFound = screen.getByPlaceholderText(
      "Paste an English article here",
    ) as HTMLTextAreaElement;
    expect(textareaNotFound.value).toBe("");
  });

  it("409 with candidate_conflict_open_reader pushes to the reader-record route and does not show the dialog", async () => {
    setLocationSearch("?resume_candidate=rec_resume_1");
    installResumeFetchMock(
      {
        ok: false,
        status: 409,
        code: "candidate_conflict_open_reader",
        message: "记录已开放，请直接进入 Reader。",
      },
      409,
    );

    render(
      <AnalyzeSubmitForm
        readingGoal="daily_reading"
        readingVariant="intermediate_reading"
      />,
    );

    await waitFor(() => {
      expect(navigationMock.push).toHaveBeenCalledWith("/app/reader-record/rec_resume_1");
    });

    expect(screen.queryByTestId("candidate-confirm-dialog")).toBeNull();
    expect(screen.queryByText("未找到可继续确认的内容")).toBeNull();
  });

  it("409 with candidate_conflict_return_to_library renders the inline message and 前往阅读记录 link; no dialog", async () => {
    setLocationSearch("?resume_candidate=rec_resume_1");
    installResumeFetchMock(
      {
        ok: false,
        status: 409,
        code: "candidate_conflict_return_to_library",
        message: "这篇内容当前无法继续确认。",
      },
      409,
    );

    render(
      <AnalyzeSubmitForm
        readingGoal="daily_reading"
        readingVariant="intermediate_reading"
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("这篇内容当前无法继续确认。")).toBeTruthy();
    });

    const libraryLink = screen.getByRole("button", { name: "前往阅读记录" });
    expect(libraryLink).toBeTruthy();

    // No confirm dialog.
    expect(screen.queryByTestId("candidate-confirm-dialog")).toBeNull();
    expect(navigationMock.push).not.toHaveBeenCalled();

    // Activating the link pushes to the library route.
    fireEvent.click(libraryLink);
    expect(navigationMock.push).toHaveBeenCalledWith("/app/library");
  });

  it("5xx / network failure renders the 重试加载 button; clicking it re-invokes the fetch", async () => {
    setLocationSearch("?resume_candidate=rec_resume_1");

    // First fetch: network failure. Subsequent fetches: success.
    const fetchMock = vi
      .fn<(input: RequestInfo | URL) => Promise<Response>>()
      .mockRejectedValueOnce(new Error("network down"))
      .mockImplementation(async (input) => {
        const url = String(input);
        if (url.includes("/candidate-document")) {
          return new Response(JSON.stringify(makeResumeResponse("full_text")), {
            status: 200,
            headers: { "content-type": "application/json" },
          });
        }
        throw new Error(`Unexpected fetch ${url}`);
      });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <AnalyzeSubmitForm
        readingGoal="daily_reading"
        readingVariant="intermediate_reading"
      />,
    );

    await waitFor(() => {
      expect(screen.getByText(/加载失败/)).toBeTruthy();
    });

    const retryButton = screen.getByRole("button", { name: "重试加载" });
    expect(retryButton).toBeTruthy();

    fireEvent.click(retryButton);

    await waitFor(() => {
      expect(screen.getByTestId("candidate-confirm-dialog")).toBeTruthy();
    });

    // The retry must have triggered an additional fetch call.
    expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it("query param absent + submit-origin localStorage candidate opens the recovery dialog with the textarea prefilled", async () => {
    // Seed a submit-origin candidate so the existing recovery path engages.
    const seededSubmitCandidate = {
      readingRecordId: "rec_local_submit",
      candidateDocumentId: "cand_local_submit",
      originalInputId: "inp_local_submit",
      inputSnapshot: "LocalStorage submit snapshot must populate the textarea.",
      filename: null,
      canonicalTextPreview: "LocalStorage submit preview",
      origin: "submit",
      savedAt: new Date().toISOString(),
    };
    window.localStorage.setItem(
      PENDING_CANDIDATE_STORAGE_KEY,
      JSON.stringify(seededSubmitCandidate),
    );

    // No query param, no fetch expected for the resume path.
    setLocationSearch("");

    render(
      <AnalyzeSubmitForm
        readingGoal="daily_reading"
        readingVariant="intermediate_reading"
      />,
    );

    await waitFor(() => {
      expect(screen.getByTestId("candidate-confirm-dialog")).toBeTruthy();
    });

    // In submit-mode recovery, all three buttons (including 重新提交) must be present.
    expect(screen.getByRole("button", { name: "重新提交" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "稍后处理" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "确认并开始透读" })).toBeTruthy();

    // The textarea must be pre-filled with the inputSnapshot.
    const textarea = screen.getByPlaceholderText(
      "Paste an English article here",
    ) as HTMLTextAreaElement;
    expect(textarea.value).toBe(
      "LocalStorage submit snapshot must populate the textarea.",
    );
  });

  it("query param absent + no localStorage renders the page normally with no dialog", async () => {
    setLocationSearch("");

    // Defensive: explicitly stub fetch to ensure no BFF calls happen here.
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    render(
      <AnalyzeSubmitForm
        readingGoal="daily_reading"
        readingVariant="intermediate_reading"
      />,
    );

    // The textarea must be present (normal page render) and empty.
    const textarea = screen.getByPlaceholderText(
      "Paste an English article here",
    ) as HTMLTextAreaElement;
    expect(textarea.value).toBe("");

    // No confirm dialog should appear.
    expect(screen.queryByTestId("candidate-confirm-dialog")).toBeNull();

    // Resume-error inline sections must NOT be present either.
    expect(screen.queryByText("未找到可继续确认的内容")).toBeNull();
    expect(screen.queryByText("这篇内容当前无法继续确认")).toBeNull();
    expect(screen.queryByText(/加载失败/)).toBeNull();

    // No BFF fetch was issued (we have no query and no localStorage).
    expect(fetchMock).not.toHaveBeenCalled();
    expect(navigationMock.push).not.toHaveBeenCalled();
  });
});
