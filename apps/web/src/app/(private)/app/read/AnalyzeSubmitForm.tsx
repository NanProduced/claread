"use client";

import { AlertTriangle, ArrowRight, ChevronDown, FileText, FileUp, ImageIcon, RefreshCw, X } from "lucide-react";
import Image from "next/image";
import type { Route } from "next";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState, type ChangeEvent, type DragEvent, type ReactNode } from "react";
import { ReadingPlanFields } from "@/components/composed";
import { Button } from "@/components/primitives/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/primitives/popover";
import { cn } from "@/lib/cn";
import {
  isArtifactPipelineWorkerStalled,
  type ReaderArtifactPipelineStatusSafeDto,
} from "@/lib/reader-orchestration/status-mapper";
import {
  formatReadingPlanSummary,
  type ReadingDefaultState,
  type ReaderRecordReadingGoal,
  type ReaderRecordReadingVariant,
  normalizeReaderRecordReadingDefaults,
} from "@/lib/reading-defaults";
import { appLibraryRoute, appReaderRoute } from "@/lib/routes";
import type { ReaderUnifiedInputSubmitResponseDto } from "@/types/api/reader-plate";
import type {
  ReaderArtifactPipelineStatusResult,
  ReaderCandidateDocumentReadResult,
  ReaderPlateBffError,
  ReaderSourceArtifactSubmitInputResult,
  ReaderSourceArtifactUploadCompleteResult,
  ReaderSourceArtifactUploadInitResult,
} from "@/services/bff/reader-plate";
import {
  clearPendingCandidate,
  readPendingCandidate,
  savePendingCandidate,
  type PendingCandidate,
} from "./pending-candidate";
import { CandidateConfirmDialog } from "./CandidateConfirmDialog";
import { ContentCheckPanel } from "./ContentCheckPanel";
import { useReadPageUi } from "./read-page-ui";
import {
  MarkdownTextInput,
  type MarkdownTextInputHandle,
} from "./MarkdownTextInput";
import {
  lintMarkdownInput,
  summarizeLintWarnings,
  type MarkdownLintResult,
} from "./markdown-lint";
import {
  readPageSubmitEndpoint,
  readPageSubmitRequestBody,
} from "./submit-mode";
import { formatApproxWordCount } from "@/lib/word-count";

type SubmitState =
  | { kind: "idle" }
  | { kind: "pending"; message: string }
  | { kind: "artifact-uploading"; filename: string; message: string }
  | { kind: "artifact-polling"; filename: string; message: string }
  | { kind: "success"; message: string }
  | { kind: "error"; message: string }
  | {
      kind: "candidate";
      candidate: PendingCandidate;
    }
  | {
      /**
       * L2 同页 Content Check（替代候选确认模态的默认流程）。
       * fallbackCandidate 用于该 record 无 Confirmed Source 行（L2 前存量
       * 记录）时回退旧 CandidateConfirmDialog 流程。
       */
      kind: "content-check";
      recordId: string;
      filename: string | null;
      inputSnapshot: string | null;
      origin: "submit" | "resume";
      fallbackCandidate: PendingCandidate | null;
    }
  | {
      kind: "rejected";
      reasons: string[];
      preview: string;
    }
  | { kind: "resume-not-found"; recordId: string; message: string }
  | { kind: "resume-return-to-library"; message: string }
  | { kind: "resume-failed"; recordId: string; message: string };

type ReaderCandidateResumePayload = ReaderCandidateDocumentReadResult;

type UnifiedSubmitPayload =
  | ({ ok: true } & ReaderUnifiedInputSubmitResponseDto)
  | ReaderPlateBffError;

type ArtifactSourceKind = "file" | "image";
type SourceFileKind = "pdf" | "markdown" | "text" | "image";

interface SourceFileDescriptor {
  kind: SourceFileKind;
  sourceKind: ArtifactSourceKind;
}

interface AttachedSource {
  file: File;
  sourceKind: ArtifactSourceKind;
  descriptor: SourceFileDescriptor;
}

type PipelineOutcome = ReaderArtifactPipelineStatusSafeDto["outcome"];
type PipelineNextAction = ReaderArtifactPipelineStatusSafeDto["next_action"];

const LOADING_MESSAGES = [
  "正在梳理文章结构",
  "正在识别关键表达",
  "正在整理语法线索",
  "正在生成精读批注",
  "正在准备阅读视图",
];

const SOURCE_ACCEPT = ".pdf,.txt,.md,.markdown,image/png,image/jpeg,image/jpg,image/webp,image/gif";
const SUPPORTED_SOURCE_FORMATS = "PDF / Markdown / TXT / PNG / JPG / WEBP / GIF";
const MAX_ARTIFACT_BYTES = 25 * 1024 * 1024;
const POLL_INTERVAL_MS = 3000;
const CLIENT_MARKDOWN_LINT_EXTENSIONS = new Set(["txt", "md", "markdown"]);
const SUPPORTED_IMAGE_EXTENSIONS = new Set(["png", "jpg", "jpeg", "webp", "gif"]);
const SUPPORTED_IMAGE_MIME_TYPES = new Set([
  "image/png",
  "image/jpeg",
  "image/jpg",
  "image/webp",
  "image/gif",
]);

