"use client";

/**
 * ContentCheckPanel — 同页 Content Check（L2 Confirmed Source，mock BFF）。
 *
 * 提交后输入主区域在同一路由内平滑切换为 Content Check。展示结构化
 * Plate 预览（复用输入页 MarkdownTextInput 编辑器，WYSIWYG 形态，不展示
 * raw Markdown），编辑同一份 Source Draft，防抖自动保存（PUT
 * confirmed-source, expected_revision 乐观并发），服务端 reparse 后刷新
 * quality / adaptation_notice / content_check。
 *
 * 视觉原则：正文预览是主舞台；"需要你决定"的位置以安静卡片列在右侧
 * review 队列，按 tier 分层（attention = 高影响风险，routine = 常规过目），
 * 不使用警告色大片铺陈；后端英文诊断（code/message）只进「技术详情」
 * 折叠区，不上主屏。
 *
 * 操作层级：主"确认并开始阅读"、次"返回修改"、低噪"稍后处理"。
 */

import { useCallback, useRef, useState } from "react";
import {
  AlertTriangle,
  ArrowRight,
  Check,
  ChevronDown,
  FileText,
  RefreshCw,
  Wrench,
} from "lucide-react";

import { Button } from "@/components/primitives/button";
import { cn } from "@/lib/cn";
import type { ReaderAdaptationRecordDto } from "@/types/api/reader-plate";
import {
  applyContentCheckAutoFix,
  guidanceForContentCheckCode,
  locateContentCheckExcerpt,
} from "./content-check-guidance";
import {
  MarkdownTextInput,
  type MarkdownTextInputHandle,
} from "./MarkdownTextInput";
import { TextAction } from "@/components/primitives/text-action";
import { readRejectedReasons, useContentCheck } from "./use-content-check";

export interface ContentCheckPanelDeferInfo {
  recordId: string;
  candidateDocumentId: string | null;
  canonicalTextPreview: string | null;
}

export interface ContentCheckPanelProps {
  recordId: string;
  filename?: string | null;
  /** submit = 本页刚提交；resume = 从恢复入口进入（隐藏"返回修改"）。 */
  origin: "submit" | "resume";
  onOpenReader: (recordId: string) => void;
  onConfirmed: (recordId: string) => void;
  /** 该 record 没有 Confirmed Source 行（L2 前存量记录）。 */
  onSourceMissing: () => void;
  /** 返回修改：把当前草稿文本交回输入编辑器。 */
  onBackToInput: (markdown: string) => void;
  /** 稍后处理：父组件负责持久化恢复入口并复位输入页。 */
  onDefer: (info: ContentCheckPanelDeferInfo) => void;
}

