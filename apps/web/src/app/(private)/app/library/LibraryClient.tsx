"use client";

import {
  AlertTriangle,
  ArrowRight,
  BookMarked,
  Calendar,
  CheckCircle2,
  CircleDashed,
  FileText,
  LoaderCircle,
  NotebookPen,
  Plus,
  Search,
} from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import { ApertureWatermark } from "@/components/brand/BrandMarks";
import { Button } from "@/components/primitives/button";
import { appReadRoute, appReaderRoute } from "@/lib/routes";
import type { RecordsBffStatus } from "@/services/bff/records";
import type { RecordListItemVm } from "@/types/view/RecordListItemVm";
import { DeleteRecordButton } from "./DeleteRecordButton";
import { LibraryFavoriteButton } from "./LibraryFavoriteButton";

const statusTitle: Record<RecordsBffStatus, string> = {
  ready: "还没有阅读记录",
  unauthenticated: "会话已过期",
  limited_debug: "调试态受限",
  upstream_unavailable: "历史记录服务不可用",
  upstream_error: "读取历史记录失败",
};

const readingGoalLabel: Record<string, string> = {
  daily_reading: "日常阅读",
  academic: "学术摘要",
  exam: "备考精读",
};

const readingVariantLabel: Record<string, string> = {
  beginner_reading: "入门",
  intermediate_reading: "中级",
  intensive_reading: "精读",
  academic_general: "学术通用",
  gaokao: "高考",
  cet: "四六级",
  kaoyan: "考研",
  tem: "专四专八",
  ielts_toefl: "雅思托福",
};

const sourceTypeLabel: Record<string, string> = {
  user_input: "手动粘贴",
  daily_article: "每日文章",
  imported: "导入",
  ocr: "OCR",
};

function formatDate(value: string): string {
  return new Date(value).toLocaleDateString("zh-CN");
}

function normalize(value: string) {
  return value.trim().toLowerCase();
}

function summarizeSourceExcerpt(record: RecordListItemVm) {
  const excerpt = record.sourceTextExcerpt.trim();
  if (excerpt) {
    return excerpt;
  }

  const firstLine = record.sourceText
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find(Boolean);

  if (!firstLine) {
    return "暂无原文片段";
  }

  return firstLine.length > 140 ? `${firstLine.slice(0, 140)}...` : firstLine;
}

function readingGoalName(value: string) {
  return readingGoalLabel[value] ?? "透读文章";
}

function readingVariantName(value: string) {
  return readingVariantLabel[value] ?? value;
}

function sourceTypeName(value: string) {
  return sourceTypeLabel[value] ?? "外部来源";
}

function recordTimeLabel(record: RecordListItemVm) {
  if (record.lastOpenedAt) {
    return `最近阅读 ${formatDate(record.lastOpenedAt)}`;
  }

  return `创建于 ${formatDate(record.createdAt)}`;
}

function statusMeta(status: string) {
  if (status === "ready") {
    return {
      label: "已完成",
      icon: CheckCircle2,
      className: "text-ink-soft/90",
      iconClassName: "text-emerald-700/80",
    };
  }

  if (status === "failed") {
    return {
      label: "解析失败",
      icon: AlertTriangle,
      className: "text-rose-800/90",
      iconClassName: "text-rose-700/90",
    };
  }

  if (status === "queued" || status === "running" || status === "finalizing") {
    return {
      label: "处理中",
      icon: LoaderCircle,
      className: "text-ink-soft/90",
      iconClassName: "text-lens-blue/80",
    };
  }

  return {
    label: "处理中",
    icon: CircleDashed,
    className: "text-muted",
    iconClassName: "text-muted",
  };
}

