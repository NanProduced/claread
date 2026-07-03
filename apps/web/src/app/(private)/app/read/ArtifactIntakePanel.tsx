"use client";

import { ArrowRight, FileUp, RefreshCw, X } from "lucide-react";
import type { Route } from "next";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/primitives/button";
import type {
  ReaderArtifactPipelineStatusSafeDto,
} from "@/lib/reader-orchestration/status-mapper";
import type {
  ReaderArtifactPipelineStatusResult,
  ReaderPlateBffError,
  ReaderSourceArtifactUploadCompleteResult,
  ReaderSourceArtifactUploadInitResult,
  ReaderSourceArtifactSubmitInputResult,
} from "@/services/bff/reader-plate";
import { appReadingRecordRoute } from "@/lib/routes";
import type { ReaderRecordReadingGoal, ReaderRecordReadingVariant } from "@/lib/reading-defaults";

const ACCEPT = ".pdf,.txt,.md,.markdown,image/png,image/jpeg,image/jpg,image/webp,image/gif";
const MAX_BYTES = 25 * 1024 * 1024; // 25 MB sanity ceiling
const POLL_INTERVAL_MS = 3000;

type Stage =
  | { kind: "idle" }
  | { kind: "uploading"; message: string }
  | { kind: "polling"; message: string }
  | { kind: "candidate"; readingRecordId: string; filename: string }
  | { kind: "revise"; message: string; filename: string }
  | { kind: "error"; message: string; filename: string }
  | { kind: "done" };

interface ArtifactIntakePanelProps {
  readingGoal: ReaderRecordReadingGoal;
  readingVariant: ReaderRecordReadingVariant;
  onUseTextMode: () => void;
  /**
   * Test-only hook: receives the internal `startArtifactFlow(file)` function so
   * tests in jsdom can drive the artifact pipeline without depending on
   * `<input type="file">` synthetic-event propagation (which is broken in
   * jsdom for hidden inputs). Undefined in production.
   */
  __testStartArtifactFlow?: (start: (file: File) => Promise<void>) => void;
}

type PipelineOutcome = ReaderArtifactPipelineStatusSafeDto["outcome"];
type PipelineNextAction = ReaderArtifactPipelineStatusSafeDto["next_action"];

function isBffError(value: ReaderArtifactPipelineStatusResult): value is ReaderPlateBffError {
  return value.ok === false;
}

function describeNextAction(action: PipelineNextAction, outcome: PipelineOutcome): string {
  if (action === "wait_for_worker" || action === "retry_later") {
    return "继续等待，文档还在后台处理中";
  }
  if (action === "open_reader" || outcome === "stable_document_ready") {
    return "文档已就绪，正在打开 Reader";
  }
  if (action === "confirm_candidate_document" || outcome === "candidate_document_required") {
    return "已生成候选文档，需要你确认后再开始阅读";
  }
  if (action === "revise_input" || outcome === "input_rejected_or_action_required") {
    return "系统没能识别这份文件，可以换一份文件或改用粘贴文本";
  }
  if (action === "show_error") {
    return "处理过程中出现错误，可以重试或重新选择文件";
  }
  if (action === "submit_input") {
    return "正在提交文件";
  }
  if (action === "complete_upload") {
    return "正在确认上传";
  }
  return "处理中";
}

function isTerminalOutcome(outcome: PipelineOutcome): boolean {
  return (
    outcome === "stable_document_ready" ||
    outcome === "candidate_document_required" ||
    outcome === "input_rejected_or_action_required" ||
    outcome === "extraction_failed" ||
    outcome === "materialization_failed"
  );
}

function isTerminalAction(action: PipelineNextAction): boolean {
  return (
    action === "open_reader" ||
    action === "confirm_candidate_document" ||
    action === "revise_input" ||
    action === "show_error"
  );
}

function summarizeOutcome(outcome: PipelineOutcome): string {
  switch (outcome) {
    case "extraction_failed":
      return "文本提取失败";
    case "materialization_failed":
      return "排版失败";
    default:
      return "处理失败";
  }
}

