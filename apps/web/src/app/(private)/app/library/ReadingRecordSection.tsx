"use client";

import { AlertTriangle, ArrowRight, Calendar } from "lucide-react";
import Link from "next/link";
import type {
  ReadingRecordListItemVm,
  ReadingRecordsBffError,
} from "@/services/bff/reading-records";

function formatDate(value: string): string {
  return new Date(value).toLocaleDateString("zh-CN");
}

function productStateLabel(state: string): string {
  switch (state) {
    case "processing":
      return "处理中";
    case "needs_confirmation":
      return "待确认";
    case "readable_enhancing":
      return "可读·增强中";
    case "action_required":
      return "需处理";
    case "failed":
      return "失败";
    case "deleted":
      return "已删除";
    default:
      return state;
  }
}

function readinessStateLabel(state: string): string {
  switch (state) {
    case "submitted":
      return "已提交";
    case "candidate_base_ready":
      return "候选 Base 就绪";
    case "article_ready":
      return "文章就绪";
    case "initial_enhancement_ready":
      return "初始增强就绪";
    case "coverage_complete":
      return "覆盖完成";
    default:
      return state;
  }
}

export function ReadingRecordSection({
  readingRecords,
  status,
  message,
  hasQuery = false,
}: {
  readingRecords: ReadingRecordListItemVm[];
  status: "ready" | ReadingRecordsBffError["code"];
  message?: string;
  hasQuery?: boolean;
}) {
  return (
    <section className="mb-8 shrink-0 border-b border-hairline pb-6">
      <div className="mb-4 flex items-center gap-3">
        <p className="text-[0.6rem] font-bold tracking-[0.2em] text-lens-blue">
          Reading Records
        </p>
        <div className="h-[1px] w-8 bg-hairline" />
      </div>
      <h2 className="mb-4 font-headline text-[1.3rem] font-semibold leading-tight text-ink">阅读记录</h2>

      {status !== "ready" ? (
        <div className="flex items-center gap-2 text-[0.85rem] text-rose-800/90">
          <AlertTriangle className="h-4 w-4 shrink-0" />
          <span>{message || "无法加载阅读记录，请稍后重试。"}</span>
        </div>
      ) : readingRecords.length === 0 ? (
        <p className="text-[0.85rem] leading-6 text-muted">
          {hasQuery
            ? "当前检索条件下还没有匹配的阅读记录。"
            : "还没有阅读记录。提交一篇新解读后会在这里显示。"}
        </p>
      ) : (
        <ul className="space-y-1">
          {readingRecords.map((item) => (
            <li key={item.readingRecordId}>
              <Link
                href={item.readerUrl}
                className="group flex items-center justify-between gap-4 rounded-md px-2 py-2 transition-colors hover:bg-black/[0.03]"
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate font-headline text-[1rem] font-semibold text-ink transition-colors group-hover:text-lens-blue">
                    {item.title}
                  </p>
                  <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[0.7rem] text-muted">
                    <span className="flex items-center gap-1">
                      <Calendar className="h-3 w-3 opacity-60" />
                      {formatDate(item.createdAt)}
                    </span>
                    <span className="text-muted/30 select-none">·</span>
                    <span>{productStateLabel(item.productState)}</span>
                    <span className="text-muted/30 select-none">·</span>
                    <span>{readinessStateLabel(item.readinessState)}</span>
                  </div>
                </div>
                <ArrowRight className="h-4 w-4 shrink-0 text-muted transition-all duration-200 group-hover:translate-x-1 group-hover:text-ink" />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