function renderArchiveEmptyState({
  hasQuery,
  title,
  description,
  onReset,
}: {
  hasQuery: boolean;
  title: string;
  description: string;
  onReset?: () => void;
}) {
  return (
    <section className="relative mt-10 border-t border-hairline pt-16">
      <ApertureWatermark
        size={220}
        className="absolute bottom-[-4.5rem] right-[-1.5rem] opacity-[0.06] saturate-0"
      />
      <div className="relative max-w-[42rem]">
        <div className="mb-5 flex items-center gap-4">
          <span className="h-px w-10 bg-hairline" />
          <p className="text-[0.68rem] font-bold uppercase tracking-[0.18em] text-lens-blue">
            {hasQuery ? "Archive Filters" : "Archive Awaits"}
          </p>
        </div>
        <h2 className="font-headline text-[2rem] font-semibold leading-[1.08] tracking-tight text-ink sm:text-[2.35rem]">
          {title}
        </h2>
        <p className="mt-4 max-w-[38ch] font-reading text-[1.02rem] leading-[1.8] text-ink-soft">
          {description}
        </p>
        <div className="mt-8 flex flex-wrap items-center gap-3">
          {hasQuery ? (
            <Button variant="outline" onClick={onReset}>
              查看全部记录
            </Button>
          ) : (
            <Button asChild variant="primary" className="rounded-pill">
              <Link href={appReadRoute}>
                <Plus aria-hidden="true" className="h-4 w-4" />
                开始一篇新解读
              </Link>
            </Button>
          )}
        </div>
        {!hasQuery ? (
          <div className="mt-12 grid gap-5 border-t border-hairline/80 pt-6 text-[0.8rem] leading-6 text-muted sm:grid-cols-3">
            <div>
              <p className="text-[0.68rem] font-bold uppercase tracking-[0.14em] text-ink">标题与片段</p>
              <p className="mt-2">每篇文章都会留下标题、原文片段和最近阅读时间，方便回找。</p>
            </div>
            <div>
              <p className="text-[0.68rem] font-bold uppercase tracking-[0.14em] text-ink">笔记与生词</p>
              <p className="mt-2">你留下的笔记和生词，会在这里形成真正可回看的阅读痕迹。</p>
            </div>
            <div>
              <p className="text-[0.68rem] font-bold uppercase tracking-[0.14em] text-ink">按目标重读</p>
              <p className="mt-2">日常阅读、备考精读与学术阅读，会在页边书签里成为快速索引。</p>
            </div>
          </div>
        ) : null}
      </div>
    </section>
  );
}

