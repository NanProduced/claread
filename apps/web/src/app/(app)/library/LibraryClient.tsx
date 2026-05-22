"use client";

import { ArrowRight, BookOpen, Calendar, FileText, Search, MoreHorizontal } from "lucide-react";
import type { Route } from "next";
import Link from "next/link";
import { useMemo, useState } from "react";
import { Button } from "@/components/primitives/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/primitives/popover";
import { EmptyState } from "@/components/composed/empty-state";
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
    <div className="space-y-2">
      <div className="mb-10 flex items-center justify-between border-b border-hairline pb-4">
        <div className="flex w-full max-w-sm items-center gap-3">
          <Search className="h-4 w-4 text-muted" />
          <input
            type="text"
            placeholder="搜索标题或原文片段..."
            className="w-full bg-transparent text-[0.95rem] text-ink outline-none placeholder:text-muted"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <p className="text-[0.65rem] font-bold uppercase tracking-[0.15em] text-ink">{filteredRecords.length} DOCUMENTS</p>
      </div>

      {filteredRecords.length > 0 ? (
        <section className="flex flex-col">
          {filteredRecords.map((record) => (
            <div key={record.id} className="group relative flex items-start justify-between gap-6 border-b border-hairline py-10 transition-colors">
              <Link href={readerRoute(record.id)} className="absolute inset-0 z-0 focus-ring rounded-xl outline-offset-4" aria-label={`阅读 ${record.title}`} />
              <div className="relative z-10 min-w-0 pr-8 pointer-events-none">
                 <h2 className="font-headline text-[1.4rem] font-semibold leading-[1.3] tracking-tight text-ink group-hover:text-lens-blue transition-colors">
                   {record.title}
                 </h2>
                 <p className="mt-2.5 line-clamp-2 max-w-3xl text-[0.95rem] leading-relaxed text-muted">
                   {excerpt(record.sourceText)}
                 </p>
                 <div className="mt-5 flex items-center gap-5 text-[0.7rem] font-semibold uppercase tracking-wider text-muted">
                    <span className="flex items-center gap-1.5 text-ink">
                      <Calendar className="h-3.5 w-3.5" />
                      {formatDate(record.createdAt)}
                    </span>
                    <span className="flex items-center gap-1.5">
                      <FileText className="h-3.5 w-3.5" />
                      {record.wordCount} words
                    </span>
                    <span className="flex items-center gap-1.5 text-[#F5A623]">
                      <BookOpen className="h-3.5 w-3.5" />
                      {record.inlineMarkCount + record.sentenceEntryCount} notes
                    </span>
                 </div>
              </div>
              <div className="relative z-10 flex shrink-0 items-center gap-3 pt-1">
                 <Popover>
                   <PopoverTrigger asChild>
                     <button aria-label="更多选项" className="focus-ring flex h-8 w-8 items-center justify-center rounded-full text-muted hover:bg-surface-warm hover:text-ink transition-colors">
                       <MoreHorizontal className="h-4 w-4" />
                     </button>
                   </PopoverTrigger>
                   <PopoverContent align="end" className="w-[200px] p-2 rounded-xl">
                     <div className="text-[0.65rem] font-bold uppercase tracking-[0.1em] text-muted mb-2 px-2 pt-1">Options</div>
                     <div className="flex items-center justify-between px-2 py-1.5">
                       <span className="text-sm font-medium text-ink">删除记录</span>
                       <DeleteRecordButton recordId={record.id} title={record.title} />
                     </div>
                   </PopoverContent>
                 </Popover>
                 
                 <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full border border-hairline text-muted transition-colors group-hover:border-lens-blue group-hover:bg-lens-blue group-hover:text-surface">
                   <ArrowRight className="h-4 w-4" />
                 </div>
              </div>
            </div>
          ))}
        </section>
      ) : (
        <EmptyState
          icon={FileText}
          title={hasQuery ? "没有匹配的记录" : statusTitle[status]}
          description={
            hasQuery ? "换一个标题、关键词或原文片段再试。" : "完成一次真实解析后，这里会成为你的英文阅读档案。"
          }
          action={
            !hasQuery ? (
              <Button asChild variant="outline">
                <Link href={readRoute}>解析新文章</Link>
              </Button>
            ) : undefined
          }
        />
      )}
    </div>
  );
}