/**
 * 阅读方案的两个维度与解析链路合同一一对应（不改 API）：
 * - 阅读目标 ↔ reading_goal："daily_reading" 日常阅读 / "exam" 备考精读
 *   （academic 已被新版解析链路移除，不出现在选项中）。
 * - 阅读方案（日常阅读层级 / 备考目标）↔ reading_variant：日常阅读为入门/进阶/精读，
 *   备考精读为高考/四六级/考研/专四专八/雅思托福。
 * 选项文案统一取自 reading-defaults 的 READER_RECORD_* 合同常量。
 */
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
    return "已提取出候选正文，需要你确认后再开始阅读";
  }
  if (action === "revise_input" || outcome === "input_rejected_or_action_required") {
    return "暂时没能识别这份来源，可以换一个文件或改用粘贴文本";
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

function formatFileSize(bytes: number): string {
  if (bytes < 1024 * 1024) {
    return `${Math.max(1, Math.round(bytes / 1024)).toLocaleString("en-US")} KB`;
  }
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function sourceFileExtension(filename: string): string {
  const dotIndex = filename.lastIndexOf(".");
  if (dotIndex < 0 || dotIndex === filename.length - 1) {
    return "";
  }
  return filename.slice(dotIndex + 1).trim().toLowerCase();
}

function normalizedMimeType(file: File): string {
  return (file.type || "").split(";")[0]?.trim().toLowerCase() ?? "";
}

function describeSourceFile(file: File): SourceFileDescriptor | null {
  const extension = sourceFileExtension(file.name);
  const mimeType = normalizedMimeType(file);

  if (extension === "pdf" || mimeType === "application/pdf") {
    return { kind: "pdf", sourceKind: "file" };
  }

  if (
    extension === "md" ||
    extension === "markdown" ||
    mimeType === "text/markdown" ||
    mimeType === "text/x-markdown"
  ) {
    return { kind: "markdown", sourceKind: "file" };
  }

  if (extension === "txt" || mimeType === "text/plain") {
    return { kind: "text", sourceKind: "file" };
  }

  if (SUPPORTED_IMAGE_EXTENSIONS.has(extension) || SUPPORTED_IMAGE_MIME_TYPES.has(mimeType)) {
    return { kind: "image", sourceKind: "image" };
  }

  return null;
}

function validateSourceFile(file: File):
  | { ok: true; descriptor: SourceFileDescriptor }
  | { ok: false; message: string } {
  const descriptor = describeSourceFile(file);
  if (!descriptor) {
    const filename = file.name ? `“${file.name}”` : "这个文件";
    return {
      ok: false,
      message: `暂不支持 ${filename}。请上传 ${SUPPORTED_SOURCE_FORMATS}。`,
    };
  }

  if (file.size > MAX_ARTIFACT_BYTES) {
    return {
      ok: false,
      message: `文件太大（${(file.size / 1024 / 1024).toFixed(1)} MB），请选择 25 MB 以内的文件。`,
    };
  }

  return { ok: true, descriptor };
}

function shouldLintSourceFileInBrowser(file: File): boolean {
  return CLIENT_MARKDOWN_LINT_EXTENSIONS.has(sourceFileExtension(file.name));
}

function hasFileTransfer(dataTransfer: DataTransfer): boolean {
  return Array.from(dataTransfer.types ?? []).includes("Files");
}

function ApertureCornerSubmitButton({
  isPending,
  isReady,
  onClick,
}: {
  isPending: boolean;
  isReady: boolean;
  onClick: () => void;
}) {
  return (
    <Button
      variant="primary-ink"
      className={cn("aperture-corner-cta group/aperture font-sans disabled:cursor-not-allowed", isReady && "aperture-corner-cta--ready")}
      data-pending={isPending ? "true" : "false"}
      data-ready={isReady ? "true" : "false"}
      disabled={isPending || !isReady}
      onClick={onClick}
    >
      <span className="aperture-corner-cta__mark" aria-hidden="true">
        <span className="aperture-corner-cta__asset aperture-corner-cta__asset--default" />
        <span className="aperture-corner-cta__asset aperture-corner-cta__asset--focus" />
      </span>
      <span className="aperture-corner-cta__content">
        <span className="aperture-corner-cta__label">
          {isPending ? "透读中..." : "开始透读"}
        </span>
        {!isPending ? (
          <ArrowRight aria-hidden className="aperture-corner-cta__arrow" />
        ) : null}
      </span>
    </Button>
  );
}

function MiniAperturePulse({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "relative inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-ink/10 bg-surface/70",
        className,
      )}
      aria-hidden="true"
    >
      <span className="absolute h-7 w-7 rounded-full border border-lens-blue/25 motion-safe:animate-ping motion-reduce:animate-none" />
      <span
        className="brand-aperture-mark h-[18px] w-[18px] bg-[url('/brand/claread-icon-fullcolor.png')] bg-contain bg-center bg-no-repeat"
      />
    </span>
  );
}

function AnalysisLoadingArtwork() {
  return (
    <div className="relative h-[18.5rem] w-full max-w-[34rem] sm:h-[20rem]">
      <div className="absolute inset-0 flex items-center justify-center">
        <div className="relative aspect-[16/11] w-full max-w-[29rem]">
          <Image
            src="/images/loading/analysis-loading-stage-idle.png"
            alt=""
            aria-hidden="true"
            fill
            sizes="(max-width: 640px) 80vw, 29rem"
            className="pointer-events-none absolute inset-0 h-full w-full select-none object-contain"
          />

          <div className="pointer-events-none absolute inset-0 motion-reduce:hidden" aria-hidden="true">
            <span className="loading-stage__pulse loading-stage__pulse--outer" />
            <span className="loading-stage__pulse loading-stage__pulse--inner" />
            <span className="loading-stage__scan-track" />
            <span className="loading-stage__scan-core" />
            <span className="loading-stage__glint loading-stage__glint--left" />
            <span className="loading-stage__glint loading-stage__glint--right" />
            <span className="loading-stage__highlight-shimmer" />
          </div>
        </div>
      </div>

      <style>{`
        .loading-stage__pulse {
          position: absolute;
          left: 57%;
          top: 46.2%;
          transform: translate(-50%, -50%) scale(0.92);
          border-radius: 999px;
          border: 1px solid color-mix(in srgb, var(--lens-blue) 16%, transparent);
          opacity: 0;
          animation: loading-stage-pulse 3.1s cubic-bezier(0.22, 1, 0.36, 1) infinite;
        }

        .loading-stage__pulse--outer {
          width: 26%;
          height: 26%;
        }

        .loading-stage__pulse--inner {
          width: 18%;
          height: 18%;
          animation-delay: 0.24s;
        }

        .loading-stage__scan-track {
          position: absolute;
          left: 28%;
          right: 19%;
          top: 49.2%;
          height: 1px;
          overflow: hidden;
        }

        .loading-stage__scan-core {
          position: absolute;
          left: 28%;
          top: calc(49.2% - 3px);
          width: 52%;
          height: 6px;
          border-radius: 999px;
          background: color-mix(in srgb, var(--lens-blue) 50%, transparent);
          opacity: 0;
          filter: blur(0.4px);
          animation: loading-stage-scan 3.1s ease-in-out infinite;
        }

        .loading-stage__glint {
          position: absolute;
          width: 16px;
          height: 16px;
          opacity: 0;
        }

        .loading-stage__glint::before,
        .loading-stage__glint::after {
          content: "";
          position: absolute;
          left: 50%;
          top: 50%;
          transform: translate(-50%, -50%);
          border-radius: 999px;
          background: color-mix(in srgb, var(--surface) 72%, var(--lens-blue) 28%);
        }

        .loading-stage__glint::before {
          width: 16px;
          height: 2px;
        }

        .loading-stage__glint::after {
          width: 2px;
          height: 16px;
        }

        .loading-stage__glint--left {
          left: 19.2%;
          top: 60.8%;
          animation: loading-stage-glint-left 3.1s ease-in-out infinite;
        }

        .loading-stage__glint--right {
          left: 78.4%;
          top: 29.8%;
          width: 14px;
          height: 14px;
          animation: loading-stage-glint-right 3.1s ease-in-out infinite;
        }

        .loading-stage__highlight-shimmer {
          position: absolute;
          left: 36.2%;
          top: 63.6%;
          width: 18.5%;
          height: 4.2%;
          overflow: hidden;
          border-radius: 999px;
          opacity: 0;
          animation: loading-stage-shimmer 3.1s ease-in-out infinite;
        }

        .loading-stage__highlight-shimmer::before {
          content: "";
          position: absolute;
          inset: 0;
          background: color-mix(in srgb, var(--surface-raised) 80%, transparent);
          transform: translateX(-115%);
          animation: loading-stage-shimmer-pass 3.1s ease-in-out infinite;
        }

        @keyframes loading-stage-pulse {
          0%,
          14%,
          100% {
            opacity: 0;
            transform: translate(-50%, -50%) scale(0.92);
          }

          30% {
            opacity: 0.42;
          }

          56% {
            opacity: 0;
            transform: translate(-50%, -50%) scale(1.18);
          }
        }

        @keyframes loading-stage-scan {
          0%,
          16%,
          100% {
            opacity: 0;
            transform: translateX(-18%);
          }

          28% {
            opacity: 0.88;
          }

          62% {
            opacity: 0.24;
            transform: translateX(18%);
          }
        }

        @keyframes loading-stage-glint-left {
          0%,
          38%,
          100% {
            opacity: 0;
            transform: scale(0.72);
          }

          46% {
            opacity: 0.88;
            transform: scale(1);
          }

          58% {
            opacity: 0;
            transform: scale(1.08);
          }
        }

        @keyframes loading-stage-glint-right {
          0%,
          60%,
          100% {
            opacity: 0;
            transform: scale(0.74);
          }

          68% {
            opacity: 0.72;
            transform: scale(1);
          }

          79% {
            opacity: 0;
            transform: scale(1.06);
          }
        }

        @keyframes loading-stage-shimmer {
          0%,
          56%,
          100% {
            opacity: 0;
          }

          68%,
          88% {
            opacity: 0.72;
          }
        }

        @keyframes loading-stage-shimmer-pass {
          0%,
          56% {
            transform: translateX(-115%);
          }

          86% {
            transform: translateX(118%);
          }

          100% {
            transform: translateX(118%);
          }
        }
      `}</style>
    </div>
  );
}

