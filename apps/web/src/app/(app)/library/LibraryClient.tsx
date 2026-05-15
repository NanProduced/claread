"use client";

import { ArrowRight, Clock3, FileText, Search, X } from "lucide-react";
import type { Route } from "next";
import Link from "next/link";
import { useMemo, useState } from "react";
import type { RecordsBffStatus } from "@/services/bff/records";
import type { RecordListItemVm } from "@/types/view/RecordListItemVm";
import { DeleteRecordButton } from "./DeleteRecordButton";

const readRoute = "/read" as Route;

const goalLabel: Record<string, string> = {
  academic: "学术摘要",
  daily_reading: "日常阅读",
  exam: "备考精读",
};

const statusTitle: Record<RecordsBffStatus, string> = {
  ready: "还没有阅读记录",
  unauthenticated: "会话已过期",
  mock_session: "会话不可用",
  upstream_unavailable: "历史记录服务不可用",
  upstream_error: "读取历史记录失败",
};

function readerRoute(recordId: string): Route {
  return `/reader/${recordId}` as Route;
}

function formatDate(value: string): string {
  return new Date(value).toLocaleDateString("zh-CN");
}

function normalize(value: string) {
  return value.trim().toLowerCase();
}

function excerpt(sourceText: string) {
  const firstLine = sourceText
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find(Boolean);

  if (!firstLine) {
    return "暂无原文片段";
  }

  return firstLine.length > 140 ? `${firstLine.slice(0, 140)}...` : firstLine;
}

function EmptyState({
  status,
  hasQuery,
}: {
  status: RecordsBffStatus;
  hasQuery: boolean;
}) {
  return (
    <section className="border-t border-hairline py-10">
      <div className="max-w-xl">
        <div className="flex h-11 w-11 items-center justify-center rounded-full bg-lens-blue-soft text-lens-blue">
          <FileText aria-hidden="true" className="h-5 w-5" />
        </div>
        <h2 className="mt-4 font-headline text-2xl font-semibold text-ink">
          {hasQuery ? "没有匹配的记录" : statusTitle[status]}
        </h2>
        <p className="mt-3 text-sm leading-6 text-muted">
          {hasQuery
            ? "换一个标题、关键词或原文片段再试。"
            : "完成一次真实解析后，这里会成为你的英文阅读档案。"}
        </p>
        {!hasQuery ? (
          <Link
            href={readRoute}
            className="focus-ring mt-5 inline-flex min-h-10 items-center rounded-pill border border-hairline bg-surface px-4 text-sm font-semibold text-ink transition-colors hover:border-muted"
          >
            解析新文章
          </Link>
        ) : null}
      </div>
    </section>
  );
}

export function LibraryClient({
  records,
  status,
}: {
  records: RecordListItemVm[];
  status: RecordsBffStatus;
}) {
  const [query, setQuery] = useState("");
  const normalizedQuery = normalize(query);
  const filteredRecords = useMemo(() => {
    if (!normalizedQuery) {
      return records;
    }

    return records.filter((record) => {
      const haystack = `${record.title}\n${record.sourceText}`.toLowerCase();
      return haystack.includes(normalizedQuery);
    });
  }, [normalizedQuery, records]);
  const hasQuery = normalizedQuery.length > 0;

  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-3 border-b border-hairline pb-4 md:flex-row md:items-center md:justify-between">
        <label className="focus-within:border-muted flex min-h-11 flex-1 items-center gap-3 rounded-pill border border-hairline bg-surface px-4 transition-colors md:max-w-xl">
          <Search aria-hidden="true" className="h-4 w-4 shrink-0 text-muted" />
          <span className="sr-only">搜索阅读记录</span>
          <input
            className="w-full bg-transparent text-sm text-ink outline-none placeholder:text-subtle"
            placeholder="搜索标题或原文片段"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          {hasQuery ? (
            <button
              type="button"
              className="focus-ring -mr-1 flex h-7 w-7 items-center justify-center rounded-full text-muted transition-colors hover:bg-reader-paper hover:text-ink"
              onClick={() => setQuery("")}
              aria-label="清空搜索"
            >
              <X aria-hidden="true" className="h-4 w-4" />
            </button>
          ) : null}
        </label>
        <p className="text-xs text-muted">
          显示 {filteredRecords.length} / {records.length} 条
        </p>
      </div>

      {filteredRecords.length > 0 ? (
        <section className="divide-y divide-hairline border-y border-hairline">
          {filteredRecords.map((record) => (
            <article
              key={record.id}
              className="group grid gap-4 py-5 transition-colors hover:bg-reader-paper/55 md:grid-cols-[minmax(0,1fr)_auto] md:items-center md:px-3"
            >
              <Link href={readerRoute(record.id)} className="focus-ring min-w-0 rounded-note px-1 py-1">
                <h2 className="line-clamp-2 font-headline text-[1.35rem] font-semibold leading-snug text-ink">
                  {record.title}
                </h2>
                <p className="mt-2 line-clamp-2 max-w-3xl text-sm leading-6 text-muted">
                  {excerpt(record.sourceText)}
                </p>
                <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-muted">
                  <span className="inline-flex items-center gap-1">
                    <Clock3 aria-hidden="true" className="h-3 w-3" />
                    {formatDate(record.createdAt)}
                  </span>
                  <span>{record.wordCount} words</span>
                  <span>{record.inlineMarkCount} 标注</span>
                  <span>{record.sentenceEntryCount} 句析</span>
                  <span className="rounded-pill border border-hairline bg-surface px-2 py-0.5 text-muted">
                    {goalLabel[record.readingGoal] ?? record.readingGoal}
                  </span>
                </div>
              </Link>
              <div className="flex items-center justify-between gap-3 md:justify-end">
                <Link
                  href={readerRoute(record.id)}
                  className="focus-ring inline-flex min-h-9 items-center gap-2 rounded-pill border border-hairline bg-surface px-3 text-xs font-semibold text-ink transition-colors hover:border-muted"
                >
                  继续阅读
                  <ArrowRight aria-hidden="true" className="h-3.5 w-3.5" />
                </Link>
                <DeleteRecordButton recordId={record.id} title={record.title} />
              </div>
            </article>
          ))}
        </section>
      ) : (
        <EmptyState status={status} hasQuery={hasQuery} />
      )}
    </div>
  );
}
