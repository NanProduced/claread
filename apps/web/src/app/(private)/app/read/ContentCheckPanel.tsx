"use client";

/**
 * ContentCheckPanel — 同页 Content Check（L2 Confirmed Source，mock BFF）。
 *
 * 替代 CandidateConfirmDialog 模态：提交后输入主区域在同一路由内平滑
 * 切换为 Content Check。展示结构化 Plate 预览（复用输入页 MarkdownTextInput
 * 编辑器，WYSIWYG 形态，不展示 raw Markdown），编辑同一份 Source Draft，
 * 防抖自动保存（PUT confirmed-source, expected_revision 乐观并发），
 * 服务端 reparse 后刷新 quality / adaptation_notice / content_check。
 *
 * 操作层级：主"确认并开始阅读"、次"返回修改"、低噪"稍后处理"。
 */

import { useCallback, useRef, useState } from "react";
import {
  AlertTriangle,
  ArrowRight,
  Check,
  ChevronDown,
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
  /** 该 record 没有 Confirmed Source 行（L2 前存量记录），回退旧流程。 */
  onLegacyFallback: () => void;
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
          {items.map((item, index) => (
            <li key={`${item.code}-${index}`}>
              <span className="font-medium text-ink/80">{item.code}</span>
              {item.message ? (
                <span className="text-muted-foreground"> · {item.message}</span>
              ) : null}
            </li>
          ))}
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
  onLegacyFallback,
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
  } = useContentCheck({ recordId, onOpenReader, onLegacyFallback, onConfirmed });

  const draft = state.draft;
  const checkEntries =
    draft?.contentCheck.map((item, index) => ({
      item,
      key: `${draft.revision}:${index}:${item.code}`,
    })) ?? [];
  const unresolvedChecks = checkEntries.filter(
    (entry) => !resolvedCheckCodes.has(entry.key),
  );
  const resolvedCount = checkEntries.length - unresolvedChecks.length;

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
      ? "确认中..."
      : "确认并开始阅读";
  const handleConfirm = useCallback(() => {
    if (
      isBusy ||
      (isRejected && !state.dirty) ||
      unresolvedChecks.length > 0 ||
      !canAttemptConfirm
    ) {
      return;
    }
    void confirmAndStart(flushEditor());
  }, [
    canAttemptConfirm,
    confirmAndStart,
    flushEditor,
    isBusy,
    isRejected,
    state.dirty,
    unresolvedChecks.length,
  ]);

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

  return (
    <section
      data-testid="content-check-panel"
      aria-labelledby="content-check-title"
      className="flex min-h-[24rem] w-full flex-col overflow-hidden rounded-[10px] bg-surface/40 ring-1 ring-hairline/35 motion-safe:animate-in motion-safe:fade-in-0 motion-safe:slide-in-from-bottom-2 motion-safe:duration-200 motion-reduce:animate-none lg:min-h-[32rem]"
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
        <p className="mt-2 max-w-[42rem] font-sans text-[0.8rem] leading-6 text-muted-foreground">
          这段内容里有系统无法完全确定的格式或结构，直接透读可能影响批注质量，
          所以先请你过目。下面的预览就是最终用于阅读的正文，可以直接修改；
          修改会自动保存并重新检查。确认后 Claread 会冻结这份正文并生成阅读视图，
          进入透读。
        </p>
        <p className="mt-2 font-sans text-[0.74rem] font-medium text-subtle">
          {filename?.trim() ? `来源文件：${filename.trim()}` : "来源：粘贴文本"}
        </p>
      </header>

      {state.phase === "conflict" ? (
        <div
          data-testid="content-check-conflict"
          role="alert"
          className="mx-5 mt-4 shrink-0 rounded-[10px] border border-feedback-warning/40 bg-feedback-warning-soft px-4 py-3 font-sans sm:mx-8"
        >
          <p className="text-[0.8rem] font-semibold text-feedback-warning">
            {state.errorMessage ?? "草稿已被其他更新抢先保存。"}
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={() => void handleReloadLatest()}
            >
              载入最新版本（放弃我的修改）
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => void retryWithLatestRevision()}
            >
              以我的修改重试
            </Button>
          </div>
        </div>
      ) : null}

      <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-5 py-4 sm:px-8 xl:grid xl:grid-cols-[minmax(0,1fr)_17.5rem] xl:content-start xl:gap-6">
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
              className="h-full min-h-[18rem] overflow-y-auto rounded-[8px] border border-hairline/60 bg-surface/38 px-6 py-5 font-reading text-[1.08rem] leading-[1.9] text-ink selection:bg-lens-blue/15 selection:text-ink sm:px-8 sm:text-[1.14rem]"
            />
          ) : null}
        </div>

        <aside
          data-testid="content-check-summary-rail"
          className="order-1 flex flex-col gap-4 xl:order-2 xl:col-start-2 xl:row-start-1"
        >
        {draft && draft.adaptationNotice.length > 0 ? (
          <AdaptationNoticeRail items={draft.adaptationNotice} />
        ) : null}

        {unresolvedChecks.length > 0 ? (
          <div
            data-testid="content-check-risk-list"
            className="space-y-3 font-sans"
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-[0.78rem] font-semibold text-ink">
                {unresolvedChecks.length} 处需要你决定
              </p>
              <button
                type="button"
                data-testid="content-check-keep-all-plain"
                onClick={() =>
                  resolveAllCheckCodes(checkEntries.map((entry) => entry.key))
                }
                className="focus-ring text-[0.76rem] font-medium text-muted-foreground underline-offset-4 transition-colors hover:text-ink hover:underline"
              >
                全部按普通文字继续
              </button>
            </div>
            {unresolvedChecks.map(({ item, key }, index) => {
              const guidance = guidanceForContentCheckCode(item.code);
              const excerpt = draft
                ? locateContentCheckExcerpt(item.code, workingMarkdown)
                : null;
              return (
                <article
                  key={`${item.code}-${index}`}
                  data-testid="content-check-risk-item"
                  data-code={item.code}
                  className="rounded-[10px] border border-feedback-warning/36 bg-surface/60 px-4 py-3"
                >
                  <p className="flex items-center gap-2 text-[0.8rem] font-semibold text-ink">
                    <AlertTriangle
                      aria-hidden
                      className="h-3.5 w-3.5 text-feedback-warning"
                    />
                    {guidance.title}
                  </p>
                  {item.message ? (
                    <p className="mt-1 text-[0.76rem] leading-5 text-muted-foreground">
                      {item.message}
                    </p>
                  ) : null}
                  {excerpt ? (
                    <pre className="mt-2 overflow-x-auto whitespace-pre-wrap rounded-[8px] border border-hairline/60 bg-surface/54 px-3 py-2 font-mono text-[0.72rem] leading-5 text-ink/78">
                      {excerpt}
                    </pre>
                  ) : null}
                  <p className="mt-2 text-[0.76rem] leading-5 text-ink/82">
                    建议：{guidance.suggestion}
                  </p>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {guidance.hasAutoFix ? (
                      <Button
                        type="button"
                        variant="secondary"
                        size="sm"
                        onClick={() => handleAutoFix(item.code, key)}
                      >
                        <Wrench aria-hidden className="mr-1 h-3 w-3" />
                        采用建议
                      </Button>
                    ) : null}
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      onClick={() => resolveCheckCode(key)}
                    >
                      保留普通文字
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      onClick={() => editorRef.current?.focus()}
                    >
                      自行修改
                    </Button>
                  </div>
                </article>
              );
            })}
          </div>
        ) : null}

        {resolvedCount > 0 ? (
          <p
            data-testid="content-check-resolved-summary"
            className="font-sans text-[0.74rem] font-medium text-subtle"
          >
            已按普通文字处理 {resolvedCount} 项。
          </p>
        ) : null}

        {isRejected ? (
          <div
            data-testid="content-check-rejected"
            role="alert"
            className="rounded-[10px] border border-feedback-warning/40 bg-feedback-warning-soft px-4 py-3 font-sans"
          >
            <p className="text-[0.8rem] font-semibold text-feedback-warning">
              当前内容无法生成阅读版本
            </p>
            <ul className="mt-1.5 list-disc space-y-0.5 pl-4 text-[0.76rem] leading-5 text-ink/78">
              {(draft ? readRejectedReasons(draft.quality, draft.contentCheck) : [])
                .slice(0, 3)
                .map((reason) => <li key={reason}>{reason}</li>)}
            </ul>
          </div>
        ) : null}
        </aside>
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
                      ? `已保存 · 第 ${draft.revision} 版`
                      : ""}
          </p>
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={isBusy}
              onClick={() =>
                onDefer({
                  recordId,
                  candidateDocumentId: draft?.candidate?.candidate_document_id ?? null,
                  canonicalTextPreview:
                    draft?.candidate?.canonical_text_preview ?? null,
                })
              }
            >
              稍后处理
            </Button>
            {origin === "submit" ? (
              <Button
                type="button"
                variant="secondary"
                size="sm"
                disabled={isBusy}
                onClick={() => onBackToInput(flushEditor())}
              >
                返回修改
              </Button>
            ) : null}
            {state.errorMessage && state.dirty && state.phase === "ready" ? (
              <Button
                type="button"
                variant="secondary"
                size="sm"
                disabled={isBusy}
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
                unresolvedChecks.length > 0 ||
                !canAttemptConfirm
              }
              onClick={handleConfirm}
            >
              {primaryLabel}
              {state.phase !== "confirming" ? (
                <ArrowRight aria-hidden className="ml-1 h-3.5 w-3.5" />
              ) : null}
            </Button>
          </div>
        </div>
      </footer>
    </section>
  );
}
