"use client";

import { ArrowRight, Clock3, FileText } from "lucide-react";
import type { Route } from "next";
import Link from "next/link";
import { useMemo, useState } from "react";
import { Button } from "@/components/primitives/button";
import { EmptyState } from "@/components/composed/empty-state";
import { ListRow } from "@/components/composed/list-row";
import { SearchField } from "@/components/composed/search-field";
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
    <div className="space-y-5">
      <SearchField
        label="搜索阅读记录"
        placeholder="搜索标题或原文片段"
        value={query}
        onValueChange={setQuery}
        summary={`显示 ${filteredRecords.length} / ${records.length} 条`}
      />

      {filteredRecords.length > 0 ? (
        <section className="overflow-hidden rounded-panel border border-hairline bg-[linear-gradient(180deg,rgba(255,255,255,0.96),rgba(251,250,246,0.985))] shadow-surface-quiet">
          {filteredRecords.map((record) => (
            <ListRow
              key={record.id}
              href={readerRoute(record.id)}
              title={<span className="line-clamp-2">{record.title}</span>}
              description={<p className="line-clamp-2 max-w-3xl">{excerpt(record.sourceText)}</p>}
              meta={
                <>
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
                </>
              }
              trailing={
                <>
                  <Button asChild variant="outline" size="sm">
                    <Link href={readerRoute(record.id)}>
                      继续阅读
                      <ArrowRight aria-hidden="true" className="h-3.5 w-3.5" />
                    </Link>
                  </Button>
                  <DeleteRecordButton recordId={record.id} title={record.title} />
                </>
              }
            />
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