function AdaptationNoticeRail({
  items,
}: {
  items: ReaderAdaptationRecordDto[];
}) {
  const [expanded, setExpanded] = useState(false);
  if (items.length === 0) return null;
  return (
    <div
      data-testid="content-check-adaptation-notice"
      className="rounded-[10px] border border-hairline/70 bg-surface/54 px-4 py-3 font-sans"
    >
      <button
        type="button"
        aria-expanded={expanded}
        onClick={() => setExpanded((current) => !current)}
        className="focus-ring flex w-full items-center justify-between gap-3 text-left"
      >
        <span className="inline-flex items-center gap-2 text-[0.78rem] font-medium text-muted-foreground">
          <Check aria-hidden className="h-3.5 w-3.5 text-lens-blue" />
          已自动处理 {items.length} 项格式问题，不影响阅读
        </span>
        <ChevronDown
          aria-hidden
          className={cn(
            "h-3.5 w-3.5 text-subtle transition-transform duration-200",
            expanded && "rotate-180",
          )}
        />
      </button>
      {expanded ? (
        <ul className="mt-3 space-y-1.5 border-t border-hairline/60 pt-3 text-[0.76rem] leading-5 text-muted-foreground">
          {items.map((item, index) => {
            const guidance = guidanceForContentCheckCode(item.code);
            return (
              <li key={`${item.code}-${index}`}>
                <span className="font-medium text-ink/80">{guidance.title}</span>
                {item.message ? (
                  <details className="mt-0.5 text-[0.72rem] text-subtle">
                    <summary>技术详情</summary>
                    <p className="mt-1 font-mono">
                      {item.code}
                      {item.message ? ` · ${item.message}` : ""}
                      <span className="ml-1 font-sans">（诊断信息）</span>
                    </p>
                  </details>
                ) : null}
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}

export function ContentCheckPanel({
  recordId,
  filename,
  origin,
  onOpenReader,
  onConfirmed,
  onSourceMissing,
  onBackToInput,
  onDefer,
}: ContentCheckPanelProps) {
  const editorRef = useRef<MarkdownTextInputHandle | null>(null);
  const {
    state,
    workingMarkdown,
    resolvedCheckCodes,
    handleEdit,
    saveNow,
    confirmAndStart,
    reloadLatest,
    retryWithLatestRevision,
    retryLoad,
    resolveCheckCode,
    resolveAllCheckCodes,
    unresolveCheckCode,
  } = useContentCheck({ recordId, onOpenReader, onSourceMissing, onConfirmed });

  const draft = state.draft;
  const checkEntries =
    draft?.contentCheck.map((item, index) => ({
      item,
      key: `${draft.revision}:${index}:${item.code}`,
    })) ?? [];
  const unresolvedChecks = checkEntries.filter(
    (entry) => !resolvedCheckCodes.has(entry.key),
  );
  const resolvedChecks = checkEntries.filter((entry) =>
    resolvedCheckCodes.has(entry.key),
  );
  const resolvedCount = resolvedChecks.length;
  const attentionChecks = unresolvedChecks.filter(
    (entry) => guidanceForContentCheckCode(entry.item.code).tier === "attention",
  );
  const routineChecks = unresolvedChecks.filter(
    (entry) => guidanceForContentCheckCode(entry.item.code).tier === "routine",
  );

  const flushEditor = useCallback((): string => {
    return editorRef.current?.flush() ?? workingMarkdown;
  }, [workingMarkdown]);

  function handleAutoFix(code: string, resolutionKey: string) {
    const current = flushEditor();
    const fixed = applyContentCheckAutoFix(code, current);
    if (fixed !== null) {
      editorRef.current?.setValue(fixed);
      handleEdit(fixed);
    }
    resolveCheckCode(resolutionKey);
  }

  async function handleReloadLatest() {
    const latest = await reloadLatest();
    if (latest !== null) {
      editorRef.current?.setValue(latest);
    }
  }

  const isBusy = state.phase === "saving" || state.phase === "confirming";
  const isStableReady = draft?.outcome === "stable_document_ready";
  const isRejected = draft?.outcome === "input_rejected_or_action_required";
  // rejected/no-candidate 草稿在用户修改后仍需允许重新保存检查；只有
  // “未修改且没有 candidate”才没有可执行的确认动作。
  const canAttemptConfirm =
    state.dirty || isStableReady || Boolean(draft?.candidate);
  const primaryLabel = isStableReady
    ? "开始阅读"
    : state.phase === "confirming"
      ? "确认中…"
      : "确认并开始阅读";
  const handleConfirm = () => {
    if (
      isBusy ||
      (isRejected && !state.dirty) ||
      attentionChecks.length > 0 ||
      !canAttemptConfirm
    ) {
      return;
    }
    void confirmAndStart(flushEditor());
  };

  const handleDefer = async () => {
    const text = flushEditor();
    const needsSave = text !== draft?.savedMarkdown || state.dirty;
    if (needsSave) {
      const saved = await saveNow(text);
      if (!saved) return;
    }
    onDefer({
      recordId,
      // A save may reparse the document and replace its candidate. Avoid
      // persisting stale candidate metadata; recordId is the resume authority.
      candidateDocumentId: needsSave
        ? null
        : (draft?.candidate?.candidate_document_id ?? null),
      canonicalTextPreview: needsSave
        ? null
        : (draft?.candidate?.canonical_text_preview ?? null),
    });
  };

  if (state.phase === "loading" && !draft) {
    return (
      <section
        data-testid="content-check-panel"
        aria-live="polite"
        className="flex min-h-[24rem] flex-1 items-center justify-center rounded-[10px] bg-surface/40 ring-1 ring-hairline/35 motion-safe:animate-in motion-safe:fade-in-0 motion-safe:duration-200 motion-reduce:animate-none lg:min-h-[32rem]"
      >
        <p className="font-sans text-[0.86rem] font-medium text-muted-foreground">
          正在载入待确认的内容…
        </p>
      </section>
    );
  }

  if (state.phase === "error" && !draft) {
    return (
      <section
        data-testid="content-check-panel"
        role="alert"
        className="flex min-h-[24rem] flex-1 flex-col items-center justify-center gap-4 rounded-[10px] bg-surface/40 px-8 ring-1 ring-hairline/35 motion-safe:animate-in motion-safe:fade-in-0 motion-safe:duration-200 motion-reduce:animate-none lg:min-h-[32rem]"
      >
        <p className="font-sans text-[0.9rem] font-medium text-feedback-error">
          {state.errorMessage ?? "加载失败，请稍后重试。"}
        </p>
        <Button type="button" variant="secondary" size="sm" onClick={retryLoad}>
          重试加载
          <RefreshCw aria-hidden className="ml-1 h-3.5 w-3.5" />
        </Button>
      </section>
    );
  }

  const hasRail =
    unresolvedChecks.length > 0 ||
    resolvedCount > 0 ||
    Boolean(draft && draft.adaptationNotice.length > 0) ||
    isRejected;

  const statusSummary =
    unresolvedChecks.length === 0
      ? "正文已就绪，确认后开始阅读"
      : attentionChecks.length > 0
        ? `有 ${attentionChecks.length} 处需要你决定${
            routineChecks.length > 0 ? `，另有 ${routineChecks.length} 处建议过目` : ""
          }`
        : `有 ${routineChecks.length} 处建议你看一眼`;

  function renderCheckCard({
    item,
    key,
  }: {
    item: ReaderAdaptationRecordDto;
    key: string;
  }) {
    const guidance = guidanceForContentCheckCode(item.code);
    const isAttention = guidance.tier === "attention";
    const excerpt = draft
      ? locateContentCheckExcerpt(item.code, workingMarkdown)
      : null;
    return (
      <article
        key={key}
        data-testid="content-check-risk-item"
        data-code={item.code}
        className="rounded-[10px] border border-hairline/70 bg-surface/60 px-4 py-3"
      >
        <div className="flex items-start justify-between gap-2">
          <p className="text-[0.8rem] font-semibold leading-5 text-ink">
            {guidance.title}
          </p>
          <span
            className={cn(
              "shrink-0 rounded-full px-2 py-0.5 text-[0.64rem] font-semibold tracking-[0.05em]",
              isAttention
                ? "bg-feedback-warning-soft text-ink/75"
                : "bg-surface-raised text-subtle",
            )}
          >
            {isAttention ? "需要决定" : "建议过目"}
          </span>
        </div>
        <p className="mt-1.5 text-[0.76rem] leading-5 text-muted-foreground">
          {guidance.suggestion}
        </p>
        {excerpt ? (
          <button
            type="button"
            data-testid="content-check-reveal"
            title={undefined}
            onClick={() => editorRef.current?.reveal(excerpt)}
            className="focus-ring mt-2 block w-full cursor-pointer rounded-[8px] text-left transition-colors hover:ring-1 hover:ring-lens-blue/30"
          >
            <pre className="overflow-x-auto whitespace-pre-wrap rounded-[8px] border border-hairline/60 bg-surface/54 px-3 py-2 font-mono text-[0.72rem] leading-5 text-ink/78">
              {excerpt}
            </pre>
            <span className="mt-1 inline-block font-sans text-[0.68rem] font-medium text-subtle">
              点击在正文中定位
            </span>
          </button>
        ) : null}
        {item.message ? (
          <details className="mt-2 text-[0.72rem] text-subtle">
            <summary>技术详情</summary>
            <p className="mt-1 font-mono leading-5">
              {item.code} · {item.message}
              <span className="ml-1 font-sans">（诊断信息）</span>
            </p>
          </details>
        ) : null}
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {guidance.hasAutoFix ? (
            <Button
              type="button"
              variant="secondary"
              size="sm"
              className="max-sm:min-h-11"
              onClick={() => handleAutoFix(item.code, key)}
            >
              <Wrench aria-hidden className="mr-1 h-3 w-3" />
              采用建议
            </Button>
          ) : null}
          <Button
            type="button"
            variant="secondary"
            size="sm"
            className="max-sm:min-h-11"
            onClick={() => resolveCheckCode(key)}
          >
            确认无误
          </Button>
        </div>
      </article>
    );
  }

  return (
    <section
      data-testid="content-check-panel"
      aria-labelledby="content-check-title"
      className="flex h-[calc(100dvh-8rem)] min-h-[24rem] max-h-[calc(100dvh-2rem)] w-full flex-col overflow-hidden rounded-[10px] bg-surface/40 ring-1 ring-hairline/35 motion-safe:animate-in motion-safe:fade-in-0 motion-safe:slide-in-from-bottom-2 motion-safe:duration-200 motion-reduce:animate-none lg:min-h-[32rem]"
    >
      <header className="shrink-0 border-b border-hairline/68 px-5 pb-4 pt-5 sm:px-8">
        <p className="text-[0.68rem] font-bold tracking-[0.14em] text-lens-blue">
          Content Check
        </p>
        <h2
          id="content-check-title"
          className="mt-1.5 font-headline text-[1.45rem] font-semibold leading-tight tracking-[-0.015em] text-ink sm:text-[1.62rem]"
        >
          确认识别出的正文
        </h2>
        <p className="mt-2 inline-flex items-center gap-1.5 font-sans text-[0.74rem] font-medium text-subtle">
          <FileText aria-hidden className="h-3.5 w-3.5" />
          {filename?.trim() ? `来源：${filename.trim()}` : "来源：粘贴文本"}
          <span aria-hidden>·</span>
          <span>正文可直接修改，修改会自动保存</span>
        </p>
      </header>

      {state.phase === "conflict" ? (
        <div
          data-testid="content-check-conflict"
          role="alert"
          className="mx-5 mt-4 shrink-0 rounded-[10px] border border-feedback-warning/40 bg-feedback-warning-soft px-4 py-3 font-sans sm:mx-8"
        >
          <p className="text-[0.8rem] font-semibold text-ink/80">
            {state.errorMessage ?? "草稿已被其他更新抢先保存。"}
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={() => void handleReloadLatest()}
              className="max-sm:min-h-11"
            >
              载入最新版本（放弃我的修改）
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => void retryWithLatestRevision()}
              className="max-sm:min-h-11"
            >
              以我的修改重试
            </Button>
          </div>
        </div>
      ) : null}

      {draft ? (
        <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-hairline/60 px-5 py-2.5 font-sans sm:px-8">
          <p className="inline-flex items-center gap-2 text-[0.76rem] font-medium text-muted-foreground">
            {unresolvedChecks.length === 0 ? (
              <Check aria-hidden className="h-3.5 w-3.5 text-lens-blue" />
            ) : null}
            {statusSummary}
          </p>
          {routineChecks.length > 0 ? (
            <TextAction
              data-testid="content-check-keep-all-plain"
              className="max-sm:min-h-11"
              onClick={() => resolveAllCheckCodes(routineChecks.map((entry) => entry.key))}
            >
              确认全部普通建议
            </TextAction>
          ) : null}
        </div>
      ) : null}

      <div
        className={cn(
          "flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-5 py-4 sm:px-8",
          hasRail &&
            "xl:grid xl:grid-cols-[minmax(0,1fr)_19.5rem] xl:grid-rows-[minmax(0,1fr)] xl:gap-8",
        )}
      >
        <div className="order-2 min-h-[18rem] flex-1 xl:order-1 xl:col-start-1 xl:row-start-1">
          <label htmlFor="content-check-editor" className="sr-only">
            待确认正文预览与编辑
          </label>
          {draft ? (
            <MarkdownTextInput
              ref={editorRef}
              key={draft.sourceDocumentId}
              id="content-check-editor"
              ariaLabelledBy="content-check-title"
              initialValue={draft.savedMarkdown}
              onChange={handleEdit}
              onSubmit={handleConfirm}
              className="mx-auto h-full min-h-[18rem] w-full max-w-[52rem] overflow-y-auto rounded-[12px] border border-hairline/70 bg-surface px-6 py-6 font-sans text-base leading-[1.68] text-ink shadow-[var(--app-panel-shadow-quiet)] selection:bg-lens-blue/15 selection:text-ink max-sm:h-auto max-sm:overflow-visible sm:px-10 sm:py-8"
            />
          ) : null}
        </div>

        {hasRail ? (
          <aside
            data-testid="content-check-summary-rail"
            className="order-1 flex flex-col gap-4 xl:order-2 xl:col-start-2 xl:row-start-1 xl:self-start xl:sticky xl:top-0"
          >
            {draft && draft.adaptationNotice.length > 0 ? (
              <AdaptationNoticeRail items={draft.adaptationNotice} />
            ) : null}

            {unresolvedChecks.length > 0 ? (
              <div
                data-testid="content-check-risk-list"
                className="space-y-3 font-sans"
              >
                {attentionChecks.map((entry) => renderCheckCard(entry))}
                {routineChecks.map((entry) => renderCheckCard(entry))}
              </div>
            ) : null}

            {resolvedCount > 0 ? (
              <div
                data-testid="content-check-resolved-summary"
                className="rounded-[10px] border border-hairline/60 bg-surface/40 px-4 py-3 font-sans"
              >
                <p className="text-[0.74rem] font-medium text-subtle">
                  已处理 {resolvedCount} 项
                </p>
                <ul className="mt-2 space-y-1.5">
                  {resolvedChecks.map(({ item, key }) => (
                    <li
                      key={key}
                      className="flex items-center justify-between gap-2 text-[0.74rem]"
                    >
                      <span className="min-w-0 truncate text-muted-foreground">
                        {guidanceForContentCheckCode(item.code).title}
                      </span>
                      <TextAction
                        onClick={() => unresolveCheckCode(key)}
                        className="min-h-0 px-0 text-[0.72rem]"
                      >
                        撤销
                      </TextAction>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}

            {isRejected ? (
              <div
                data-testid="content-check-rejected"
                role="alert"
                className="rounded-[10px] border border-feedback-warning/40 bg-feedback-warning-soft px-4 py-3 font-sans"
              >
                <p className="inline-flex items-center gap-2 text-[0.8rem] font-semibold text-ink/80">
                  <AlertTriangle
                    aria-hidden
                    className="h-3.5 w-3.5 text-feedback-warning"
                  />
                  当前内容无法生成阅读版本
                </p>
                <ul className="mt-1.5 list-disc space-y-0.5 pl-4 text-[0.76rem] leading-5 text-ink/78">
                  {(draft
                    ? readRejectedReasons(draft.quality, draft.contentCheck)
                    : []
                  )
                    .slice(0, 3)
                    .map((reason) => <li key={reason}>{reason}</li>)}
                </ul>
              </div>
            ) : null}
          </aside>
        ) : null}
      </div>

      <footer className="shrink-0 border-t border-hairline/68 px-5 py-3.5 sm:px-8">
        <div className="flex min-w-0 flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <p
            data-testid="content-check-save-status"
            aria-live="polite"
            className="min-w-0 font-sans text-[0.74rem] font-medium text-muted-foreground"
          >
            {state.phase === "saving"
              ? "正在保存并重新检查…"
              : state.errorMessage && state.phase !== "conflict"
                ? state.errorMessage
                : state.infoMessage
                  ? state.infoMessage
                  : state.dirty
                    ? "有未保存的修改…"
                    : draft
                      ? "已自动保存"
                      : ""}
          </p>
          <div className="flex shrink-0 flex-col items-stretch gap-1.5 sm:items-end">
            {draft ? (
              <p className="font-sans text-[0.72rem] font-medium text-subtle">
                确认后正文冻结，将进入阅读
              </p>
            ) : null}
            <div className="flex flex-wrap items-center gap-2">
              <TextAction
                disabled={isBusy}
                className="max-sm:min-h-11"
                onClick={() => void handleDefer()}
              >
                稍后处理
              </TextAction>
              {origin === "submit" ? (
                <TextAction
                  disabled={isBusy}
                  className="max-sm:min-h-11"
                  onClick={() => onBackToInput(flushEditor())}
                >
                  重新输入
                </TextAction>
              ) : null}
              {state.errorMessage && state.dirty && state.phase === "ready" ? (
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  disabled={isBusy}
                  className="max-sm:min-h-11"
                  onClick={() => void saveNow()}
                >
                  重试保存
                </Button>
              ) : null}
              <Button
                type="button"
                variant="primary-ink"
                size="sm"
                data-testid="content-check-confirm-button"
                disabled={
                  isBusy ||
                  (isRejected && !state.dirty) ||
                  attentionChecks.length > 0 ||
                  !canAttemptConfirm
                }
                className="max-sm:min-h-11"
                onClick={handleConfirm}
              >
                {primaryLabel}
                {state.phase !== "confirming" ? (
                  <ArrowRight aria-hidden className="ml-1 h-3.5 w-3.5" />
                ) : null}
              </Button>
            </div>
          </div>
        </div>
      </footer>
    </section>
  );
}
