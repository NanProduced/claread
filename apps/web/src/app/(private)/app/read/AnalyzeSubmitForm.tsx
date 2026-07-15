"use client";

import { ArrowRight, BookOpen, Check, ChevronDown, FileCheck2, FileText, FileUp, ImageIcon, RefreshCw, Target, X } from "lucide-react";
import Image from "next/image";
import type { Route } from "next";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, type ChangeEvent, type DragEvent, type ReactNode } from "react";
import { Button } from "@/components/primitives/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/primitives/popover";
import { cn } from "@/lib/cn";
import type {
  ReaderArtifactPipelineStatusSafeDto,
} from "@/lib/reader-orchestration/status-mapper";
import {
  READER_RECORD_READING_GOAL_OPTIONS,
  READER_RECORD_READING_VARIANT_OPTIONS,
  READER_RECORD_DEFAULT_READING_VARIANT_BY_GOAL,
  type ReadingDefaultState,
  type ReaderRecordReadingGoal,
  type ReaderRecordReadingVariant,
  normalizeReaderRecordReadingDefaults,
} from "@/lib/reading-defaults";
import { appLibraryRoute, appReadingRecordRoute } from "@/lib/routes";
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
import {
  readPageSubmitEndpoint,
  readPageSubmitRequestBody,
} from "./submit-mode";

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
      kind: "rejected";
      reasons: string[];
      preview: string;
    }
  | { kind: "resume-not-found"; recordId: string; message: string }
  | { kind: "resume-return-to-library"; message: string }
  | { kind: "resume-failed"; recordId: string; message: string };

type ReaderCandidateResumeErrorCode =
  | "candidate_not_found"
  | "candidate_conflict_open_reader"
  | "candidate_conflict_return_to_library"
  | "upstream_unavailable";

type ReaderCandidateResumePayload = ReaderCandidateDocumentReadResult;

type UnifiedSubmitPayload =
  | ({ ok: true } & ReaderUnifiedInputSubmitResponseDto)
  | ReaderPlateBffError;

type ArtifactSourceKind = "file" | "image";
type SourceFileKind = "pdf" | "markdown" | "text" | "image";

interface SourceFileDescriptor {
  kind: SourceFileKind;
  sourceKind: ArtifactSourceKind;
  label: string;
  badge: string;
  previewStatus: string;
}

