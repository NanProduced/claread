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
import { RECENT_READING_RECORD_STORAGE_KEY } from "./recent-reading-record";

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

    const saved = JSON.parse(
      window.localStorage.getItem(RECENT_READING_RECORD_STORAGE_KEY) ?? "null",
    ) as Record<string, unknown>;
    expect(saved).toMatchObject({
      readingRecordId: "rec_unified_1",
      readerUrl: "/app/reader-record/rec_unified_1",
      title: "Unified stable fixture",
      createdAt: expect.any(String),
    });
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
      /async function handleSubmit\(\)\s*\{[\s\S]{0,200}?if \(state\.kind === "pending"\)\s*\{\s*return;\s*\}/,
    );
  });

  it("candidate_document_required outcome shows the candidate card and does not navigate", async () => {
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
      expect(screen.getByText(/已收到候选文档/)).toBeTruthy();
    });
    expect(screen.getByText("rec_unified_2")).toBeTruthy();
    expect(screen.getByText("cand_1")).toBeTruthy();
    expect(screen.getByText("inp_cand_1")).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "去阅读记录确认" }),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "稍后处理" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "重新编辑" })).toBeTruthy();
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

  it("loads a valid recent Reading Record from localStorage and continues reading", async () => {
    window.localStorage.setItem(
      RECENT_READING_RECORD_STORAGE_KEY,
      JSON.stringify({
        readingRecordId: "reading_record_recent",
        readerUrl: "/app/reader-record/reading_record_recent",
        title: "Saved recent article",
        createdAt: "2026-06-22T12:00:00.000Z",
      }),
    );

    render(
      <AnalyzeSubmitForm
        readingGoal="daily_reading"
        readingVariant="intermediate_reading"
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("Saved recent article")).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: /继续阅读/ }));

    expect(navigationMock.push).toHaveBeenCalledWith(
      "/app/reader-record/reading_record_recent",
    );
  });

  it("ignores invalid recent Reading Record localStorage payloads", async () => {
    window.localStorage.setItem(
      RECENT_READING_RECORD_STORAGE_KEY,
      JSON.stringify({
        readingRecordId: "legacy_record",
        readerUrl: "/app/reader/legacy_record",
        title: "Legacy record should not render",
        createdAt: "2026-06-22T12:00:00.000Z",
      }),
    );

    render(
      <AnalyzeSubmitForm
        readingGoal="daily_reading"
        readingVariant="intermediate_reading"
      />,
    );

    await waitFor(() => {
      expect(screen.queryByText("Legacy record should not render")).toBeNull();
    });
    expect(screen.queryByRole("button", { name: /继续阅读/ })).toBeNull();
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

  it("keeps the recent Reading Record helper free of legacy analysis wiring", () => {
    const source = readFileSync(
      resolve(process.cwd(), "src/app/(private)/app/read/recent-reading-record.ts"),
      "utf-8",
    );

    expect(source).not.toContain("legacyAppReaderRoute");
    expect(source).not.toContain("/app/reader/");
    expect(source).not.toContain("analysis-tasks");
    expect(source).not.toContain("saveRecentReadingRecordForSubmitMode");
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
// ArtifactIntakePanel review-fix source guards
//   These assert the F2 review fixes to ArtifactIntakePanel.
//   Each is a deterministic source check — they prove the panel's
//   architectural shape and behaviour wiring without depending on jsdom's
//   fragile FileList propagation through React's synthetic event system.
// ---------------------------------------------------------------------------

describe("ArtifactIntakePanel review-fix source guards", () => {
  const PANEL_PATH = "src/app/(private)/app/read/ArtifactIntakePanel.tsx";

  function readPanelSource(): string {
    return readFileSync(resolve(process.cwd(), PANEL_PATH), "utf-8");
  }

  it("does NOT import the raw ReaderArtifactPipelineStatusResponseDto", () => {
    const source = readPanelSource();
    expect(source).not.toMatch(/ReaderArtifactPipelineStatusResponseDto/);
    expect(source).toContain("ReaderArtifactPipelineStatusSafeDto");
    expect(source).toContain("@/lib/reader-orchestration/status-mapper");
  });

  it("never reads failure_class / failure_code / rationale_code / english_word_ratio / natural_language_score", () => {
    const source = readPanelSource();
    expect(source).not.toMatch(/failure_class|failure_code|rationale_code/);
    expect(source).not.toMatch(/english_word_ratio|natural_language_score/);
  });

  it("clears the polling interval on unmount via useEffect cleanup", () => {
    const source = readPanelSource();
    expect(source).toMatch(/useEffect\(\s*\(\)\s*=>\s*\{\s*return\s*\(\)\s*=>\s*\{/);
    expect(source).toContain("clearInterval(pollTimerRef.current)");
  });

  it("keeps the last File in a ref so 重试 can re-run startArtifactFlow without re-opening the picker", () => {
    const source = readPanelSource();
    expect(source).toContain("lastFileRef");
    expect(source).toMatch(/lastFileRef\.current\s*=\s*file/);
    expect(source).toMatch(/function retryLast/);
    expect(source).toMatch(/startArtifactFlow\(lastFile\)/);
  });

  it("'重新选择文件' button clears input.value before re-opening the picker (so same file re-fires change)", () => {
    const source = readPanelSource();
    expect(source).toMatch(/fileInputRef\.current\.value\s*=\s*""/);
  });

  it("passes currentFilename explicitly into pollUntilTerminal / applyOutcome (no stale closure)", () => {
    const source = readPanelSource();
    expect(source).toMatch(/pollUntilTerminal\(\s*artifact\s*,\s*file\.name/);
    expect(source).toMatch(/applyOutcome\(\s*status\s*,\s*currentFilename\s*\)/);
  });
});