function AnalysisLoadingStage({
  title,
  animationSlot,
}: {
  title: string;
  animationSlot?: ReactNode;
}) {
  return (
    <div
      className="relative z-10 flex min-h-0 flex-1 items-center justify-center overflow-hidden px-8 py-10 sm:px-16 xl:px-24"
      aria-live="polite"
    >
      <div className="pointer-events-none absolute inset-x-16 top-10 hidden max-w-[42rem] space-y-4 opacity-[0.16] sm:block xl:left-24">
        {[0.82, 0.64, 0.76, 0.52, 0.7].map((width, index) => (
          <span
            key={index}
            className="block h-px rounded-full bg-ink/35"
            style={{ width: `${width * 100}%` }}
          />
        ))}
      </div>

      <div className="relative flex w-full max-w-[36rem] flex-col items-center text-center">
        {animationSlot ?? <AnalysisLoadingArtwork />}

        <p className="mt-1 font-sans text-[0.72rem] font-bold tracking-[0.16em] text-lens-blue/88">
          Claread Reading Desk
        </p>
        <h2 className="mt-2.5 font-headline text-[1.5rem] font-semibold leading-tight text-ink sm:text-[1.78rem]">
          {title}
        </h2>
      </div>
    </div>
  );
}

export function AnalysisLoadingStatusBar({
  messagePrefix,
}: {
  messagePrefix: string;
}) {
  const [messageIndex, setMessageIndex] = useState(0);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setMessageIndex((current) => (current + 1) % LOADING_MESSAGES.length);
    }, 2600);

    return () => window.clearInterval(timer);
  }, []);

  return (
    <div className="flex min-h-12 max-w-[38rem] items-center gap-3 font-sans text-[0.78rem]">
      <MiniAperturePulse className="h-8 w-8 bg-surface/76" />
      <div className="min-w-0">
        <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
          <p className="shrink-0 font-semibold text-ink">{messagePrefix}</p>
          <span className="hidden h-1 w-1 shrink-0 rounded-full bg-hairline sm:inline-flex" aria-hidden="true" />
          <p
            key={messageIndex}
            className="min-w-0 text-[0.75rem] font-semibold text-ink/74 motion-safe:animate-in motion-safe:fade-in motion-reduce:animate-none"
            aria-live="polite"
          >
            {LOADING_MESSAGES[messageIndex]}
          </p>
        </div>
        <p className="mt-1 min-w-0 text-[0.72rem] font-medium leading-5 text-muted-foreground">
          离开本页不会影响透读，完成后会保存到阅读记录
        </p>
      </div>
    </div>
  );
}

const SOURCE_FORMAT_SHORT_LABELS: Record<SourceFileKind, string> = {
  pdf: "PDF",
  markdown: "Markdown",
  text: "TXT",
  image: "图片",
};

/**
 * 上传文件状态：压缩成单一文件行（图标 + 文件名 + 格式 · 大小 + 更换/移除）。
 * 不重复解释流程状态（格式待处理提示、提交后动作说明），不提供无判断
 * 价值的装饰性预览卡。
 */
function SourceFilePreview({
  source,
  onReplace,
  onRemove,
}: {
  source: AttachedSource;
  onReplace: () => void;
  onRemove: () => void;
}) {
  const { descriptor } = source;
  const isImage = descriptor.kind === "image";

  return (
    <div
      data-testid="source-file-preview"
      className="relative z-10 flex min-h-0 flex-1 flex-col px-5 py-5 sm:px-8 sm:py-6"
    >
      <div
        data-testid="attached-source"
        className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-3 border-b border-hairline/70 pb-5 font-sans"
      >
        <span className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-[10px] border border-ink/10 bg-surface/70 text-ink">
          {isImage ? (
            <ImageIcon aria-hidden className="h-4.5 w-4.5" />
          ) : (
            <FileText aria-hidden className="h-4.5 w-4.5" />
          )}
        </span>
        <div className="min-w-0 flex-1 basis-48">
          <p
            className="truncate text-[0.94rem] font-semibold leading-6 text-ink"
            title={source.file.name}
          >
            {source.file.name}
          </p>
          <p className="mt-0.5 text-[0.76rem] font-medium text-muted-foreground">
            {SOURCE_FORMAT_SHORT_LABELS[descriptor.kind]} · {formatFileSize(source.file.size)}
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          <Button type="button" variant="secondary" size="sm" onClick={onReplace}>
            更换
          </Button>
          <Button type="button" variant="ghost" size="sm" onClick={onRemove}>
            移除
          </Button>
        </div>
      </div>
    </div>
  );
}

type AnalyzeSubmitFormProps = ReadingDefaultState;