interface AttachedSource {
  file: File;
  sourceKind: ArtifactSourceKind;
  descriptor: SourceFileDescriptor;
  previewUrl: string | null;
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
const SUPPORTED_IMAGE_EXTENSIONS = new Set(["png", "jpg", "jpeg", "webp", "gif"]);
const SUPPORTED_IMAGE_MIME_TYPES = new Set([
  "image/png",
  "image/jpeg",
  "image/jpg",
  "image/webp",
  "image/gif",
]);

const SHORT_DESC: Record<string, string> = {
  daily_reading: "自然读懂",
  academic: "术语与结构",
  exam: "长难句与考点",
};

const GOAL_ICONS: Record<string, React.ElementType> = {
  daily_reading: BookOpen,
  academic: FileText,
  exam: Target,
};

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

function imageBadge(extension: string, mimeType: string): string {
  if (extension === "jpeg") {
    return "JPG";
  }
  if (extension) {
    return extension.toUpperCase();
  }
  const subtype = mimeType.split("/")[1] ?? "";
  return subtype === "jpeg" ? "JPG" : subtype.toUpperCase() || "IMG";
}

function describeSourceFile(file: File): SourceFileDescriptor | null {
  const extension = sourceFileExtension(file.name);
  const mimeType = normalizedMimeType(file);

  if (extension === "pdf" || mimeType === "application/pdf") {
    return {
      kind: "pdf",
      sourceKind: "file",
      label: "PDF 文档",
      badge: "PDF",
      previewStatus: "PDF 待提取",
    };
  }

  if (
    extension === "md" ||
    extension === "markdown" ||
    mimeType === "text/markdown" ||
    mimeType === "text/x-markdown"
  ) {
    return {
      kind: "markdown",
      sourceKind: "file",
      label: "Markdown 文档",
      badge: "MD",
      previewStatus: "Markdown 待提取",
    };
  }

  if (extension === "txt" || mimeType === "text/plain") {
    return {
      kind: "text",
      sourceKind: "file",
      label: "TXT 文本",
      badge: "TXT",
      previewStatus: "TXT 待提取",
    };
  }

  if (SUPPORTED_IMAGE_EXTENSIONS.has(extension) || SUPPORTED_IMAGE_MIME_TYPES.has(mimeType)) {
    return {
      kind: "image",
      sourceKind: "image",
      label: "图片 OCR",
      badge: imageBadge(extension, mimeType),
      previewStatus: "图片待提取",
    };
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

function hasFileTransfer(dataTransfer: DataTransfer): boolean {
  return Array.from(dataTransfer.types ?? []).includes("Files");
}

function GoalCard({
  goal,
  active,
  onSelect,
}: {
  goal: { value: string; label: string };
  active: boolean;
  onSelect: () => void;
}) {
  const Icon = GOAL_ICONS[goal.value] || BookOpen;
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onSelect}
      className={`group relative flex w-full flex-col items-center justify-center gap-2.5 rounded-[14px] border p-3 pt-4 text-center transition-all duration-300 ease-out focus-ring ${
        active
          ? "border-transparent bg-white shadow-[0_6px_20px_rgba(17,17,17,0.06)] ring-1 ring-ink/5"
          : "border-transparent bg-transparent hover:bg-ink/[0.03]"
      }`}
    >
      {active && (
        <div className="absolute right-2 top-2 flex h-[1.1rem] w-[1.1rem] items-center justify-center rounded-full bg-lens-blue text-white shadow-sm">
          <Check className="h-[0.7rem] w-[0.7rem]" strokeWidth={3} />
        </div>
      )}
      <div className="flex flex-col items-center gap-0.5">
        <span className={`font-sans text-[0.85rem] tracking-tight ${active ? "font-semibold text-ink" : "font-medium text-ink/80 group-hover:text-ink"}`}>
          {goal.label}
        </span>
        <span className="font-sans text-[0.72rem] tracking-wide text-muted">{SHORT_DESC[goal.value]}</span>
      </div>
      <div className={`mt-0.5 flex h-7 w-7 items-center justify-center transition-colors duration-300 ${active ? "text-ink/80" : "text-subtle group-hover:text-muted"}`}>
        <Icon className="h-[1.15rem] w-[1.15rem]" strokeWidth={1.5} />
      </div>
    </button>
  );
}

function VariantPill({
  variant,
  active,
  onSelect,
}: {
  variant: { value: string; label: string };
  active: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onSelect}
      className={`relative flex min-h-[2.25rem] w-full items-center justify-center gap-1.5 rounded-[8px] border px-1 transition-all duration-300 ease-out focus-ring ${
        active
          ? "border-lens-blue/30 bg-[rgba(37,99,235,0.06)] text-ink ring-1 ring-lens-blue/20"
          : "border-hairline/60 bg-transparent text-muted hover:border-hairline hover:bg-ink/[0.03] hover:text-ink"
      }`}
    >
      <span className={`text-[0.78rem] tracking-tight ${active ? "font-semibold" : "font-medium"}`}>{variant.label}</span>
      {active && <span className="h-[5px] w-[5px] shrink-0 rounded-full bg-lens-blue" />}
    </button>
  );
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
      className={cn("aperture-corner-cta group/aperture font-sans", isReady && "aperture-corner-cta--ready")}
      data-pending={isPending ? "true" : "false"}
      data-ready={isReady ? "true" : "false"}
      disabled={isPending}
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
          background: linear-gradient(
            90deg,
            rgba(31, 94, 255, 0) 0%,
            rgba(31, 94, 255, 0.08) 24%,
            rgba(140, 174, 255, 0.8) 50%,
            rgba(31, 94, 255, 0.08) 76%,
            rgba(31, 94, 255, 0) 100%
          );
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
          background: color-mix(in srgb, var(--reader-paper) 72%, var(--lens-blue) 28%);
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
          background: linear-gradient(
            90deg,
            rgba(255, 255, 255, 0) 0%,
            rgba(255, 251, 239, 0.15) 32%,
            rgba(255, 255, 255, 0.82) 50%,
            rgba(255, 251, 239, 0.15) 68%,
            rgba(255, 255, 255, 0) 100%
          );
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
        <p className="mt-1 min-w-0 text-[0.72rem] font-medium leading-5 text-muted">
          离开本页不会影响透读，完成后会保存到阅读记录
        </p>
      </div>
    </div>
  );
}

