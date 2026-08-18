"use client";

import { AlertTriangle, ArrowRight, Check, ChevronDown, FileText, FileType, FileUp, ImageIcon, RefreshCw, X } from "lucide-react";
import Image from "next/image";
import type { Route } from "next";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState, type ChangeEvent, type DragEvent, type ReactNode } from "react";
import { ReadingPlanFields } from "@/components/composed";
import { Button } from "@/components/primitives/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/primitives/popover";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/primitives/tooltip";
import { cn } from "@/lib/cn";
import { userFacingErrorCopy } from "@/lib/user-facing-error";
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
  ReaderPlateBffError,
  ReaderSourceArtifactSubmitInputResult,
  ReaderSourceArtifactUploadCompleteResult,
  ReaderSourceArtifactUploadInitResult,
} from "@/services/bff/reader-plate";
import {
  clearPendingCandidate,
  readPendingCandidate,
  savePendingCandidate,
} from "./pending-candidate";
import { TextAction } from "@/components/primitives/text-action";
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
import { rejectedReasonCopyForFlags } from "./content-check-guidance";
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
      /** L2 同页 Content Check。Confirmed Source 404 走 resume-not-found。 */
      kind: "content-check";
      recordId: string;
      filename: string | null;
      inputSnapshot: string | null;
      origin: "submit" | "resume";
    }
  | {
      kind: "rejected";
      reasons: string[];
      preview: string;
    }
  | { kind: "resume-not-found"; recordId: string; message: string }
  | { kind: "resume-return-to-library"; message: string }
  | { kind: "resume-failed"; recordId: string; message: string };

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
    return `${Math.max(1, Math.round(bytes / 1024))} KB`;
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
          {isPending ? "透读中…" : "开始透读"}
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
  // 静态品牌插画：不叠加编排动效（DESIGN.md 禁止用动效表演解析过程）。
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
        </div>
      </div>
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
  detail,
}: {
  messagePrefix: string;
  /** 真实阶段状态（pipeline next_action 映射），无则不显示。 */
  detail?: string;
}) {
  return (
    <div className="flex min-h-12 max-w-[38rem] items-center gap-3 font-sans text-[0.78rem]">
      <MiniAperturePulse className="h-8 w-8 bg-surface/76" />
      <div className="min-w-0">
        <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
          <p className="shrink-0 font-semibold text-ink">{messagePrefix}</p>
          {detail ? (
            <>
              <span className="hidden h-1 w-1 shrink-0 rounded-full bg-hairline sm:inline-flex" aria-hidden="true" />
              <p className="min-w-0 text-[0.75rem] font-semibold text-ink/74" aria-live="polite">
                {detail}
              </p>
            </>
          ) : null}
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

const SOURCE_KIND_ICONS: Record<SourceFileKind, typeof FileText> = {
  pdf: FileText,
  markdown: FileType,
  text: FileType,
  image: ImageIcon,
};

/**
 * 上传文件确认态：居中紧凑「落签卡」——文件信息 + 竖排三步预告 + 操作。
 * 只支持单文件，内容量小就让卡片小而完整，不再用全宽行 + 大片空白硬撑。
 */
function SourceFilePreview({
  source,
  onReplace,
  onRemove,
  stashedTextHint,
}: {
  source: AttachedSource;
  onReplace: () => void;
  onRemove: () => void;
  stashedTextHint?: boolean;
}) {
  const { descriptor } = source;
  const KindIcon = SOURCE_KIND_ICONS[descriptor.kind];

  return (
    <div
      data-testid="source-file-preview"
      className="relative z-10 flex min-h-0 flex-1 flex-col items-center justify-center px-5 py-8 sm:px-8"
    >
      <div className="w-full max-w-[32rem] rounded-[12px] border border-hairline/70 bg-surface px-6 py-6 shadow-[var(--app-panel-shadow-quiet)]">
        <div
          data-testid="attached-source"
          className="flex min-w-0 items-center gap-4 font-sans"
        >
          <span className="inline-flex h-12 w-12 shrink-0 items-center justify-center rounded-[10px] border border-ink/10 bg-surface-raised/60 text-ink">
            <KindIcon aria-hidden className="h-5 w-5" />
          </span>
          <div className="min-w-0 flex-1">
            <Tooltip>
              <TooltipTrigger asChild>
                <p className="cursor-default truncate text-[0.98rem] font-semibold leading-6 text-ink">
                  {source.file.name}
                </p>
              </TooltipTrigger>
              <TooltipContent>{source.file.name}</TooltipContent>
            </Tooltip>
            <p className="mt-0.5 text-[0.78rem] font-medium text-muted-foreground">
              {SOURCE_FORMAT_SHORT_LABELS[descriptor.kind]} · {formatFileSize(source.file.size)}
            </p>
          </div>
        </div>

        <ol className="mt-5 space-y-2.5 border-t border-hairline/60 pt-5 font-sans text-[0.8rem]">
          {["提取文字", "可能需要你过目", "开始阅读"].map((step, index) => (
            <li key={step} className="flex items-center gap-2.5">
              <span
                aria-hidden="true"
                className={cn(
                  "inline-flex h-4.5 w-4.5 shrink-0 items-center justify-center rounded-full border text-[0.62rem] font-semibold tabular-nums",
                  index === 0
                    ? "border-lens-blue/40 bg-lens-blue-soft text-lens-blue"
                    : "border-hairline/80 text-subtle",
                )}
              >
                {index + 1}
              </span>
              <span
                className={cn(
                  "font-medium",
                  index === 0 ? "text-ink" : "text-muted-foreground",
                )}
              >
                {step}
              </span>
            </li>
          ))}
        </ol>

        {stashedTextHint ? (
          <p className="mt-4 font-sans text-[0.74rem] font-medium text-muted-foreground">
            已暂存你粘贴的内容，移除文件后恢复
          </p>
        ) : null}

        <div className="mt-5 flex items-center justify-end gap-3 border-t border-hairline/60 pt-4">
          <TextAction onClick={onReplace}>更换</TextAction>
          <TextAction onClick={onRemove} className="hover:text-feedback-error">
            移除
          </TextAction>
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
  const stashedEditorTextRef = useRef<string | null>(null);
  const [text, setText] = useState("");
  const [attachedSource, setAttachedSource] = useState<AttachedSource | null>(null);
  const [isDragActive, setDragActive] = useState(false);
  const defaults = normalizeReaderRecordReadingDefaults({ readingGoal: initialGoal, readingVariant: initialVariant });
  const [readingGoal, setReadingGoal] = useState<ReaderRecordReadingGoal>(defaults.readingGoal);
  const [readingVariant, setReadingVariant] = useState<ReaderRecordReadingVariant>(defaults.readingVariant);
  const [isReadingPlanOpen, setReadingPlanOpen] = useState(false);
  const [state, setState] = useState<SubmitState>({ kind: "idle" });
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
  // Markdown 结构识别确认（安静、非阻塞）：让用户看到格式被理解。
  const markdownStructureLabels = useMemo(() => {
    if (!text.trim()) return [];
    const labels: string[] = [];
    if (/^#{1,6}\s/m.test(text)) labels.push("标题");
    if (/^\s*(?:[-*+]|\d+\.)\s/m.test(text)) labels.push("列表");
    if (/^\s*\|.*\|\s*$/m.test(text)) labels.push("表格");
    if (/^```/m.test(text)) labels.push("代码块");
    if (/^>\s?/m.test(text)) labels.push("引用");
    return labels;
  }, [text]);

  // L2/L3：编辑器有内容（或进入 Content Check）后收起 Hero，编辑器成首屏主任务。
  // Content Check / 等待解析进入 focusMode：右侧「今日精选」收起，任务区获得全宽。
  const { setHasContent, setFocusMode } = useReadPageUi();
  useEffect(() => {
    setHasContent(
      Boolean(attachedSource) || text.trim().length > 0 || state.kind === "content-check",
    );
  }, [attachedSource, text, state.kind, setHasContent]);
  useEffect(() => {
    setFocusMode(state.kind === "content-check" || isWaiting);
  }, [state.kind, isWaiting, setFocusMode]);

  useEffect(() => {
    const searchParams = new URLSearchParams(window.location.search);
    const resumeRecordId = searchParams.get("resume_candidate")?.trim() ?? "";

    if (resumeRecordId) {
      setState({
        kind: "content-check",
        recordId: resumeRecordId,
        filename: null,
        inputSnapshot: null,
        origin: "resume",
      });
      return;
    }

    const timer = window.setTimeout(() => {
      const pending = readPendingCandidate();
      if (pending) {
        const snapshot = pending.inputSnapshot ?? "";
        setText(snapshot);
        markdownEditorRef.current?.setValue(snapshot);
        setState({
          kind: "content-check",
          recordId: pending.readingRecordId,
          filename: pending.filename ?? null,
          inputSnapshot: pending.inputSnapshot ?? null,
          origin: pending.origin === "resume" ? "resume" : "submit",
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
    const stashed = stashedEditorTextRef.current;
    stashedEditorTextRef.current = null;
    if (stashed !== null) {
      setText(stashed);
      markdownEditorRef.current?.setValue(stashed);
    }
    setState({ kind: "idle" });
    resetFileInput();
    markdownEditorRef.current?.focus();
  }

  function selectSourceFile(file: File) {
    stopPolling();
    clearPendingCandidate();
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

    if (stashedEditorTextRef.current === null) {
      const live = markdownEditorRef.current?.flush() ?? text;
      stashedEditorTextRef.current = live;
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
          message: "解析服务暂时没响应，请稍后重试或换个文件",
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
      savePendingCandidate({
        readingRecordId,
        candidateDocumentId,
        originalInputId: null,
        inputSnapshot: null,
        filename: currentFilename,
      });
      setState({
        kind: "content-check",
        recordId: readingRecordId,
        filename: currentFilename,
        inputSnapshot: null,
        origin: "submit",
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
      message: "正在准备上传…",
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
        message: "正在上传文件…",
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
        message: "正在提交文件…",
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
        message: userFacingErrorCopy(error, "文件处理失败，请稍后重试。"),
      });
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

    setState({ kind: "pending", message: "正在提交，准备解析…" });

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
          savePendingCandidate({
            readingRecordId: payload.reading_record_id,
            candidateDocumentId: payload.candidate_document_id,
            originalInputId: payload.original_input_id,
            inputSnapshot: trimmed,
          });
          setState({
            kind: "content-check",
            recordId: payload.reading_record_id,
            filename: null,
            inputSnapshot: trimmed,
            origin: "submit",
          });
          return;
        }
        case "input_rejected_or_action_required": {
          setState({
            kind: "rejected",
            // suitability.reasons 是后端英文诊断句，不上屏；按 flags 映射。
            reasons: rejectedReasonCopyForFlags(payload.suitability.flags ?? []),
            preview: payload.suitability.normalized_preview ?? "",
          });
          return;
        }
      }
    } catch (error: unknown) {
      setState({
        kind: "error",
        message: userFacingErrorCopy(error, "提交失败，请稍后重试。"),
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
    state.kind === "artifact-uploading"
      ? "正在上传这份文件"
      : state.kind === "artifact-polling"
        ? "正在提取这份来源"
        : "正在准备阅读";
  const waitingMessagePrefix =
    state.kind === "artifact-uploading"
      ? "正在上传"
      : state.kind === "artifact-polling"
        ? "正在提取"
        : "正在准备";
  const waitingDetail =
    state.kind === "artifact-uploading" ||
    state.kind === "artifact-polling" ||
    state.kind === "pending"
      ? state.message
      : undefined;

  return (
    <TooltipProvider>
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
            onSourceMissing={() => {
              // Confirmed Source 404：记录已被删除，或来自无 source 行的
              // 旧版本。清理「稍后处理」的恢复入口，避免每次打开输入页
              // 都恢复成死链。
              clearPendingCandidate();
              setState({
                kind: "resume-not-found",
                recordId: state.recordId,
                message: "这条待确认的内容已不存在，可能已被删除，请重新提交",
              });
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
            // 文件已附着时编辑器不渲染，但保持工作台高度：落签卡在可用
            // 空间内垂直居中，避免顶部贴齐、下方大片空白。
            "min-h-[24rem] flex-1 lg:min-h-[26rem]",
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
              stashedTextHint={Boolean(stashedEditorTextRef.current?.trim())}
            />
          ) : (
            <MarkdownTextInput
              ref={markdownEditorRef}
              id="analysis-text"
              ariaLabelledBy="analysis-text-label"
              ariaDescribedBy="analysis-text-hint"
              placeholder="粘贴英文文章，或直接开始输入"
              placeholderSub="支持 Markdown / PDF / TXT / 图片"
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
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  aria-label="清空"
                  className="absolute right-3 top-3 z-20 inline-flex h-9 w-9 cursor-pointer items-center justify-center rounded-full text-subtle transition-colors hover:bg-surface/70 hover:text-ink focus-ring"
                  onClick={() => {
                    setText("");
                    setLintResult({ warnings: [], hasDangerousContent: false });
                    setDegradedMessage(null);
                    markdownEditorRef.current?.clear();
                    markdownEditorRef.current?.focus();
                  }}
                >
                  <X aria-hidden className="h-4 w-4" />
                </button>
              </TooltipTrigger>
              <TooltipContent>清空</TooltipContent>
            </Tooltip>
          )}

          <div className="relative z-20 mx-5 mb-4 shrink-0 border-t border-hairline/68 px-0 pt-3 sm:mx-8">
            {isWaiting ? (
              <AnalysisLoadingStatusBar
                messagePrefix={waitingMessagePrefix}
                detail={waitingDetail}
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
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <span className="cursor-default font-medium text-subtle">
                          {approxWordCount}
                        </span>
                      </TooltipTrigger>
                      <TooltipContent>
                        {`共 ${text.trim().length.toLocaleString("zh-CN")} 字符`}
                      </TooltipContent>
                    </Tooltip>
                  ) : null}

                  {!attachedSource && markdownStructureLabels.length > 0 ? (
                    <span
                      data-testid="read-source-structure-hint"
                      className="inline-flex items-center gap-1 font-medium text-subtle"
                    >
                      <Check aria-hidden className="h-3 w-3 text-lens-blue" />
                      已识别{markdownStructureLabels.join("、")}结构
                    </span>
                  ) : null}

                  {lintResult.hasDangerousContent ? (
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <span
                          data-testid="read-source-lint-warning"
                          className="inline-flex cursor-default items-center gap-1 font-semibold text-feedback-warning"
                        >
                          <AlertTriangle aria-hidden className="h-3 w-3" />
                          有 {lintResult.warnings.length} 处格式需要确认
                        </span>
                      </TooltipTrigger>
                      <TooltipContent>
                        {summarizeLintWarnings(lintResult.warnings)}
                      </TooltipContent>
                    </Tooltip>
                  ) : null}

                  {!attachedSource && degradedMessage ? (
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <span
                          data-testid="read-source-degraded-hint"
                          className="inline-flex cursor-default items-center gap-1 font-semibold text-feedback-warning"
                        >
                          <AlertTriangle aria-hidden className="h-3 w-3" />
                          部分格式已按纯文本显示
                        </span>
                      </TooltipTrigger>
                      <TooltipContent>{degradedMessage}</TooltipContent>
                    </Tooltip>
                  ) : null}
                </div>

                <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                  {!attachedSource ? (
                    <button
                      type="button"
                      className="focus-ring inline-flex min-h-10 shrink-0 items-center gap-2 self-start whitespace-nowrap rounded-[var(--cl-radius-control-sm)] border border-transparent bg-surface-raised/50 px-3 text-sm font-normal leading-none text-ink transition-colors duration-150 hover:border-hairline hover:bg-surface-raised motion-reduce:transition-none"
                      onClick={openFilePicker}
                    >
                      <FileUp aria-hidden className="h-3.5 w-3.5 text-subtle" />
                      <span>上传文件</span>
                    </button>
                  ) : null}

                  <div className="flex min-w-0 flex-col items-stretch gap-2 sm:ml-auto sm:shrink-0 sm:flex-row sm:items-center sm:justify-end">
                    {!attachedSource && !text.trim() ? (
                      <span className="self-center font-sans text-xs font-medium text-subtle sm:mr-1">
                        粘贴文章或上传文件后即可开始
                      </span>
                    ) : null}
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

      {state.kind !== "idle" && !isWaiting && state.kind !== "content-check" && state.kind !== "rejected" && state.kind !== "resume-not-found" && state.kind !== "resume-return-to-library" && state.kind !== "resume-failed" ? (
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
              onClick={() =>
                setState({
                  kind: "content-check",
                  recordId: state.recordId,
                  filename: null,
                  inputSnapshot: null,
                  origin: "resume",
                })
              }
            >
              重试加载
            </Button>
          </div>
        </section>
      ) : null}
    </div>
    </TooltipProvider>
  );
}