export function LibraryClient({
  records,
  status,
  message,
  total,
  noteCount,
  vocabularyCount,
}: {
  records: RecordListItemVm[];
  status: RecordsBffStatus;
  message?: string;
  total: number;
  noteCount: number;
  vocabularyCount: number;
}) {
  const [query, setQuery] = useState("");
  const [goalFilter, setGoalFilter] = useState<string>("all");

  const normalizedQuery = normalize(query);

  const filteredRecords = useMemo(() => {
    let result = records;

    if (goalFilter !== "all") {
      result = result.filter((r) => r.readingGoal === goalFilter);
    }

    if (normalizedQuery) {
      result = result.filter((record) => {
        const haystack = `${record.title}\n${record.sourceText}`.toLowerCase();
        return haystack.includes(normalizedQuery);
      });
    }

    return result;
  }, [normalizedQuery, goalFilter, records]);

  const goalCounts = useMemo(() => {
    const counts = {
      all: records.length,
      daily_reading: 0,
      exam: 0,
      academic: 0,
    };

    for (const record of records) {
      if (record.readingGoal === "daily_reading") {
        counts.daily_reading += 1;
      } else if (record.readingGoal === "exam") {
        counts.exam += 1;
      } else if (record.readingGoal === "academic") {
        counts.academic += 1;
      }
    }

    return counts;
  }, [records]);

  const hasQuery = normalizedQuery.length > 0 || goalFilter !== "all";

  const recentRecord = useMemo(() => {
    if (filteredRecords.length === 0) {
      return null;
    }

    return [...filteredRecords].sort((a, b) => {
      const dateA = new Date(a.lastOpenedAt || a.createdAt).getTime();
      const dateB = new Date(b.lastOpenedAt || b.createdAt).getTime();
      return dateB - dateA;
    })[0];
  }, [filteredRecords]);

  const lastReadDateStr = recentRecord
    ? new Date(recentRecord.lastOpenedAt || recentRecord.createdAt).toLocaleDateString("zh-CN", {
        year: "numeric",
        month: "long",
        day: "numeric",
      })
    : null;

  const goalOptions = [
    { value: "all", label: "全部", count: goalCounts.all },
    { value: "daily_reading", label: "日常阅读", count: goalCounts.daily_reading },
    { value: "exam", label: "备考精读", count: goalCounts.exam },
    { value: "academic", label: "学术阅读", count: goalCounts.academic },
  ] as const;

  function resetFilters() {
    setQuery("");
    setGoalFilter("all");
  }

  return (
    <div className="grid min-h-0 flex-1 gap-12 lg:gap-20 xl:gap-28 lg:grid-cols-[minmax(0,1fr)_280px] xl:grid-cols-[minmax(0,1fr)_320px]">
      <div className="flex h-full min-h-0 flex-col space-y-2 lg:py-12">
        {/* Archive Header */}
        <div className="mb-6 shrink-0 flex flex-col sm:flex-row sm:items-end justify-between gap-4 pl-2 border-b border-hairline pb-5">
          <div>
            <div className="mb-2 flex items-center gap-3">
              <p className="text-[0.6rem] font-bold uppercase tracking-[0.2em] text-lens-blue">Library</p>
              <div className="h-[1px] w-8 bg-hairline" />
            </div>
            <h1 className="font-headline text-[2rem] font-semibold leading-[1] tracking-tight text-ink md:text-[2.5rem] lg:text-[3rem]">
              Reading Archive.
            </h1>
          </div>
          <div className="pb-1">
            <Button asChild variant="primary-ink" className="group px-6 py-3 font-sans text-[0.82rem] font-semibold tracking-[0.08em] transition-all duration-300 border-transparent min-w-[130px]">
              <Link href={appReadRoute} className="flex items-center justify-center">
                <Plus aria-hidden="true" className="mr-2 h-4 w-4 transition-transform duration-300 group-hover:rotate-90" />
                新解读
              </Link>
            </Button>
          </div>
        </div>

        <div className="mb-8 shrink-0 flex items-center justify-between pb-2 pl-2">
          <div className="flex w-full max-w-sm items-center gap-3">
            <Search className="h-4 w-4 text-muted" />
            <input
              type="text"
              aria-label="搜索标题或原文片段"
              placeholder="搜索标题或原文片段..."
              className="w-full bg-transparent text-[0.95rem] text-ink outline-none placeholder:text-muted"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>
          <p className="text-[0.72rem] font-semibold tracking-[0.08em] text-ink">
            共 {filteredRecords.length} 篇记录
          </p>
        </div>

        {filteredRecords.length > 0 ? (
          <div className="min-h-0 flex-1 overflow-y-auto scrollbar-hide">
            <section className="pr-5">
              {filteredRecords.map((record) => {
                const statusDisplay = statusMeta(record.analysisStatus);
                const StatusIcon = statusDisplay.icon;

                return (
                  <div
                    key={record.id}
                    className="group relative flex items-stretch justify-between gap-8 border-b border-hairline/40 py-7 first:pt-3 transition-all duration-300"
                  >
                    <div className="relative z-10 min-w-0 flex-1">
                      {/* Editorial Breadcrumb Tagline */}
                      <div className="mb-2.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[0.66rem] font-semibold tracking-[0.1em] text-muted">
                        <span className={`${statusDisplay.className} inline-flex items-center gap-1.5 text-[0.66rem] font-bold tracking-[0.05em]`}>
                          <StatusIcon
                            className={`h-3.5 w-3.5 ${statusDisplay.iconClassName} ${
                              record.analysisStatus === "queued" ||
                              record.analysisStatus === "running" ||
                              record.analysisStatus === "finalizing"
                                ? "animate-spin"
                                : ""
                            }`}
                          />
                          {statusDisplay.label}
                        </span>
                        <span className="text-muted/30 select-none">·</span>
                        <span className="text-ink-soft/90">{readingGoalName(record.readingGoal)}</span>
                        <span className="text-muted/30 select-none">·</span>
                        <span className="text-ink-soft/90">{readingVariantName(record.readingVariant)}</span>
                        <span className="text-muted/30 select-none">·</span>
                        <span className="text-ink-soft/80">{sourceTypeName(record.sourceType)}</span>
                      </div>

                      {/* Article Title Linked to Reader */}
                      <Link
                        href={appReaderRoute(record.id)}
                        className="focus-ring inline-block rounded-md outline-offset-4"
                      >
                        <h2 className="font-headline text-[1.58rem] font-bold leading-[1.28] tracking-tight text-ink transition-colors group-hover:text-lens-blue">
                          {record.title}
                        </h2>
                      </Link>

                      {/* Excerpt with Reader Serif Stack */}
                      <p className="mt-3 line-clamp-2 max-w-3xl font-reading text-[1rem] leading-[1.7] text-muted/95">
                        {summarizeSourceExcerpt(record)}
                      </p>

                      {/* Dynamic Semantic Stats Row (Printed Editorial Style) */}
                      <div className="mt-4 flex flex-wrap items-center gap-x-2.5 gap-y-1.5 text-[0.72rem] font-medium tracking-[0.05em] text-muted">
                        <span className="flex items-center gap-1 text-muted/75">
                          <Calendar className="h-3.5 w-3.5 opacity-60" />
                          {recordTimeLabel(record)}
                        </span>
                        <span className="text-muted/30 select-none">·</span>
                        
                        <span className="flex items-center gap-1 text-muted/75">
                          <FileText className="h-3.5 w-3.5 opacity-60" />
                          {record.wordCount} 词
                        </span>
                        <span className="text-muted/30 select-none">·</span>
                        
                        {record.noteCount > 0 ? (
                          <span className="flex items-center gap-1 text-vocab-amber font-semibold transition-colors hover:text-vocab-amber/90">
                            <NotebookPen className="h-3.5 w-3.5" />
                            {record.noteCount} 笔记
                          </span>
                        ) : (
                          <span className="flex items-center gap-1 text-muted/50">
                            <NotebookPen className="h-3.5 w-3.5 opacity-40" />
                            0 笔记
                          </span>
                        )}
                        <span className="text-muted/30 select-none">·</span>
                        
                        {record.vocabularyCount > 0 ? (
                          <span className="flex items-center gap-1 text-grammar-violet font-semibold transition-colors hover:text-grammar-violet/90">
                            <BookMarked className="h-3.5 w-3.5" />
                            {record.vocabularyCount} 生词
                          </span>
                        ) : (
                          <span className="flex items-center gap-1 text-muted/50">
                            <BookMarked className="h-3.5 w-3.5 opacity-40" />
                            0 生词
                          </span>
                        )}
                      </div>
                    </div>

                    {/* Integrated Tactile Actions Panel */}
                    <div className="relative z-10 flex shrink-0 flex-col items-end justify-between self-stretch py-0.5 min-w-[40px]">
                      {/* Top right: Favorite toggle */}
                      <LibraryFavoriteButton
                        recordId={record.id}
                        initialFavorited={record.isFavorited}
                        compact
                      />
                      
                      {/* Bottom right: Secondary operations appearing on hover for decluttered feed */}
                      <div className="flex items-center gap-2 lg:opacity-0 lg:group-hover:opacity-100 lg:focus-within:opacity-100 transition-opacity duration-300">
                        <DeleteRecordButton recordId={record.id} title={record.title} compact />
                        <Link
                          href={appReaderRoute(record.id)}
                          className="focus-ring group inline-flex items-center justify-center h-8 w-8 rounded-md text-muted transition-all duration-200 hover:text-ink hover:translate-x-[4px] active:translate-x-0 hover:scale-110"
                          title="继续阅读"
                        >
                          <ArrowRight className="h-4.5 w-4.5 transition-all duration-200 stroke-[1.8] group-hover:stroke-[2.3] group-hover:text-ink" />
                        </Link>
                      </div>
                    </div>
                  </div>
                );
              })}
            </section>
          </div>
        ) : (
          renderArchiveEmptyState({
            hasQuery,
            title: hasQuery ? "这一栏暂时没有对应记录。" : statusTitle[status],
            description: hasQuery
              ? "换一个标题、关键词或阅读目标，再把这本归档册翻一页。"
              : message ?? "完成一次真实解析后，标题、笔记、生词和最近阅读都会在这里形成可回看的目录。",
            onReset: hasQuery ? resetFilters : undefined,
          })
        )}
      </div>

      <aside className="relative hidden min-w-0 lg:block">
        <div className="sticky top-8 px-2 pb-16">
          <div className="relative mx-auto w-full max-w-[18.5rem]">
            <div className="pointer-events-none absolute -top-6 right-5 z-30 text-muted/40">
              <svg width="20" height="42" viewBox="0 0 24 48" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <path d="M14 36V12a4 4 0 0 0-8 0v28a6 6 0 0 0 12 0V12a8 8 0 0 0-16 0v24" />
              </svg>
            </div>

            <div className="overflow-hidden rounded-t-[1.45rem] border border-hairline border-b-0 bg-[linear-gradient(180deg,color-mix(in_srgb,var(--surface)_82%,white)_0%,color-mix(in_srgb,var(--reader-paper)_88%,white)_100%)] px-7 pb-8 pt-10 shadow-[0_16px_40px_rgba(28,24,18,0.08)]">
              <div className="mb-6">
                <p className="text-[0.58rem] font-bold uppercase tracking-[0.18em] text-subtle">Claread Archive</p>
                <h2 className="mt-1.5 font-headline text-[1.2rem] font-semibold leading-tight text-ink">我的归档书签</h2>
              </div>

              <div className="space-y-8">
                <section>
                  <p className="font-reading text-[0.98rem] leading-[1.75] text-ink">
                    本册收录了 <span className="font-semibold text-ink">{total}</span> 篇文章，留有 {noteCount} 条笔记与 {vocabularyCount} 个生词。
                  </p>
                  {lastReadDateStr ? (
                    <p className="mt-1.5 font-reading text-[0.9rem] leading-[1.6] text-muted">
                      最近一次翻阅在 {lastReadDateStr}。
                    </p>
                  ) : (
                    <p className="mt-1.5 font-reading text-[0.9rem] leading-[1.6] text-muted">
                      你的归档会在第一次真实解读后留下时间与痕迹。
                    </p>
                  )}
                </section>

                <section className="border-t border-hairline pt-7">
                  <h3 className="mb-4 text-[0.62rem] font-bold uppercase tracking-[0.16em] text-subtle">
                    最近重读
                  </h3>
                  {recentRecord ? (
                    <Link
                      href={appReaderRoute(recentRecord.id)}
                      className="group block rounded-note focus-ring outline-offset-4"
                    >
                      <h4 className="font-headline text-[1.12rem] font-semibold leading-[1.32] text-ink transition-colors group-hover:text-ink-soft">
                        {recentRecord.title}
                      </h4>
                      <p className="mt-2 line-clamp-2 text-[0.79rem] leading-[1.65] text-muted">
                        {summarizeSourceExcerpt(recentRecord)}
                      </p>
                      <div className="mt-3 flex items-center gap-2 text-[0.62rem] font-semibold tracking-[0.08em] text-muted">
                        <span>{readingGoalName(recentRecord.readingGoal)}</span>
                        <span className="h-[3px] w-[3px] rounded-full bg-hairline" />
                        <span className="text-lens-blue">{formatDate(recentRecord.lastOpenedAt || recentRecord.createdAt)}</span>
                      </div>
                    </Link>
                  ) : (
                    <p className="text-[0.8rem] leading-6 text-muted">
                      当前筛选下还没有可回读的记录。
                    </p>
                  )}
                </section>

                <section className="border-t border-hairline pt-7">
                  <div className="mb-4 flex items-center justify-between gap-3">
                    <h3 className="text-[0.62rem] font-bold uppercase tracking-[0.16em] text-subtle">
                      按阅读目标浏览
                    </h3>
                    {goalFilter !== "all" ? (
                      <button
                        type="button"
                        onClick={resetFilters}
                        className="text-[0.62rem] font-semibold tracking-[0.08em] text-lens-blue transition-colors hover:text-ink"
                      >
                        清除
                      </button>
                    ) : null}
                  </div>
                  <div className="space-y-1">
                    {goalOptions.map((option) => {
                      const active = goalFilter === option.value;
                      return (
                        <button
                          key={option.value}
                          type="button"
                          onClick={() => setGoalFilter(option.value)}
                          className={`flex w-full items-center justify-between px-1 py-2 text-left transition-colors outline-none focus-visible:ring-1 focus-visible:ring-lens-blue ${
                            active ? "text-ink" : "text-subtle hover:text-muted"
                          }`}
                        >
                          <span className="inline-flex items-center gap-3">
                            <span
                              className={`h-[3px] w-[3px] rounded-full transition-colors ${
                                active ? "bg-lens-blue" : "bg-hairline"
                              }`}
                            />
                            <span className="text-[0.85rem] font-medium tracking-[0.02em]">
                              {option.label}
                            </span>
                          </span>
                          <span className={`text-[0.7rem] ${active ? "text-muted font-medium" : "text-hairline/80"}`}>
                            {option.count}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                </section>
              </div>
            </div>

            <div
              className="relative -mt-px h-[4.5rem] overflow-hidden border-x border-b border-hairline bg-[linear-gradient(180deg,color-mix(in_srgb,var(--surface)_84%,white)_0%,color-mix(in_srgb,var(--reader-paper)_92%,white)_100%)] shadow-[0_18px_32px_rgba(28,24,18,0.06)]"
              style={{ clipPath: "polygon(0 0, 100% 0, 100% 100%, 66% 100%, 50% 70%, 34% 100%, 0 100%)" }}
            >
              <ApertureWatermark
                size={120}
                className="absolute bottom-[-3.5rem] right-[-2.5rem] opacity-[0.04] saturate-0"
              />
            </div>
          </div>
        </div>
      </aside>
    </div>
  );
}
