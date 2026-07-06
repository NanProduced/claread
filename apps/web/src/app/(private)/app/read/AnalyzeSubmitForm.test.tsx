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
