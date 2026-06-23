"use client";

import { useMemo, useState } from "react";
import {
  BookOpen,
  Eye,
  Highlighter,
  MessageSquare,
  Search,
  SlidersHorizontal,
  Sparkles,
} from "lucide-react";

import { readerCommandControl } from "@/components/reader/interaction";
import { ImmersiveReaderSurface } from "@/components/reader/plate/ImmersiveReaderSurface";
import { IntensiveReaderSurface } from "@/components/reader/plate/IntensiveReaderSurface";
import {
  defaultReaderSettings,
  modeShowsTranslation,
  modeVisibility,
  readerModeTypography,
  readerThemeClassName,
  ReaderSettingsPanel,
  type ReaderSettingsState,
} from "@/components/reader/settings";
import type { ThemeName } from "@/lib/appearance";
import { cn } from "@/lib/cn";
import {
  adaptReaderPlateSnapshotToPlateDocument,
  adaptReaderPlateSnapshotToReaderVm,
} from "@/lib/reader-plate/projection";
import type {
  ReaderPlateSnapshotDto,
  ReadingRecordProductState,
  ReadingRecordReadinessState,
} from "@/types/api/reader-plate";

interface ReaderRecordWorkbenchSurfaceProps {
  snapshot: ReaderPlateSnapshotDto;
}

function formatDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "今日";
  }
  return `${date.getFullYear()}年${date.getMonth() + 1}月${date.getDate()}日`;
}

function sourceTypeLabel(sourceType: string) {
  if (sourceType === "plain_text" || sourceType === "text") {
    return "粘贴导入";
  }
  if (sourceType === "url") {
    return "网页导入";
  }
  return sourceType || "Reading Record";
}

function productStateLabel(productState: ReadingRecordProductState) {
  switch (productState) {
    case "processing":
      return "处理中";
    case "needs_confirmation":
      return "待确认";
    case "readable_enhancing":
      return "可读增强中";
    case "action_required":
      return "需要处理";
    case "failed":
      return "处理失败";
    case "deleted":
      return "已删除";
    default:
      return "只读快照";
  }
}

function readinessStateLabel(readinessState: ReadingRecordReadinessState) {
  switch (readinessState) {
    case "submitted":
      return "已提交";
    case "candidate_base_ready":
      return "候选底稿已就绪";
    case "article_ready":
      return "正文可读";
    case "initial_enhancement_ready":
      return "初始增强已就绪";
    case "coverage_complete":
      return "增强覆盖完成";
    default:
      return readinessState;
  }
}

function productStateBanner(productState: ReadingRecordProductState) {
  switch (productState) {
    case "processing":
      return {
        title: "处理中",
        body: "阅读记录已创建，系统正在准备增强内容；正文仍可继续阅读。",
        className: "border-lens-blue/20 bg-lens-blue-soft text-ink-soft",
      };
    case "readable_enhancing":
      return {
        title: "可读增强中",
        body: "正文已经可读，系统仍在补充译文、标注或其他增强内容。",
        className: "border-lens-blue/20 bg-lens-blue-soft text-ink-soft",
      };
    case "failed":
      return {
        title: "增强失败",
        body: "本次增强未成功完成，但正文和已发布内容仍可继续阅读。",
        className: "border-amber-300/70 bg-amber-50/95 text-amber-950",
      };
    case "action_required":
      return {
        title: "需要处理",
        body: "此阅读记录需要额外处理后才能继续增强；本轮页面暂不提供处理动作。",
        className: "border-orange-300/80 bg-orange-50/95 text-orange-950",
      };
    default:
      return null;
  }
}