function SourceFilePreview({
  source,
  imagePreviewUrl,
  hasTextDraft,
  onReplace,
  onRemove,
}: {
  source: AttachedSource;
  imagePreviewUrl: string | null;
  hasTextDraft: boolean;
  onReplace: () => void;
  onRemove: () => void;
}) {
  const { descriptor } = source;
  const isImage = descriptor.kind === "image";

  return (
    <div
      data-testid="source-file-preview"
      className="relative z-10 flex min-h-0 flex-1 items-center px-8 py-8 sm:px-14 sm:py-10 xl:px-20 xl:py-12"
    >
      <div className="mx-auto grid w-full max-w-[60rem] gap-7 lg:grid-cols-[minmax(0,1fr)_14rem] lg:items-center lg:gap-10">
        <div className="min-w-0 font-sans">
          <div
            data-testid="attached-source"
            className="max-w-[46rem] border-y border-hairline/70 py-5"
          >
            <div className="flex min-w-0 items-center gap-3">
              <span className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-[10px] border border-ink/10 bg-reader-paper/70 text-ink">
                {isImage ? (
                  <ImageIcon aria-hidden className="h-4.5 w-4.5" />
                ) : (
                  <FileText aria-hidden className="h-4.5 w-4.5" />
                )}
              </span>
              <div className="min-w-0">
                <p
                  className="max-w-[44rem] break-words text-[0.96rem] font-semibold leading-6 text-ink"
                  title={source.file.name}
                >
                  {source.file.name}
                </p>
                <p className="mt-1 text-[0.76rem] font-medium text-muted">
                  {descriptor.label} · {formatFileSize(source.file.size)} · 点击开始透读后提取正文
                </p>
                {hasTextDraft ? (
                  <p className="mt-2 text-[0.74rem] font-medium text-subtle">
                    移除文件后可继续编辑原文本。
                  </p>
                ) : null}
              </div>
            </div>
          </div>

          <div className="mt-5 flex flex-wrap gap-2 font-sans">
            <Button type="button" variant="secondary" size="sm" onClick={onReplace}>
              替换文件
            </Button>
            <Button type="button" variant="ghost" size="sm" onClick={onRemove}>
              移除文件
            </Button>
          </div>
        </div>

        <div className="relative min-h-[12rem] overflow-hidden rounded-[10px] border border-hairline/70 bg-[rgba(255,255,255,0.32)]">
          {isImage && imagePreviewUrl ? (
            // eslint-disable-next-line @next/next/no-img-element -- object URLs from local files are not suitable for next/image.
            <img
              src={imagePreviewUrl}
              alt=""
              className="absolute inset-0 h-full w-full object-contain p-4"
              data-testid="source-image-preview"
            />
          ) : (
            <div className="flex h-full min-h-[12rem] flex-col justify-between p-5">
              <div className="flex items-center justify-between gap-4 font-sans">
                <span className="inline-flex h-9 w-9 items-center justify-center rounded-[10px] border border-hairline/70 bg-reader-paper/76 text-ink">
                  <FileCheck2 aria-hidden className="h-5 w-5" />
                </span>
                <span className="rounded-full border border-hairline/70 bg-surface/58 px-2.5 py-1 text-[0.68rem] font-bold tracking-[0.12em] text-muted">
                  {descriptor.badge}
                </span>
              </div>
              <div className="space-y-3" aria-hidden="true">
                {[0.82, 0.66, 0.74, 0.52, 0.62].map((width, index) => (
                  <span
                    key={index}
                    className="block h-px rounded-full bg-ink/18"
                    style={{ width: `${width * 100}%` }}
                  />
                ))}
              </div>
              <p className="font-sans text-[0.76rem] font-semibold text-muted">
                {descriptor.previewStatus}
              </p>
            </div>
          )}
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
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
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
  const [state, setState] = useState<SubmitState>({ kind: "idle" });
  const [isCandidateDialogOpen, setCandidateDialogOpen] = useState(false);
  const isWaiting =
    state.kind === "pending" ||
    state.kind === "artifact-uploading" ||
    state.kind === "artifact-polling";
  const isSubmitting: boolean = isWaiting;
  const isReadyToSubmit = Boolean(attachedSource || text.trim().length > 0);

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
        setText(pending.inputSnapshot ?? "");
        setState({
          kind: "candidate",
          candidate: pending,
        });
        setCandidateDialogOpen(true);
      }
    }, 0);

    return () => window.clearTimeout(timer);
  }, []);

  useEffect(() => {
    return () => {
      stopPolling();
      revokePreviewUrl(attachedSourceRef.current);
    };
  }, []);

  function stopPolling() {
    if (pollTimerRef.current !== null) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }

  function revokePreviewUrl(source: AttachedSource | null) {
    if (source?.previewUrl) {
      URL.revokeObjectURL(source.previewUrl);
    }
  }

  function setCurrentAttachedSource(source: AttachedSource | null) {
    revokePreviewUrl(attachedSourceRef.current);
    attachedSourceRef.current = source;
    setAttachedSource(source);
  }

  function makeAttachedSource(file: File, descriptor: SourceFileDescriptor): AttachedSource {
    const sourceKind = descriptor.sourceKind;
    const previewUrl =
      sourceKind === "image" && typeof URL.createObjectURL === "function"
        ? URL.createObjectURL(file)
        : null;
    return {
      file,
      sourceKind,
      descriptor,
      previewUrl,
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
    textareaRef.current?.focus();
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
      router.push(appReadingRecordRoute(readingRecordId) as Route);
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
      if (saved) {
        setState({
          kind: "candidate",
          candidate: saved,
        });
        setCandidateDialogOpen(true);
      } else {
        setState({
          kind: "error",
          message: "已生成候选文档，但本地暂存失败，请稍后再试。",
        });
      }
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
    try {
      const response = await fetch(
        `/api/web/reader-plate/records/${encodeURIComponent(recordId)}/candidate-document`,
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
        // Do NOT call setText() — resume mode must not pre-fill the textarea.
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
        router.push(appReadingRecordRoute(recordId));
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
      await startArtifactFlow(attachedSource.file);
      return;
    }

    const trimmed = text.trim();
    if (trimmed.length === 0) {
      setState({ kind: "error", message: "请先粘贴一段需要透读的英文内容。" });
      return;
    }

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
          const readerUrl = appReadingRecordRoute(payload.reading_record_id);
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
          if (pending) {
            setState({
              kind: "candidate",
              candidate: pending,
            });
            setCandidateDialogOpen(true);
          } else {
            setState({
              kind: "error",
              message: "已生成候选文档，但本地暂存失败，请稍后再试。",
            });
          }
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

  const selectedGoalLabel = READER_RECORD_READING_GOAL_OPTIONS.find((option) => option.value === readingGoal)?.label;
  const selectedVariantLabel = READER_RECORD_READING_VARIANT_OPTIONS[readingGoal].find(
    (option) => option.value === readingVariant,
  )?.label;
  const loadingStageTitle =
    state.kind === "artifact-uploading" || state.kind === "artifact-polling"
      ? "正在提取这份来源"
      : "正在透读这篇文章";
  const waitingMessagePrefix =
    state.kind === "artifact-uploading" || state.kind === "artifact-polling"
      ? "正在提取"
      : "正在透读";

  return (
    <div className="flex min-h-0 flex-1 w-full flex-col overflow-y-auto">
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
        <label htmlFor="analysis-text" className="sr-only">
          在此贴入或导入英文文章
        </label>

        <div
          data-testid="read-source-input"
          onDragEnter={handleDragEnter}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          className={cn(
            "group/manuscript relative flex min-h-[22rem] flex-1 w-full shrink-0 flex-col overflow-hidden rounded-[10px] bg-[linear-gradient(180deg,rgba(251,247,238,0.62),rgba(251,247,238,0.18)_48%,rgba(251,247,238,0)_100%)] ring-1 ring-hairline/35 transition-[box-shadow,background-color] duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] focus-within:shadow-[0_18px_44px_rgba(23,21,17,0.055)] lg:min-h-[31rem] lg:shrink 2xl:min-h-[34rem]",
            isDragActive && "bg-[linear-gradient(180deg,rgba(235,241,255,0.52),rgba(251,247,238,0.22)_52%,rgba(251,247,238,0)_100%)] ring-lens-blue/34 shadow-[0_22px_54px_rgba(31,94,255,0.08)]",
          )}
        >
          <div
            aria-hidden="true"
            className={cn("absolute inset-0 z-0", !attachedSource && "cursor-text")}
            onClick={() => {
              if (!attachedSource) {
                textareaRef.current?.focus();
              }
            }}
          />
          {!attachedSource ? (
            <>
              <div className="pointer-events-none absolute left-4 top-5 h-[calc(100%-2.5rem)] w-px bg-hairline/75 transition-colors duration-300 group-focus-within/manuscript:bg-lens-blue/28 xl:left-5" />
              <div className="pointer-events-none absolute left-12 top-9 h-[3.4rem] w-[2px] bg-ink/22 transition-all duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] group-focus-within/manuscript:h-[4.4rem] group-focus-within/manuscript:bg-lens-blue/58 xl:left-16" />
            </>
          ) : null}

          {!isWaiting && !attachedSource && !text.trim() ? (
            <div className="pointer-events-none absolute left-16 top-9 z-10 max-w-[26rem] xl:left-24 xl:top-11">
              <p className="font-reading text-[1.16rem] leading-tight text-ink/78 xl:text-[1.28rem]">
                Paste an English article here
              </p>
              <p className="mt-2 max-w-[21rem] font-sans text-[0.78rem] leading-6 text-muted">
                粘贴英文文章，或拖入 PDF / Markdown / TXT / 图片。
              </p>
            </div>
          ) : null}

          {isDragActive && !isWaiting ? (
            <div className="pointer-events-none absolute inset-3 z-30 flex items-center justify-center rounded-[12px] border border-dashed border-lens-blue/42 bg-[rgba(246,249,255,0.78)] backdrop-blur-[2px]">
              <div className="flex flex-col items-center gap-3 text-center font-sans">
                <span className="inline-flex h-11 w-11 items-center justify-center rounded-[12px] border border-lens-blue/24 bg-white/72 text-lens-blue shadow-sm">
                  <FileUp aria-hidden className="h-5 w-5" />
                </span>
                <div>
                  <p className="text-[0.92rem] font-semibold text-ink">松开以上传文件</p>
                  <p className="mt-1 text-[0.76rem] font-medium text-muted">
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
              imagePreviewUrl={attachedSource.previewUrl}
              hasTextDraft={text.trim().length > 0}
              onReplace={openFilePicker}
              onRemove={clearAttachedSource}
            />
          ) : (
            <textarea
              ref={textareaRef}
              id="analysis-text"
              className="relative z-10 min-h-0 flex-1 resize-none overflow-y-auto bg-transparent px-16 py-10 font-reading text-[1.08rem] leading-[2.08] text-ink outline-none placeholder:text-transparent sm:text-[1.18rem] xl:px-24 xl:py-12 xl:text-[1.24rem] selection:bg-lens-blue/15 selection:text-ink"
              placeholder="Paste an English article here"
              value={text}
              onChange={(event) => setText(event.target.value)}
              onKeyDown={(event) => {
                if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
                  event.preventDefault();
                  void handleSubmit();
                }
              }}
            />
          )}

          {!isWaiting && !attachedSource && text.length > 0 && (
            <button
              type="button"
              className="absolute right-3 top-3 z-20 inline-flex h-9 w-9 items-center justify-center rounded-full text-subtle transition-colors hover:bg-surface/70 hover:text-ink focus-ring"
              onClick={() => {
                setText("");
                textareaRef.current?.focus();
              }}
              title="清空"
            >
              <X aria-hidden className="h-4 w-4" />
            </button>
          )}

          <div className="relative z-20 mx-5 mb-4 shrink-0 border-t border-hairline/68 px-0 pt-3 sm:mx-10 xl:mx-14">
            {isWaiting ? (
              <AnalysisLoadingStatusBar
                messagePrefix={waitingMessagePrefix}
              />
            ) : (
              <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
                <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2 font-sans">
                  {!attachedSource ? (
                    <button
                      type="button"
                      className="focus-ring group/source inline-flex min-h-9 items-center gap-2 px-0 text-[0.78rem] font-medium leading-none text-ink transition-colors duration-200 hover:text-lens-blue"
                      onClick={openFilePicker}
                    >
                      <span className="inline-flex h-6 w-6 items-center justify-center rounded-[7px] border border-ink/12 bg-reader-paper/54 text-ink transition-colors duration-200 group-hover/source:border-lens-blue/34 group-hover/source:text-lens-blue">
                        <FileUp aria-hidden className="h-3.5 w-3.5" />
                      </span>
                      <span>上传文件</span>
                    </button>
                  ) : (
                    <div className="inline-flex min-h-9 items-center gap-2 text-[0.78rem] font-semibold text-ink">
                      <span className="inline-flex h-6 w-6 items-center justify-center rounded-[7px] border border-ink/12 bg-reader-paper/54 text-ink">
                        <FileCheck2 aria-hidden className="h-3.5 w-3.5" />
                      </span>
                      <span>文件来源已就绪</span>
                    </div>
                  )}
                </div>

                <div className="flex flex-col-reverse items-stretch gap-3 sm:flex-row sm:items-center sm:justify-end">
                  <Popover>
                    <PopoverTrigger asChild>
                      <button
                        type="button"
                        className="focus-ring inline-flex min-h-10 items-center justify-center gap-2 whitespace-nowrap rounded-[10px] border border-transparent px-3 font-sans text-[0.8rem] font-medium leading-none text-muted transition-colors duration-200 hover:bg-reader-paper/46 hover:text-ink data-[state=open]:bg-reader-paper/60 data-[state=open]:text-ink"
                      >
                        <span>
                          模式：{selectedGoalLabel}
                          {selectedVariantLabel && selectedVariantLabel !== "学术通用" ? ` · ${selectedVariantLabel}` : ""}
                        </span>
                        <ChevronDown aria-hidden className="h-3.5 w-3.5" />
                      </button>
                    </PopoverTrigger>
                    <PopoverContent
                      align="end"
                      side="top"
                      sideOffset={14}
                      className="w-[min(420px,calc(100vw-2rem))] rounded-[20px] border border-hairline/78 bg-[color-mix(in_srgb,var(--surface)_96%,var(--reader-paper)_4%)] p-4 shadow-[0_24px_48px_rgba(23,21,17,0.14)] data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 data-[side=top]:slide-in-from-bottom-2"
                    >
                      <div className="flex items-center justify-between gap-4 px-1.5 pb-2 font-sans">
                        <p className="text-[0.85rem] font-semibold tracking-tight text-ink">透读模式</p>
                        <span className="max-w-[14rem] truncate text-right text-[0.72rem] font-medium tracking-tight text-muted">
                          当前：{selectedGoalLabel}
                          {selectedVariantLabel && selectedVariantLabel !== "学术通用" ? ` · ${selectedVariantLabel}` : ""}
                        </span>
                      </div>

                      <div className="mt-1 flex gap-2">
                        {READER_RECORD_READING_GOAL_OPTIONS.map((goal) => (
                          <div key={goal.value} className="flex-1">
                            <GoalCard
                              goal={goal}
                              active={goal.value === readingGoal}
                              onSelect={() => {
                                setReadingGoal(goal.value);
                                const variants = READER_RECORD_READING_VARIANT_OPTIONS[goal.value];
                                if (!variants.find((v) => v.value === readingVariant)) {
                                  setReadingVariant(READER_RECORD_DEFAULT_READING_VARIANT_BY_GOAL[goal.value] || variants[0].value);
                                }
                              }}
                            />
                          </div>
                        ))}
                      </div>

                      <div className="mt-4 min-h-[7rem] px-1 pb-0.5">
                        <div className="mb-3 flex items-center gap-3">
                          <span className="shrink-0 text-[0.72rem] font-semibold tracking-tight text-muted/90">细分方式</span>
                          <div className="h-px flex-1 bg-hairline/60" />
                        </div>
                        <div className="grid grid-cols-3 gap-2">
                          {READER_RECORD_READING_VARIANT_OPTIONS[readingGoal].map((variant) => (
                            <VariantPill
                              key={variant.value}
                              variant={variant}
                              active={variant.value === readingVariant}
                              onSelect={() => setReadingVariant(variant.value)}
                            />
                          ))}
                        </div>
                      </div>
                    </PopoverContent>
                  </Popover>

                  {!attachedSource && text.length > 0 ? (
                    <span className="self-center font-sans text-[0.72rem] font-medium text-subtle">
                      {text.trim().length.toLocaleString("en-US")} chars
                    </span>
                  ) : null}

                  <ApertureCornerSubmitButton
                    isPending={isSubmitting}
                    isReady={isReadyToSubmit}
                    onClick={handleSubmit}
                  />
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {state.kind !== "idle" && !isWaiting && state.kind !== "candidate" && state.kind !== "rejected" && state.kind !== "resume-not-found" && state.kind !== "resume-return-to-library" && state.kind !== "resume-failed" ? (
        <div
          className={`mt-4 shrink-0 rounded-[14px] border border-hairline/70 bg-surface/42 px-4 py-3 text-[0.82rem] font-medium lg:mx-12 ${
            state.kind === "error" ? "text-red-700" : "text-lens-blue"
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
                <p className="mt-1 text-[0.76rem] text-muted">
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
                    setText(state.candidate.inputSnapshot ?? "");
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
              router.push(appReadingRecordRoute(candidate.readingRecordId));
            }}
            onRestart={(candidate) => {
              setText(candidate.inputSnapshot ?? "");
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
          className="mt-4 shrink-0 rounded-[14px] border border-hairline/70 bg-surface/42 px-4 py-3 font-sans text-[0.82rem] font-medium text-red-700 lg:mx-12"
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
            <p className="mt-2 whitespace-pre-wrap rounded-[8px] border border-hairline/60 bg-reader-paper/40 p-2 text-[0.74rem] text-muted">
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
          className="mt-4 shrink-0 rounded-[14px] border border-hairline/70 bg-surface/42 px-4 py-3 font-sans text-[0.82rem] font-medium text-red-700 lg:mx-12"
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
          className="mt-4 shrink-0 rounded-[14px] border border-hairline/70 bg-surface/42 px-4 py-3 font-sans text-[0.82rem] font-medium text-red-700 lg:mx-12"
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
          className="mt-4 shrink-0 rounded-[14px] border border-hairline/70 bg-surface/42 px-4 py-3 font-sans text-[0.82rem] font-medium text-red-700 lg:mx-12"
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