export function ArtifactIntakePanel({ readingGoal, readingVariant, onUseTextMode, __testStartArtifactFlow }: ArtifactIntakePanelProps) {
  const router = useRouter();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const lastFileRef = useRef<File | null>(null);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [stage, setStage] = useState<Stage>({ kind: "idle" });

  useEffect(() => {
    if (__testStartArtifactFlow) {
      __testStartArtifactFlow(startArtifactFlow);
    }
    // startArtifactFlow identity is stable within a single mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [__testStartArtifactFlow]);

  useEffect(() => {
    return () => {
      if (pollTimerRef.current !== null) {
        clearInterval(pollTimerRef.current);
        pollTimerRef.current = null;
      }
    };
  }, []);

  function stopPolling() {
    if (pollTimerRef.current !== null) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }

  function resetToIdle() {
    stopPolling();
    lastFileRef.current = null;
    setStage({ kind: "idle" });
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  }

  // POST routes relay the BFF result as a flat shape:
  //   ({ ok: true } & <dto>) | { ok: false; status; message }
  // We do NOT unwrap a `data` field — the shape IS flat at the wire.
  async function postInitUpload(body: unknown): Promise<ReaderSourceArtifactUploadInitResult> {
    const response = await fetch("/api/web/reader-plate/source-artifacts/init-upload", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    return (await response.json()) as ReaderSourceArtifactUploadInitResult;
  }

  async function postCompleteUpload(
    artifactId: string,
    body: unknown,
  ): Promise<ReaderSourceArtifactUploadCompleteResult> {
    const response = await fetch(
      `/api/web/reader-plate/source-artifacts/${encodeURIComponent(artifactId)}/complete-upload`,
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      },
    );
    return (await response.json()) as ReaderSourceArtifactUploadCompleteResult;
  }

  async function postSubmitInput(
    artifactId: string,
    body: unknown,
  ): Promise<ReaderSourceArtifactSubmitInputResult> {
    const response = await fetch(
      `/api/web/reader-plate/source-artifacts/${encodeURIComponent(artifactId)}/submit-input`,
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      },
    );
    return (await response.json()) as ReaderSourceArtifactSubmitInputResult;
  }

  async function fetchPipelineStatus(artifactId: string): Promise<ReaderArtifactPipelineStatusResult> {
    const response = await fetch(
      `/api/web/reader-plate/source-artifacts/${encodeURIComponent(artifactId)}/pipeline-status`,
    );
    return (await response.json()) as ReaderArtifactPipelineStatusResult;
  }

  async function pollUntilTerminal(artifact: string, currentFilename: string) {
    const tick = async () => {
      const result = await fetchPipelineStatus(artifact);
      if (isBffError(result)) {
        stopPolling();
        setStage({
          kind: "error",
          filename: currentFilename,
          message: result.message || "查询处理进度失败，请稍后重试。",
        });
        return;
      }
      // Flat shape: result.outcome / result.next_action / result.record are top-level.
      const status = result;
      setStage({ kind: "polling", message: describeNextAction(status.next_action, status.outcome) });
      if (isTerminalOutcome(status.outcome) || isTerminalAction(status.next_action)) {
        stopPolling();
        applyOutcome(status, currentFilename);
      }
    };
    void tick();
    pollTimerRef.current = setInterval(() => {
      void tick();
    }, POLL_INTERVAL_MS);
  }

  function applyOutcome(status: ReaderArtifactPipelineStatusSafeDto, currentFilename: string) {
    const { outcome, next_action: nextAction, record } = status;
    if (outcome === "stable_document_ready" || nextAction === "open_reader") {
      const readingRecordId = record?.reading_record_id;
      if (!readingRecordId) {
        setStage({
          kind: "error",
          filename: currentFilename,
          message: "文档已就绪，但缺少阅读记录信息，请重新提交。",
        });
        return;
      }
      setStage({ kind: "done" });
      router.push(appReadingRecordRoute(readingRecordId) as Route);
      return;
    }
    if (outcome === "candidate_document_required" || nextAction === "confirm_candidate_document") {
      const readingRecordId = record?.reading_record_id;
      if (!readingRecordId) {
        setStage({
          kind: "error",
          filename: currentFilename,
          message: "已生成候选文档，但缺少阅读记录信息。",
        });
        return;
      }
      setStage({
        kind: "candidate",
        readingRecordId,
        filename: currentFilename,
      });
      return;
    }
    if (outcome === "input_rejected_or_action_required" || nextAction === "revise_input") {
      setStage({
        kind: "revise",
        filename: currentFilename,
        message: "系统没能识别这份文件，可以换一份文件或改用粘贴文本。",
      });
      return;
    }
    if (nextAction === "show_error" || outcome === "extraction_failed" || outcome === "materialization_failed") {
      setStage({
        kind: "error",
        filename: currentFilename,
        message: `处理失败（${summarizeOutcome(outcome)}），可以重试或重新选择文件。`,
      });
    }
  }

  async function startArtifactFlow(file: File) {
    if (file.size > MAX_BYTES) {
      setStage({
        kind: "error",
        filename: file.name,
        message: `文件太大（${(file.size / 1024 / 1024).toFixed(1)} MB），请选择 25 MB 以内的文件。`,
      });
      return;
    }

    lastFileRef.current = file;
    setStage({ kind: "uploading", message: "正在准备上传…" });

    // Flat shape: result is the init payload itself, no .data wrapper.
    const initResult = await postInitUpload({
      artifactKind: "original_upload",
      sourceFilename: file.name,
      contentType: file.type || "application/octet-stream",
      byteSize: file.size,
    });
    if (!initResult.ok) {
      setStage({
        kind: "error",
        filename: file.name,
        message: initResult.message || "无法开始上传，请稍后重试。",
      });
      return;
    }
    // Flat access: initResult.artifact_id / initResult.presigned_url / initResult.headers
    const artifact = initResult.artifact_id;
    const presignedUrl = initResult.presigned_url;
    const presignedMethod = initResult.presigned_method ?? "PUT";
    const presignedHeaders = initResult.headers ?? {};
    if (!artifact || !presignedUrl) {
      setStage({
        kind: "error",
        filename: file.name,
        message: "上传服务暂时不可用，请稍后重试或重新选择文件。",
      });
      return;
    }

    setStage({ kind: "uploading", message: "正在上传文件…" });

    let putOk = false;
    try {
      const putResponse = await fetch(presignedUrl, {
        method: presignedMethod,
        headers: presignedHeaders,
        body: file,
      });
      putOk = putResponse.ok;
    } catch {
      putOk = false;
    }
    if (!putOk) {
      setStage({
        kind: "error",
        filename: file.name,
        message: "文件上传失败，请检查网络后重试。",
      });
      return;
    }

    const completeResult = await postCompleteUpload(artifact, {
      contentType: file.type || "application/octet-stream",
      byteSize: file.size,
    });
    if (!completeResult.ok) {
      setStage({
        kind: "error",
        filename: file.name,
        message: completeResult.message || "确认上传失败，请稍后重试。",
      });
      return;
    }

    setStage({ kind: "uploading", message: "正在提交文件…" });

    const submitResult = await postSubmitInput(artifact, {
      title: file.name,
      language: "en",
      readingGoal,
      readingVariant,
    });
    if (!submitResult.ok) {
      setStage({
        kind: "error",
        filename: file.name,
        message: submitResult.message || "提交文件失败，请稍后重试。",
      });
      return;
    }

    setStage({ kind: "polling", message: "已提交，正在等待后台处理…" });
    await pollUntilTerminal(artifact, file.name);
  }

  function handleFileChange(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) {
      return;
    }
    void startArtifactFlow(file);
  }

  function openFilePicker() {
    if (stage.kind === "uploading" || stage.kind === "polling") {
      return;
    }
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
    fileInputRef.current?.click();
  }

  function retryLast() {
    const lastFile = lastFileRef.current;
    if (!lastFile) {
      openFilePicker();
      return;
    }
    void startArtifactFlow(lastFile);
  }

  const lastFilename = lastFileRef.current?.name ?? null;

  return (
    <div className="flex min-h-0 w-full flex-col gap-4">
      <input
        ref={fileInputRef}
        type="file"
        accept={ACCEPT}
        onChange={handleFileChange}
        className="sr-only"
        data-testid="artifact-file-input"
        tabIndex={-1}
      />

      <div className="flex min-h-0 flex-col items-center justify-center gap-4 rounded-[10px] border border-dashed border-hairline/80 bg-[linear-gradient(180deg,rgba(251,247,238,0.45),rgba(251,247,238,0.12)_48%,rgba(251,247,238,0)_100%)] px-6 py-12 text-center">
        <FileUp aria-hidden className="h-7 w-7 text-subtle" />
        <div className="space-y-1">
          <p className="font-sans text-[0.96rem] font-semibold text-ink">支持 PDF / 图片 / Markdown / 文本</p>
          <p className="font-sans text-[0.78rem] text-muted">最大 25 MB · 文件只在你的浏览器里处理后再上传</p>
        </div>
        <Button
          type="button"
          variant="primary-ink"
          size="md"
          onClick={openFilePicker}
          disabled={stage.kind === "uploading" || stage.kind === "polling"}
        >
          {lastFilename ? "重新选择文件" : "选择文件"}
        </Button>
      </div>

      {stage.kind === "uploading" || stage.kind === "polling" ? (
        <section
          role="status"
          aria-live="polite"
          data-testid="artifact-progress"
          className="rounded-[14px] border border-hairline/70 bg-surface/42 px-4 py-3 font-sans text-[0.82rem] font-medium text-ink"
        >
          <p className="font-semibold">{lastFilename ?? ""}</p>
          <p className="mt-1 text-[0.78rem] text-muted">{stage.message}</p>
        </section>
      ) : null}

      {stage.kind === "candidate" ? (
        <section
          role="status"
          aria-live="polite"
          data-testid="artifact-candidate"
          className="rounded-[14px] border border-hairline/70 bg-surface/42 px-4 py-3 font-sans text-[0.82rem] font-medium text-ink"
        >
          <p className="font-semibold">
            <span data-testid="artifact-filename">{stage.filename}</span>
            ：已生成候选文档，需要你确认后开始阅读
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button
              type="button"
              variant="primary-ink"
              size="sm"
              onClick={() => router.push(appReadingRecordRoute(stage.readingRecordId) as Route)}
            >
              去阅读记录确认
              <ArrowRight aria-hidden className="ml-1 h-3.5 w-3.5" />
            </Button>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={() => setStage({ kind: "idle" })}
            >
              稍后处理
            </Button>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={resetToIdle}
            >
              重新选择文件
            </Button>
          </div>
        </section>
      ) : null}

      {stage.kind === "revise" ? (
        <section
          role="status"
          aria-live="polite"
          data-testid="artifact-revise"
          className="rounded-[14px] border border-hairline/70 bg-surface/42 px-4 py-3 font-sans text-[0.82rem] font-medium text-red-700"
        >
          <p className="font-semibold">
            <span data-testid="artifact-filename">{stage.filename}</span>
            ：{stage.message}
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button type="button" variant="secondary" size="sm" onClick={openFilePicker}>
              重新选择文件
              <RefreshCw aria-hidden className="ml-1 h-3.5 w-3.5" />
            </Button>
            <Button type="button" variant="secondary" size="sm" onClick={onUseTextMode}>
              改用粘贴文本
            </Button>
          </div>
        </section>
      ) : null}

      {stage.kind === "error" ? (
        <section
          role="status"
          aria-live="polite"
          data-testid="artifact-error"
          className="rounded-[14px] border border-hairline/70 bg-surface/42 px-4 py-3 font-sans text-[0.82rem] font-medium text-red-700"
        >
          <p className="font-semibold">
            <span data-testid="artifact-filename">{stage.filename}</span>
            ：{stage.message}
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={retryLast}
            >
              重试
            </Button>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={resetToIdle}
            >
              重新选择文件
              <RefreshCw aria-hidden className="ml-1 h-3.5 w-3.5" />
            </Button>
            <Button type="button" variant="secondary" size="sm" onClick={onUseTextMode}>
              改用粘贴文本
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              aria-label="关闭提示"
              onClick={() => setStage({ kind: "idle" })}
            >
              <X aria-hidden className="h-3.5 w-3.5" />
            </Button>
          </div>
        </section>
      ) : null}
    </div>
  );
}