export function ReaderRecordWorkbenchSurface({
  snapshot,
}: ReaderRecordWorkbenchSurfaceProps) {
  const readerVm = useMemo(
    () => adaptReaderPlateSnapshotToReaderVm(snapshot),
    [snapshot],
  );
  const plateDocument = useMemo(
    () => adaptReaderPlateSnapshotToPlateDocument(snapshot),
    [snapshot],
  );
  const [readerSettings, setReaderSettings] =
    useState<ReaderSettingsState>(defaultReaderSettings);
  const [themeName, setThemeName] = useState<ThemeName>("paper");
  const [settingsPanelOpen, setSettingsPanelOpen] = useState(false);
  const [expandedAnalysisEntryIds, setExpandedAnalysisEntryIds] = useState<
    string[]
  >([]);
  const [activeEntryId, setActiveEntryId] = useState<string | null>(null);

  const isImmersiveMode = readerSettings.mode === "immersive";
  const typography = readerModeTypography(readerSettings);
  const canvasThemeClass = readerThemeClassName(themeName);
  const showTranslation = modeShowsTranslation(readerSettings.mode);
  const contentVisibility = modeVisibility(readerSettings.mode);
  const sentenceCount = readerVm.article.sentences.length;
  const formattedDate = formatDate(snapshot.record.created_at);
  const readinessLabel = readinessStateLabel(snapshot.record.readiness_state);
  const statusBanner = productStateBanner(snapshot.record.product_state);
  const shellModeClass = isImmersiveMode
    ? "reader-shell--immersive"
    : "reader-shell--intensive";

  function updateReaderSettings(next: ReaderSettingsState) {
    setReaderSettings(next);
  }

  function toggleAnalysisEntry(entryId: string) {
    setExpandedAnalysisEntryIds((current) =>
      current.includes(entryId)
        ? current.filter((id) => id !== entryId)
        : [...current, entryId],
    );
    setActiveEntryId(entryId);
  }

  function setAnalysisEntryFocus(entryId: string, focused: boolean) {
    setActiveEntryId((current) => {
      if (focused) {
        return entryId;
      }
      return current === entryId ? null : current;
    });
  }

  return (
    <main
      className="paper-grain reader-shell-page min-h-screen px-3 pb-24 pt-3 text-ink sm:px-4 md:pb-6 lg:px-5"
      data-reader-record-workbench="true"
      data-testid="reader-record-workbench-surface"
    >
      <article
        className={cn(
          "reader-shell min-w-0 overflow-visible rounded-panel border border-hairline shadow-surface-quiet",
          shellModeClass,
          canvasThemeClass,
        )}
      >
        <header className="reader-header-band reader-header-band--immersive reader-header-band--clean sticky top-3 z-20 border-b-0 bg-background/88 px-5 py-6 shadow-none backdrop-blur transition-[padding,background-color,border-color,box-shadow,transform] sm:px-8 lg:px-10 lg:py-8">
          <div className="reader-header-band-inner mx-auto flex w-full max-w-[82ch] flex-col gap-6 lg:gap-8">
            <div className="flex items-center gap-1.5 text-[0.8rem] font-semibold leading-none tracking-wide">
              <span className="text-lens-blue">
                {isImmersiveMode ? "沉浸阅读" : "精读模式"}
              </span>
              <span className="text-muted/60">·</span>
              <span className="font-medium text-muted">{formattedDate}</span>
              <span className="text-muted/60">·</span>
              <span className="font-medium text-muted">Reading Record</span>
            </div>

            <div className="min-w-0">
              <h1 className="font-headline text-[clamp(2rem,4vw,3.25rem)] font-bold leading-[1.08] tracking-tight text-ink">
                {snapshot.record.title}
              </h1>
              <p className="mt-4 max-w-[72ch] font-sans text-[0.95rem] font-medium leading-[1.68] tracking-wide text-muted">
                正文、译文和标注来自当前阅读快照；当前为只读预览。
              </p>
            </div>

            <div className="flex min-h-[56px] w-full flex-col items-stretch justify-between border-y border-hairline bg-transparent py-0 sm:flex-row">
              <div className="flex flex-wrap items-center gap-3.5 py-3 sm:py-0">
                <span className="flex select-none items-center gap-1.5 rounded-[0.5rem] border border-hairline/80 bg-surface-warm px-3 py-1 text-[0.75rem] font-semibold text-ink-soft shadow-[inset_0_1px_0_rgba(255,255,255,0.8),0_1px_2px_rgba(0,0,0,0.03)]">
                  <Sparkles className="h-3.5 w-3.5 fill-vocab-amber/10 text-vocab-amber" />
                  <span>{sourceTypeLabel(snapshot.record.source_type)}</span>
                </span>
                <div className="h-3.5 w-px bg-hairline" />
                <span className="text-[0.8rem] font-semibold text-muted">
                  {sentenceCount} 句
                </span>
                <div className="h-3.5 w-px bg-hairline" />
                <span className="text-[0.8rem] font-semibold text-muted">
                  {productStateLabel(snapshot.record.product_state)}
                </span>
                <div className="h-3.5 w-px bg-hairline" />
                <span
                  className="text-[0.8rem] font-semibold text-muted"
                  data-testid="reader-record-readiness-chip"
                >
                  {readinessLabel}
                </span>
              </div>

              <div className="flex select-none items-stretch divide-x divide-hairline border-t border-hairline sm:border-t-0">
                <button
                  type="button"
                  onClick={() =>
                    updateReaderSettings({ ...readerSettings, mode: "intensive" })
                  }
                  className={cn(
                    readerCommandControl,
                    "relative flex flex-1 justify-center rounded-none px-3.5 py-2.5 text-left sm:py-3.5 md:px-5",
                    readerSettings.mode === "intensive"
                      ? "text-vocab-amber after:absolute after:bottom-0 after:left-0 after:right-0 after:h-[2px] after:bg-vocab-amber"
                      : "text-ink hover:text-ink-soft",
                  )}
                >
                  <BookOpen
                    aria-hidden="true"
                    className={cn(
                      "h-[18px] w-[18px] shrink-0",
                      readerSettings.mode === "intensive"
                        ? "text-vocab-amber"
                        : "text-muted",
                    )}
                    strokeWidth={1.5}
                  />
                  <span className="flex min-w-0 flex-col items-start whitespace-nowrap leading-none">
                    <span className="text-[0.85rem] font-semibold">精读</span>
                    <span className="mt-1 hidden text-[0.65rem] font-medium text-subtle sm:block">
                      逐句研读
                    </span>
                  </span>
                </button>

                <button
                  type="button"
                  onClick={() =>
                    updateReaderSettings({ ...readerSettings, mode: "immersive" })
                  }
                  className={cn(
                    readerCommandControl,
                    "relative flex flex-1 justify-center rounded-none px-3.5 py-2.5 text-left sm:py-3.5 md:px-5",
                    readerSettings.mode === "immersive"
                      ? "text-vocab-amber after:absolute after:bottom-0 after:left-0 after:right-0 after:h-[2px] after:bg-vocab-amber"
                      : "text-ink hover:text-ink-soft",
                  )}
                >
                  <Eye
                    aria-hidden="true"
                    className={cn(
                      "h-[18px] w-[18px] shrink-0",
                      readerSettings.mode === "immersive"
                        ? "text-vocab-amber"
                        : "text-muted",
                    )}
                    strokeWidth={1.5}
                  />
                  <span className="flex min-w-0 flex-col items-start whitespace-nowrap leading-none">
                    <span className="text-[0.85rem] font-semibold">沉浸</span>
                    <span className="mt-1 hidden text-[0.65rem] font-medium text-subtle sm:block">
                      专注阅读
                    </span>
                  </span>
                </button>

                <button
                  type="button"
                  aria-expanded={settingsPanelOpen}
                  onClick={() => setSettingsPanelOpen((current) => !current)}
                  className={cn(
                    readerCommandControl,
                    "relative flex flex-1 justify-center rounded-none px-3.5 py-2.5 text-left sm:py-3.5 md:px-5",
                    settingsPanelOpen
                      ? "text-vocab-amber after:absolute after:bottom-0 after:left-0 after:right-0 after:h-[2px] after:bg-vocab-amber"
                      : "text-ink hover:text-ink-soft",
                  )}
                >
                  <SlidersHorizontal
                    aria-hidden="true"
                    className={cn(
                      "h-[18px] w-[18px] shrink-0",
                      settingsPanelOpen ? "text-vocab-amber" : "text-muted",
                    )}
                    strokeWidth={1.5}
                  />
                  <span className="flex min-w-0 flex-col items-start whitespace-nowrap leading-none">
                    <span className="text-[0.85rem] font-semibold">阅读设置</span>
                    <span className="mt-1 hidden text-[0.65rem] font-medium text-subtle sm:block">
                      版式与偏好
                    </span>
                  </span>
                </button>
              </div>
            </div>

            <div className="flex flex-col gap-3 text-[0.78rem] leading-normal tracking-wide text-muted sm:flex-row sm:items-center sm:justify-between sm:gap-0 sm:leading-none">
              <div className="flex flex-wrap items-center gap-1.5 font-medium">
                <span>快照 {snapshot.snapshot_id}</span>
                <span className="text-muted/60">·</span>
                <span>事件序列 {snapshot.last_event_sequence}</span>
                <span className="text-muted/60">·</span>
                <span>只读预览</span>
              </div>

              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  disabled
                  title="新 Reading Record 的 Ask persistence 尚未接通"
                  className={cn(readerCommandControl, "h-8 rounded-md px-2.5")}
                  data-reader-record-disabled="ask"
                >
                  <MessageSquare aria-hidden="true" className="h-3.5 w-3.5" />
                  Ask Claread
                </button>
                <button
                  type="button"
                  disabled
                  title="新 Reading Record 的笔记和高亮持久化尚未接通"
                  className={cn(readerCommandControl, "h-8 rounded-md px-2.5")}
                  data-reader-record-disabled="notes-highlights"
                >
                  <Highlighter aria-hidden="true" className="h-3.5 w-3.5" />
                  笔记/高亮
                </button>
                <button
                  type="button"
                  disabled
                  title="新 Reading Record 的词典写入和用户资产保存尚未接通"
                  className={cn(readerCommandControl, "h-8 rounded-md px-2.5")}
                  data-reader-record-disabled="dictionary-assets"
                >
                  <Search aria-hidden="true" className="h-3.5 w-3.5" />
                  词典保存
                </button>
              </div>
            </div>

            {settingsPanelOpen ? (
              <div className="mx-auto w-full max-w-[82ch]">
                <ReaderSettingsPanel
                  themeName={themeName}
                  value={readerSettings}
                  onChange={updateReaderSettings}
                  onThemeChange={setThemeName}
                  onClose={() => setSettingsPanelOpen(false)}
                />
              </div>
            ) : null}

            {statusBanner ? (
              <div
                className={cn(
                  "mx-auto mt-1 rounded-[10px] border px-4 py-3 text-sm leading-6",
                  statusBanner.className,
                )}
                data-testid="reader-record-status-banner"
              >
                <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
                  <div>
                    <p className="text-[0.72rem] font-semibold uppercase tracking-[0.14em] opacity-70">
                      记录状态
                    </p>
                    <p className="mt-1 font-semibold">{statusBanner.title}</p>
                    <p>{statusBanner.body}</p>
                  </div>
                  <p
                    className="text-[0.78rem] font-medium opacity-80"
                    data-testid="reader-record-readiness-state"
                  >
                    当前阶段：{readinessLabel}
                  </p>
                </div>
              </div>
            ) : (
              <p
                className="mx-auto mt-1 text-[0.78rem] font-medium tracking-wide text-muted"
                data-testid="reader-record-readiness-state"
              >
                当前阶段：{readinessLabel}
              </p>
            )}

            <div className="reader-shell-message mx-auto mt-1 rounded-[10px] border border-lens-blue/20 bg-lens-blue-soft px-4 py-3 text-sm leading-6 text-ink-soft">
              当前只读预览中，Ask、笔记、高亮和词典写入暂不可用。
            </div>
          </div>
        </header>

        <div
          className={cn(
            "reader-reading-stage",
            isImmersiveMode
              ? "reader-reading-stage--immersive"
              : "reader-reading-stage--intensive",
          )}
        >
          {isImmersiveMode ? (
            <ImmersiveReaderSurface
              document={plateDocument}
              readingClassName={typography.bodyClassName}
              columnClassName={typography.columnClassName}
              paragraphDensityClassName={typography.paragraphDensityClassName}
              themeClassName={canvasThemeClass}
            />
          ) : (
            <IntensiveReaderSurface
              document={plateDocument}
              showTranslation={showTranslation}
              readingClassName={typography.bodyClassName}
              translationClassName={typography.translationClassName}
              columnClassName={typography.columnClassName}
              paragraphDensityClassName={typography.paragraphDensityClassName}
              themeClassName={canvasThemeClass}
              annotationVisibilityGroups={contentVisibility}
              activeAnalysisEntryId={activeEntryId}
              expandedAnalysisEntryIds={expandedAnalysisEntryIds}
              onAnalysisFocusChange={setAnalysisEntryFocus}
              onAnalysisToggle={toggleAnalysisEntry}
            />
          )}
        </div>
      </article>
    </main>
  );
}