export function AnalyzeSubmitForm({
  readingGoal: initialGoal,
  readingVariant: initialVariant,
}: AnalyzeSubmitFormProps) {
  const router = useRouter();
  const markdownEditorRef = useRef<MarkdownTextInputHandle | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const lastFileRef = useRef<File | null>(null);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const dragDepthRef = useRef(0);
  const attachedSourceRef = useRef<AttachedSource | null>(null);
  const [text, setText] = useState("");
  const [attachedSource, setAttachedSource] = useState<AttachedSource | null>(null);
  const [isDragActive, setDragActive] = useState(false);
  const defaults = normalizeReaderRecordReadingDefaults({ readingGoal: initialGoal, readingVariant: initialVariant });
  const [readingGoal, setReadingGoal] = useState<ReaderRecordReadingGoal>(defaults.readingGoal);
  const [readingVariant, setReadingVariant] = useState<ReaderRecordReadingVariant>(defaults.readingVariant);
  const [isReadingPlanOpen, setReadingPlanOpen] = useState(false);
  const [state, setState] = useState<SubmitState>({ kind: "idle" });
  const [isCandidateDialogOpen, setCandidateDialogOpen] = useState(false);
  // Phase 1 / P0: 输入端预警 lint 结果（非阻塞，后端仍是 fail-closed 单一真相源）
  const [lintResult, setLintResult] = useState<MarkdownLintResult>({
    warnings: [],
    hasDangerousContent: false,
  });
  // C1.3: Markdown 解析降级提示（非阻塞，初值/setValue 解析失败时显示）
  const [degradedMessage, setDegradedMessage] = useState<string | null>(null);
  const isWaiting =
    state.kind === "pending" ||
    state.kind === "artifact-uploading" ||
    state.kind === "artifact-polling";
  const isSubmitting: boolean = isWaiting;
  const isReadyToSubmit = Boolean(attachedSource || text.trim().length > 0);
  // 状态栏只呈现近似词数（基于已 debounce 的 text，不恢复逐键 serialize）。
  const approxWordCount = useMemo(() => formatApproxWordCount(text), [text]);

  // L2/L3：编辑器有内容（或进入 Content Check）后收起 Hero，编辑器成首屏主任务。
  const { setHasContent } = useReadPageUi();
  useEffect(() => {
    setHasContent(
      Boolean(attachedSource) || text.trim().length > 0 || state.kind === "content-check",
    );
  }, [attachedSource, text, state.kind, setHasContent]);

  useEffect(() => {
    const searchParams = new URLSearchParams(window.location.search);
    const resumeRecordId = searchParams.get("resume_candidate")?.trim() ?? "";

    if (resumeRecordId) {
      void runResumeFlow(resumeRecordId);
      return;
    }

    const timer = window.setTimeout(() => {
      const pending = readPendingCandidate();
      if (pending) {
        const snapshot = pending.inputSnapshot ?? "";
        setText(snapshot);
        markdownEditorRef.current?.setValue(snapshot);
        // 刷新恢复：默认走 L2 Content Check（GET confirmed-source 拿
        // draft + 最新 candidate）；存量记录无 source 行时由面板回退旧
        // 候选确认模态。
        setState({
          kind: "content-check",
          recordId: pending.readingRecordId,
          filename: pending.filename ?? null,
          inputSnapshot: pending.inputSnapshot ?? null,
          origin: "submit",
          fallbackCandidate: pending,
        });
      }
    }, 0);

    return () => window.clearTimeout(timer);
  }, []);

  useEffect(() => {
    return () => {
      stopPolling();
    };
  }, []);

  function stopPolling() {
    if (pollTimerRef.current !== null) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }

  function setCurrentAttachedSource(source: AttachedSource | null) {
    attachedSourceRef.current = source;
    setAttachedSource(source);
  }

  function makeAttachedSource(file: File, descriptor: SourceFileDescriptor): AttachedSource {
    return {
      file,
      sourceKind: descriptor.sourceKind,
      descriptor,
    };
  }

  function resetFileInput() {
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  }

  function clearAttachedSource() {
    stopPolling();
    clearPendingCandidate();
    lastFileRef.current = null;
    setCurrentAttachedSource(null);
    setCandidateDialogOpen(false);
    setState({ kind: "idle" });
    resetFileInput();
    markdownEditorRef.current?.focus();
  }

  function selectSourceFile(file: File) {
    stopPolling();
    clearPendingCandidate();
    setCandidateDialogOpen(false);
    setDragActive(false);
    dragDepthRef.current = 0;

    const validation = validateSourceFile(file);
    if (!validation.ok) {
      lastFileRef.current = null;
      setCurrentAttachedSource(null);
      setState({
        kind: "error",
        message: validation.message,
      });
      return;
    }

    lastFileRef.current = file;
    setCurrentAttachedSource(makeAttachedSource(file, validation.descriptor));
    setState({ kind: "idle" });
  }

  function openFilePicker() {
    if (isWaiting) {
      return;
    }
    resetFileInput();
    fileInputRef.current?.click();
  }

  function handleFileInputChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) {
      return;
    }
    selectSourceFile(file);
  }

  function handleDragEnter(event: DragEvent<HTMLDivElement>) {
    if (!hasFileTransfer(event.dataTransfer) || isWaiting) {
      return;
    }
    event.preventDefault();
    dragDepthRef.current += 1;
    setDragActive(true);
  }

  function handleDragOver(event: DragEvent<HTMLDivElement>) {
    if (!hasFileTransfer(event.dataTransfer) || isWaiting) {
      return;
    }
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
  }

  function handleDragLeave(event: DragEvent<HTMLDivElement>) {
    if (!hasFileTransfer(event.dataTransfer)) {
      return;
    }
    dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
    if (dragDepthRef.current === 0) {
      setDragActive(false);
    }
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    if (!hasFileTransfer(event.dataTransfer) || isWaiting) {
      return;
    }
    event.preventDefault();
    dragDepthRef.current = 0;
    setDragActive(false);
    const file = event.dataTransfer.files?.[0];
    if (file) {
      selectSourceFile(file);
    }
  }

  function retryLastFile() {
    const file = lastFileRef.current;
    if (!file) {
      openFilePicker();
      return;
    }
    void startArtifactFlow(file);
  }

  async function postInitUpload(body: unknown): Promise<ReaderSourceArtifactUploadInitResult> {
    const response = await fetch("/api/web/reader/source-artifacts/init-upload", {
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
      `/api/web/reader/source-artifacts/${encodeURIComponent(artifactId)}/complete-upload`,
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
      `/api/web/reader/source-artifacts/${encodeURIComponent(artifactId)}/submit-input`,
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
      `/api/web/reader/source-artifacts/${encodeURIComponent(artifactId)}/pipeline-status`,
    );
    return (await response.json()) as ReaderArtifactPipelineStatusResult;
  }

  async function pollUntilTerminal(artifactId: string, currentFilename: string) {
    const tick = async () => {
      const result = await fetchPipelineStatus(artifactId);
      if (isBffError(result)) {
        stopPolling();
        setState({
          kind: "error",
          message: result.message || "查询处理进度失败，请稍后重试。",
        });
        return;
      }

      const status = result;
      if (isArtifactPipelineWorkerStalled(status)) {
        stopPolling();
        setState({
          kind: "error",
          message: "文件解析服务暂未启动或队列阻塞，请确认本地 Worker 已启动后重试。",
        });
        return;
      }

      setState({
        kind: "artifact-polling",
        filename: currentFilename,
        message: describeNextAction(status.next_action, status.outcome),
      });
      if (isTerminalOutcome(status.outcome) || isTerminalAction(status.next_action)) {
        stopPolling();
        applyArtifactOutcome(status, currentFilename);
      }
    };

    void tick();
    pollTimerRef.current = setInterval(() => {
      void tick();
    }, POLL_INTERVAL_MS);
  }

  function applyArtifactOutcome(status: ReaderArtifactPipelineStatusSafeDto, currentFilename: string) {
    const { outcome, next_action: nextAction, record } = status;
    if (outcome === "stable_document_ready" || nextAction === "open_reader") {
      const readingRecordId = record?.reading_record_id;
      if (!readingRecordId) {
        setState({
          kind: "error",
          message: "文档已就绪，但缺少阅读记录信息，请重新提交。",
        });
        return;
      }
      clearPendingCandidate();
      setState({ kind: "success", message: "阅读记录已创建，正在打开 Reader。" });
      router.push(appReaderRoute(readingRecordId) as Route);
      return;
    }

    if (outcome === "candidate_document_required" || nextAction === "confirm_candidate_document") {
      const readingRecordId = record?.reading_record_id;
      if (!readingRecordId) {
        setState({
          kind: "error",
          message: "已生成候选文档，但缺少阅读记录信息。",
        });
        return;
      }
      const candidateDocumentId = status.candidate_document?.candidate_document_id;
      if (!candidateDocumentId) {
        setState({
          kind: "error",
          message: "已生成候选文档，但暂时无法打开确认窗口，请稍后重试。",
        });
        return;
      }
      const saved = savePendingCandidate({
        readingRecordId,
        candidateDocumentId,
        originalInputId: null,
        inputSnapshot: null,
        filename: currentFilename,
        canonicalTextPreview: status.candidate_document?.canonical_text_preview ?? null,
      });
      // L2 默认流程：同页 Content Check；存量记录由面板回退旧模态。
      setState({
        kind: "content-check",
        recordId: readingRecordId,
        filename: currentFilename,
        inputSnapshot: null,
        origin: "submit",
        fallbackCandidate: saved,
      });
      return;
    }

    if (outcome === "input_rejected_or_action_required" || nextAction === "revise_input") {
      setState({
        kind: "error",
        message: "系统没能识别这份文件，可以换一份文件或改用粘贴文本。",
      });
      return;
    }

    if (nextAction === "show_error" || outcome === "extraction_failed" || outcome === "materialization_failed") {
      setState({
        kind: "error",
        message: `处理失败（${summarizeOutcome(outcome)}），可以重试或重新选择文件。`,
      });
    }
  }

  async function startArtifactFlow(file: File) {
    const validation = validateSourceFile(file);
    if (!validation.ok) {
      setState({
        kind: "error",
        message: validation.message,
      });
      return;
    }

    lastFileRef.current = file;
    setCurrentAttachedSource(makeAttachedSource(file, validation.descriptor));
    setState({
      kind: "artifact-uploading",
      filename: file.name,
      message: "正在准备上传...",
    });

    try {
      const initResult = await postInitUpload({
        artifactKind: "original_upload",
        sourceFilename: file.name,
        contentType: file.type || "application/octet-stream",
        byteSize: file.size,
      });
      if (!initResult.ok) {
        setState({
          kind: "error",
          message: initResult.message || "无法开始上传，请稍后重试。",
        });
        return;
      }

      const artifactId = initResult.artifact_id;
      const presignedUrl = initResult.presigned_url;
      const presignedMethod = initResult.presigned_method ?? "PUT";
      const presignedHeaders = initResult.headers ?? {};
      if (!artifactId || !presignedUrl) {
        setState({
          kind: "error",
          message: "上传服务暂时不可用，请稍后重试或重新选择文件。",
        });
        return;
      }

      setState({
        kind: "artifact-uploading",
        filename: file.name,
        message: "正在上传文件...",
      });

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
        setState({
          kind: "error",
          message: "文件上传失败，请检查网络后重试。",
        });
        return;
      }

      const completeResult = await postCompleteUpload(artifactId, {
        contentType: file.type || "application/octet-stream",
        byteSize: file.size,
      });
      if (!completeResult.ok) {
        setState({
          kind: "error",
          message: completeResult.message || "确认上传失败，请稍后重试。",
        });
        return;
      }

      setState({
        kind: "artifact-uploading",
        filename: file.name,
        message: "正在提交文件...",
      });

      const submitResult = await postSubmitInput(artifactId, {
        title: file.name,
        language: "en",
        readingGoal,
        readingVariant,
      });
      if (!submitResult.ok) {
        setState({
          kind: "error",
          message: submitResult.message || "提交文件失败，请稍后重试。",
        });
        return;
      }

      setState({
        kind: "artifact-polling",
        filename: file.name,
        message: "已提交，正在等待后台处理...",
      });
      await pollUntilTerminal(artifactId, file.name);
    } catch (error: unknown) {
      setState({
        kind: "error",
        message: error instanceof Error ? error.message : "文件处理失败，请稍后重试。",
      });
    }
  }

  async function runResumeFlow(recordId: string) {
    // L2：resume 入口改为 GET confirmed-source（draft + 最新 candidate，
    // 合同 “GET draft / resume 语义”）。404（L2 前存量记录无 source 行）回退旧
    // candidate-document 流；record_state_advanced 直接打开 Reader。
    try {
      const sourceResponse = await fetch(
        `/api/web/reader/records/${encodeURIComponent(recordId)}/confirmed-source`,
        { method: "GET" },
      );
      const sourcePayload = (await sourceResponse.json()) as {
        ok: boolean;
        status?: number;
        code?: string;
        message?: string;
      };
      if (sourcePayload.ok) {
        setState({
          kind: "content-check",
          recordId,
          filename: null,
          inputSnapshot: null,
          origin: "resume",
          fallbackCandidate: null,
        });
        return;
      }
      if (sourcePayload.code === "candidate_conflict_open_reader") {
        router.push(appReaderRoute(recordId));
        return;
      }
      if (sourcePayload.status !== 404 && sourcePayload.code !== "confirmed_source_not_found") {
        setState({
          kind: "resume-failed",
          recordId,
          message: sourcePayload.message?.trim() || "加载失败，请稍后重试。",
        });
        return;
      }
      // 404：走下方旧 candidate-document 恢复流。
    } catch {
      // 网络异常不是 404：不穿透到旧端点（会掩盖真实故障），直接呈现可重试失败。
      setState({
        kind: "resume-failed",
        recordId,
        message: "加载失败，请稍后重试。",
      });
      return;
    }

    try {
      const response = await fetch(
        `/api/web/reader/records/${encodeURIComponent(recordId)}/candidate-document`,
        { method: "GET" },
      );
      const payload = (await response.json()) as ReaderCandidateResumePayload;

      if (payload.ok) {
        const candidate: PendingCandidate = {
          readingRecordId: payload.record_id,
          candidateDocumentId: payload.candidate_document_id,
          originalInputId: null,
          inputSnapshot: null,
          filename: payload.filename ?? null,
          canonicalTextPreview:
            payload.preview.preview_text?.trim() ||
            payload.title?.trim() ||
            null,
          documentOutline: payload.preview.document_outline ?? [],
          riskItems: payload.preview.risk_items ?? [],
          previewMode: payload.preview.preview_mode,
          totalCharCount: payload.preview.total_char_count,
          origin: "resume",
          savedAt: new Date().toISOString(),
        };
        // Do NOT save to localStorage; the BFF is the source of truth.
        // Do NOT call setText() — resume mode must not pre-fill the input.
        setState({ kind: "candidate", candidate });
        setCandidateDialogOpen(true);
        return;
      }

      handleResumeError(recordId, payload);
    } catch {
      setState({
        kind: "resume-failed",
        recordId,
        message: "加载失败，请稍后重试。",
      });
    }
  }

  function handleResumeError(
    recordId: string,
    payload: { status: number; code?: string; message?: string },
  ) {
    const message = payload.message?.trim() || "加载失败，请稍后重试。";
    switch (payload.code) {
      case "candidate_conflict_open_reader":
        router.push(appReaderRoute(recordId));
        return;
      case "candidate_conflict_return_to_library":
        setState({
          kind: "resume-return-to-library",
          message: "这篇内容当前无法继续确认。",
        });
        return;
      case "candidate_not_found":
        setState({
          kind: "resume-not-found",
          recordId,
          message: "未找到可继续确认的内容。",
        });
        return;
      default:
        setState({ kind: "resume-failed", recordId, message });
    }
  }

  async function handleSubmit() {
    if (isWaiting) {
      return;
    }

    if (attachedSource) {
      // 客户端 Markdown lint 只读取明确的文本源。PDF/图片由服务端提取后
      // 进行权威归一化，不能为了提示 badge 把最高 25 MB 的二进制附件
      // 解码成字符串并在主线程执行整篇正则扫描。
      if (shouldLintSourceFileInBrowser(attachedSource.file)) {
        try {
          const fileText = await attachedSource.file.text();
          setLintResult(lintMarkdownInput(fileText));
        } catch {
          // 文本文件读取失败不阻断上传流程。
        }
      }
      await startArtifactFlow(attachedSource.file);
      return;
    }

    // 阶段 3：提交前 flush + 非阻断 lint 提示（fail-closed 已撤销）。
    //
    // 语义（新合同）：
    //   1. flush() 同步 pending debounce，确保父状态与 editor 当前内容一致
    //   2. getSubmitText() 直读 editor 最新内容（粘贴保真优先）
    //   3. lintMarkdownInput(submitText) 同步计算最新 lint 结果并刷新
    //      警告 badge —— 纯 UX 提示，**不阻断提交**；后端 parser/gate
    //      是安全判定的单一真相源（三级分类 silent / adaptation_notice /
    //      content_check），普通链接、安全可清洗 HTML、vector<T> 等
    //      由服务端权威清洗并路由
    //   4. 按钮点击与 Ctrl/Cmd+Enter 都通过 onSubmitRef 走同一
    //      handleSubmit，合同完全一致
    //
    // 注意：提交使用的文本与 lint 检查的文本必须是同一份 submitText。
    // flush() returns the exact snapshot used for both lint and submission,
    // avoiding a second full-document serialization on long inputs.
    const submitText = markdownEditorRef.current?.flush() ?? text;
    const trimmed = submitText.trim();
    if (trimmed.length === 0) {
      setState({ kind: "error", message: "请先粘贴一段需要透读的英文内容。" });
      return;
    }

    // 非阻断 lint：lintResult 状态可能因 debounce 滞后，提交前基于实际
    // submitText 重新计算并刷新 badge；不阻断、服务端权威清洗。
    setLintResult(lintMarkdownInput(submitText));

    setState({ kind: "pending", message: "正在提交透读任务..." });

    try {
      const response = await fetch(readPageSubmitEndpoint(), {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(
          readPageSubmitRequestBody({
            text: trimmed,
            readingGoal,
            readingVariant,
          }),
        ),
      });
      const payload = (await response.json()) as UnifiedSubmitPayload;

      if (!payload.ok) {
        setState({
          kind: "error",
          message: payload.message || "提交失败，请稍后重试。",
        });
        return;
      }

      switch (payload.outcome) {
        case "stable_document_ready": {
          const readerUrl = appReaderRoute(payload.reading_record_id);
          clearPendingCandidate();
          setState({
            kind: "success",
            message: "阅读记录已创建，正在打开 Reader。",
          });
          router.push(readerUrl);
          return;
        }
        case "candidate_document_required": {
          const pending = savePendingCandidate({
            readingRecordId: payload.reading_record_id,
            candidateDocumentId: payload.candidate_document_id,
            originalInputId: payload.original_input_id,
            inputSnapshot: trimmed,
          });
          // L2 默认流程：同页 Content Check（GET confirmed-source 加载草稿）。
          // 存量记录无 source 行时面板回退旧候选确认模态；pending 保存失败
          // 不阻断——草稿在服务端，稍后处理/刷新恢复仅依赖 localStorage。
          setState({
            kind: "content-check",
            recordId: payload.reading_record_id,
            filename: null,
            inputSnapshot: trimmed,
            origin: "submit",
            fallbackCandidate: pending,
          });
          return;
        }
        case "input_rejected_or_action_required": {
          setState({
            kind: "rejected",
            reasons: payload.suitability.reasons ?? [],
            preview: payload.suitability.normalized_preview ?? "",
          });
          return;
        }
      }
    } catch (error: unknown) {
      setState({
        kind: "error",
        message: error instanceof Error ? error.message : "提交失败，请稍后重试。",
      });
    }
  }

  const readingPlanSummary = formatReadingPlanSummary(
    readingGoal,
    readingVariant,
  );

  function handleReadingPlanChange(nextPlan: ReadingDefaultState) {
    setReadingGoal(nextPlan.readingGoal);
    setReadingVariant(nextPlan.readingVariant);
  }

  const loadingStageTitle =
    state.kind === "artifact-uploading" || state.kind === "artifact-polling"
      ? "正在提取这份来源"
      : "正在透读这篇文章";
  const waitingMessagePrefix =
    state.kind === "artifact-uploading" || state.kind === "artifact-polling"
      ? "正在提取"
      : "正在透读";

  return (
    <div className="flex min-h-0 w-full flex-1 flex-col">
      <input
        ref={fileInputRef}
        type="file"
        accept={SOURCE_ACCEPT}
        onChange={handleFileInputChange}
        className="sr-only"
        data-testid="source-file-input"
        tabIndex={-1}
      />
      <div className="flex min-h-0 flex-1 flex-col">
        <label htmlFor="analysis-text" id="analysis-text-label" className="sr-only">
          在此贴入或导入英文文章
        </label>
        <span id="analysis-text-hint" className="sr-only">
          支持标题、强调、列表、引用、代码块等 Markdown 结构；按 Ctrl+Enter 提交。
        </span>

        {state.kind === "content-check" ? (
          <ContentCheckPanel
            recordId={state.recordId}
            filename={state.filename}
            origin={state.origin}
            onOpenReader={(recordId) => {
              clearPendingCandidate();
              router.push(appReaderRoute(recordId) as Route);
            }}
            onConfirmed={(recordId) => {
              clearPendingCandidate();
              router.push(appReaderRoute(recordId) as Route);
            }}
            onLegacyFallback={() => {
              if (state.fallbackCandidate) {
                setState({ kind: "candidate", candidate: state.fallbackCandidate });
                setCandidateDialogOpen(true);
              } else {
                setState({
                  kind: "resume-not-found",
                  recordId: state.recordId,
                  message: "未找到可继续确认的内容。",
                });
              }
            }}
            onBackToInput={(markdown) => {
              clearPendingCandidate();
              setText(markdown);
              markdownEditorRef.current?.setValue(markdown);
              setCurrentAttachedSource(null);
              setState({ kind: "idle" });
            }}
            onDefer={(info) => {
              if (info.candidateDocumentId) {
                savePendingCandidate({
                  readingRecordId: info.recordId,
                  candidateDocumentId: info.candidateDocumentId,
                  originalInputId: null,
                  inputSnapshot: state.inputSnapshot,
                  filename: state.filename,
                  canonicalTextPreview: info.canonicalTextPreview,
                });
              }
              setState({ kind: "idle" });
            }}
          />
        ) : (
        <div
          data-testid="read-source-input"
          onDragEnter={handleDragEnter}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          className={cn(
            "group/manuscript relative flex w-full flex-col overflow-hidden rounded-[10px] bg-surface/40 ring-1 ring-hairline/35 transition-[box-shadow,background-color] duration-200 ease-out focus-within:bg-surface/58 focus-within:shadow-[var(--app-panel-shadow-quiet)] focus-within:ring-lens-blue/28",
            // 文件已附着时编辑器不渲染：卡片收敛到文件行 + 状态栏的紧凑
            // 高度，不保留编辑器大小的空白；其余状态保持工作台稳定高度。
            attachedSource && !isWaiting
              ? "shrink-0"
              : "min-h-[24rem] flex-1 lg:min-h-[26rem]",
            isDragActive && "bg-lens-blue-soft/40 ring-lens-blue/34 shadow-[var(--app-panel-shadow-quiet)]",
          )}
        >
          <div
            aria-hidden="true"
            className={cn("absolute inset-0 z-0", !attachedSource && "cursor-text")}
            onClick={() => {
              if (!attachedSource) {
                markdownEditorRef.current?.focus();
              }
            }}
          />
          {isDragActive && !isWaiting ? (
            <div className="pointer-events-none absolute inset-3 z-30 flex items-center justify-center rounded-[12px] border border-dashed border-lens-blue/42 bg-lens-blue-soft/60 backdrop-blur-[2px]">
              <div className="flex flex-col items-center gap-3 text-center font-sans">
                <span className="inline-flex h-11 w-11 items-center justify-center rounded-[12px] border border-lens-blue/24 bg-surface-raised/72 text-lens-blue shadow-sm">
                  <FileUp aria-hidden className="h-5 w-5" />
                </span>
                <div>
                  <p className="text-[0.92rem] font-semibold text-ink">松开以上传文件</p>
                  <p className="mt-1 text-[0.76rem] font-medium text-muted-foreground">
                    支持 PDF / Markdown / TXT / 图片，单个文件最大 25 MB
                  </p>
                </div>
              </div>
            </div>
          ) : null}

          {isWaiting ? (
            <AnalysisLoadingStage title={loadingStageTitle} />
          ) : attachedSource ? (
            <SourceFilePreview
              source={attachedSource}
              onReplace={openFilePicker}
              onRemove={clearAttachedSource}
            />
          ) : (
            <MarkdownTextInput
              ref={markdownEditorRef}
              id="analysis-text"
              ariaLabelledBy="analysis-text-label"
              ariaDescribedBy="analysis-text-hint"
              placeholder="粘贴英文文章，或直接开始输入"
              placeholderSub="支持网页、Markdown、PDF、TXT"
              initialValue={text}
              onChange={(markdown) => {
                setText(markdown);
                // C1.3: 用户编辑后清除降级提示（新一轮内容由用户掌控）
                if (degradedMessage) setDegradedMessage(null);
              }}
              onLintResult={setLintResult}
              onDegraded={(result) => {
                // C1.3: 解析失败时显示可见降级提示，禁止原始标记静默上屏
                if (result.status === "degraded") {
                  setDegradedMessage("Markdown 解析失败，已按纯文本处理。可直接编辑或重新粘贴。");
                } else {
                  setDegradedMessage(null);
                }
              }}
              onSubmit={() => void handleSubmit()}
              className="relative z-10 px-5 py-6 font-sans text-base leading-[1.68] text-ink sm:px-[max(2rem,calc(50%-24rem))] sm:py-8 selection:bg-lens-blue/15 selection:text-ink"
            />
          )}

          {!isWaiting && !attachedSource && text.trim().length > 0 && (
            <button
              type="button"
              className="absolute right-3 top-3 z-20 inline-flex h-9 w-9 items-center justify-center rounded-full text-subtle transition-colors hover:bg-surface/70 hover:text-ink focus-ring"
              onClick={() => {
                setText("");
                setLintResult({ warnings: [], hasDangerousContent: false });
                setDegradedMessage(null);
                markdownEditorRef.current?.clear();
                markdownEditorRef.current?.focus();
              }}
              title="清空"
            >
              <X aria-hidden className="h-4 w-4" />
            </button>
          )}

          <div className="relative z-20 mx-5 mb-4 shrink-0 border-t border-hairline/68 px-0 pt-3 sm:mx-8">
            {isWaiting ? (
              <AnalysisLoadingStatusBar
                messagePrefix={waitingMessagePrefix}
              />
            ) : (
              <div
                data-testid="read-source-primary-actions"
                className="min-w-0 space-y-2.5"
              >
                <div
                  data-testid="read-source-status-row"
                  className="flex min-h-5 min-w-0 flex-wrap items-center gap-x-3 gap-y-1 font-sans text-xs"
                >
                  {!attachedSource && approxWordCount ? (
                    <span
                      className="font-medium text-subtle"
                      title={`共 ${text.trim().length.toLocaleString("zh-CN")} 字符`}
                    >
                      {approxWordCount}
                    </span>
                  ) : null}

                  {lintResult.hasDangerousContent ? (
                    <span
                      data-testid="read-source-lint-warning"
                      className="inline-flex items-center gap-1 font-semibold text-feedback-warning"
                      title={summarizeLintWarnings(lintResult.warnings)}
                    >
                      <AlertTriangle aria-hidden className="h-3 w-3" />
                      有 {lintResult.warnings.length} 处格式需要确认
                    </span>
                  ) : null}

                  {!attachedSource && degradedMessage ? (
                    <span
                      data-testid="read-source-degraded-hint"
                      className="inline-flex items-center gap-1 font-semibold text-feedback-warning"
                      title={degradedMessage}
                    >
                      <AlertTriangle aria-hidden className="h-3 w-3" />
                      部分格式已按纯文本显示
                    </span>
                  ) : null}
                </div>

                <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                  {!attachedSource ? (
                    <button
                      type="button"
                      className="focus-ring group/source inline-flex min-h-9 shrink-0 items-center gap-2 self-start whitespace-nowrap px-0 text-sm font-medium leading-none text-ink transition-colors duration-200 hover:text-lens-blue"
                      onClick={openFilePicker}
                    >
                      <span className="inline-flex h-6 w-6 items-center justify-center rounded-[7px] border border-ink/12 bg-surface/54 text-ink transition-colors duration-200 group-hover/source:border-lens-blue/34 group-hover/source:text-lens-blue">
                        <FileUp aria-hidden className="h-3.5 w-3.5" />
                      </span>
                      <span>上传文件</span>
                    </button>
                  ) : null}

                  <div className="flex min-w-0 flex-col items-stretch gap-2 sm:ml-auto sm:shrink-0 sm:flex-row sm:items-center sm:justify-end">
                    <Popover
                      open={isReadingPlanOpen}
                      onOpenChange={setReadingPlanOpen}
                    >
                      <PopoverTrigger asChild>
                        <button
                          type="button"
                          aria-label={`阅读方案：${readingPlanSummary}`}
                          className="focus-ring inline-flex min-h-10 items-center justify-center gap-1.5 whitespace-nowrap rounded-[var(--cl-radius-control-sm)] border border-transparent bg-surface-raised/50 px-3 text-sm font-normal leading-none text-ink transition-colors duration-150 hover:border-hairline hover:bg-surface-raised data-[state=open]:border-hairline data-[state=open]:bg-surface motion-reduce:transition-none"
                        >
                          <span>{readingPlanSummary}</span>
                          <ChevronDown aria-hidden className="h-3.5 w-3.5 text-subtle" />
                        </button>
                      </PopoverTrigger>
                      <PopoverContent
                        align="end"
                        side="top"
                        sideOffset={12}
                        collisionPadding={16}
                        aria-labelledby="reading-plan-popover-title"
                        className="w-[min(320px,calc(100vw-2rem))] rounded-[var(--cl-radius-panel)] border border-hairline/78 bg-surface p-4 shadow-[var(--app-panel-shadow-quiet)] data-[state=open]:animate-in data-[state=open]:fade-in-0 motion-reduce:animate-none"
                      >
                        <h3
                          id="reading-plan-popover-title"
                          className="sr-only"
                        >
                          选择本次阅读的方案
                        </h3>

                        <ReadingPlanFields
                          value={{ readingGoal, readingVariant }}
                          onValueChange={handleReadingPlanChange}
                          idPrefix="article-reading-plan"
                        />
                      </PopoverContent>
                    </Popover>

                    <ApertureCornerSubmitButton
                      isPending={isSubmitting}
                      isReady={isReadyToSubmit}
                      onClick={handleSubmit}
                    />
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
        )}
      </div>

      {state.kind !== "idle" && !isWaiting && state.kind !== "candidate" && state.kind !== "content-check" && state.kind !== "rejected" && state.kind !== "resume-not-found" && state.kind !== "resume-return-to-library" && state.kind !== "resume-failed" ? (
        <div
          className={`mt-4 shrink-0 rounded-[14px] border border-hairline/70 bg-surface/42 px-4 py-3 text-[0.82rem] font-medium lg:mx-12 ${
            state.kind === "error" ? "text-feedback-error" : "text-lens-blue"
          }`}
        >
          {state.message}
          {state.kind === "error" && (attachedSource || lastFileRef.current) ? (
            <div className="mt-3 flex flex-wrap gap-2 font-sans">
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={retryLastFile}
              >
                重试
                <RefreshCw aria-hidden className="ml-1 h-3.5 w-3.5" />
              </Button>
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={openFilePicker}
              >
                重新选择文件
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={clearAttachedSource}
              >
                移除文件
              </Button>
            </div>
          ) : null}
        </div>
      ) : null}

      {state.kind === "candidate" ? (
        <>
          {!isCandidateDialogOpen ? (
            <section
              role="status"
              aria-live="polite"
              className="relative z-30 mt-4 flex shrink-0 flex-wrap items-center justify-between gap-3 rounded-[14px] border border-hairline/70 bg-surface/42 px-4 py-3 font-sans text-[0.82rem] font-medium text-ink lg:mx-12"
            >
              <div className="min-w-0">
                <p className="font-semibold">已提取出待确认的英文正文</p>
                <p className="mt-1 text-[0.76rem] text-muted-foreground">
                  请确认正文完整后进入透读。
                </p>
              </div>
              <div className="flex shrink-0 flex-wrap gap-2">
                <Button
                  type="button"
                  variant="primary-ink"
                  size="sm"
                  onClick={() => setCandidateDialogOpen(true)}
                >
                  查看并确认
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  onClick={() => {
                    clearPendingCandidate();
                    const snapshot = state.candidate.inputSnapshot ?? "";
                    setText(snapshot);
                    markdownEditorRef.current?.setValue(snapshot);
                    setCurrentAttachedSource(null);
                    setState({ kind: "idle" });
                    setCandidateDialogOpen(false);
                  }}
                >
                  重新编辑
                </Button>
              </div>
            </section>
          ) : null}
          <CandidateConfirmDialog
            candidate={state.candidate}
            open={isCandidateDialogOpen}
            onOpenChange={setCandidateDialogOpen}
            mode={state.candidate.origin === "resume" ? "resume" : "submit"}
            onConfirmed={(candidate) => {
              router.push(appReaderRoute(candidate.readingRecordId));
            }}
            onRestart={(candidate) => {
              const snapshot = candidate.inputSnapshot ?? "";
              setText(snapshot);
              markdownEditorRef.current?.setValue(snapshot);
              setCurrentAttachedSource(null);
              setState({ kind: "idle" });
              setCandidateDialogOpen(false);
            }}
          />
        </>
      ) : null}

      {state.kind === "rejected" ? (
        <section
          role="status"
          aria-live="polite"
          className="mt-4 shrink-0 rounded-[14px] border border-hairline/70 bg-surface/42 px-4 py-3 font-sans text-[0.82rem] font-medium text-feedback-error lg:mx-12"
        >
          <p className="font-semibold">这次没法直接开始透读</p>
          {state.reasons.length > 0 ? (
            <ul className="mt-2 list-disc pl-5 text-[0.78rem]">
              {state.reasons.slice(0, 2).map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          ) : (
            <p className="mt-2 text-[0.78rem]">
              系统没能识别这是一段适合透读的英文文本。你可以再调整一下内容，或者试试英文新闻 / 论文片段。
            </p>
          )}
          {state.preview ? (
            <p className="mt-2 whitespace-pre-wrap rounded-[8px] border border-hairline/60 bg-surface/40 p-2 text-[0.74rem] text-muted-foreground">
              <span className="font-semibold text-ink/80">我们收到的内容：</span>
              {state.preview.slice(0, 240)}
            </p>
          ) : null}
          <div className="mt-3 flex flex-wrap gap-2">
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={() => setState({ kind: "idle" })}
            >
              重新编辑
            </Button>
          </div>
        </section>
      ) : null}

      {state.kind === "resume-not-found" ? (
        <section
          role="status"
          aria-live="polite"
          className="mt-4 shrink-0 rounded-[14px] border border-hairline/70 bg-surface/42 px-4 py-3 font-sans text-[0.82rem] font-medium text-feedback-error lg:mx-12"
        >
          <p className="font-semibold">{state.message}</p>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={() => router.push(appLibraryRoute)}
            >
              前往阅读记录
            </Button>
          </div>
        </section>
      ) : null}

      {state.kind === "resume-return-to-library" ? (
        <section
          role="status"
          aria-live="polite"
          className="mt-4 shrink-0 rounded-[14px] border border-hairline/70 bg-surface/42 px-4 py-3 font-sans text-[0.82rem] font-medium text-feedback-error lg:mx-12"
        >
          <p className="font-semibold">{state.message}</p>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={() => router.push(appLibraryRoute)}
            >
              前往阅读记录
            </Button>
          </div>
        </section>
      ) : null}

      {state.kind === "resume-failed" ? (
        <section
          role="status"
          aria-live="polite"
          className="mt-4 shrink-0 rounded-[14px] border border-hairline/70 bg-surface/42 px-4 py-3 font-sans text-[0.82rem] font-medium text-feedback-error lg:mx-12"
        >
          <p className="font-semibold">{state.message}</p>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={() => void runResumeFlow(state.recordId)}
            >
              重试加载
            </Button>
          </div>
        </section>
      ) : null}
    </div>
  );
}
