"use client";

import { AlertTriangle, ArrowRight, Calendar, LogIn, Plus } from "lucide-react";
import Link from "next/link";
import {
  Button,
} from "@/components/primitives/button";
import { appReadRoute, loginRouteBase } from "@/lib/routes";
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
      return "状态未知";
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
      return "状态未知";
  }
}

function recordCtaLabel(item: ReadingRecordListItemVm): string | null {
  switch (item.productState) {
    case "action_required":
    case "needs_confirmation":
      return "去处理";
    case "failed":
      return "查看详情";
    default:
      return null;
  }
}

export function ReadingRecordSection({
  readingRecords,
  status,
  message,
  hasQuery = false,
  onResetQuery,
}: {
  readingRecords: ReadingRecordListItemVm[];
  status: "ready" | ReadingRecordsBffError["code"];
  message?: string;
  hasQuery?: boolean;
  onResetQuery?: () => void;
}) {
  if (status === "auth_required" || status === "upstream_auth_failed") {
    const copy =
      status === "auth_required"
        ? "请先登录后查看阅读记录。"
        : "登录态已失效，请重新登录。";
    return (
      <section className="px-1 py-10">
        <div className="flex items-start gap-3 text-[0.85rem] text-rose-800/90">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <div>
            <p>{message || copy}</p>
            <div className="mt-4">
              <Button asChild variant="primary" className="gap-2">
                <Link href={loginRouteBase}>
                  <LogIn aria-hidden="true" className="h-4 w-4" />
                  去登录
                </Link>
              </Button>
            </div>
          </div>
        </div>
      </section>
    );
  }

  if (status === "limited_debug") {
    return (
      <section className="px-1 py-10">
        <div className="flex items-start gap-3 text-[0.85rem] text-amber-800/90">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <p>
            {message || "调试登录态无法访问阅读记录，请使用完整登录会话后再试。"}
          </p>
        </div>
      </section>
    );
  }

  if (status !== "ready") {
    return (
      <section className="px-1 py-10">
        <div className="flex items-start gap-3 text-[0.85rem] text-rose-800/90">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <p>{message || "无法加载阅读记录，请稍后重试。"}</p>
        </div>
      </section>
    );
  }

  if (readingRecords.length === 0) {
    return (
      <section className="px-1 py-10">
        {hasQuery ? (
          <div>
            <p className="text-[0.85rem] leading-6 text-muted">
              当前检索条件下还没有匹配的阅读记录。
            </p>
            {onResetQuery ? (
              <div className="mt-4">
                <Button variant="outline" onClick={onResetQuery}>
                  查看全部记录
                </Button>
              </div>
            ) : null}
          </div>
        ) : (
          <div>
            <p className="text-[0.85rem] leading-6 text-muted">
              还没有阅读记录。提交一篇新解读后会在这里显示。
            </p>
            <div className="mt-4">
              <Button asChild variant="primary" className="gap-2">
                <Link href={appReadRoute}>
                  <Plus aria-hidden="true" className="h-4 w-4" />
                  提交一篇新解读
                </Link>
              </Button>
            </div>
          </div>
        )}
      </section>
    );
  }

  return (
    <section className="pr-2">
      <ul className="divide-y divide-hairline/40">
        {readingRecords.map((item) => {
          const ctaLabel = recordCtaLabel(item);
          return (
            <li key={item.readingRecordId}>
              <Link
                href={item.readerUrl}
                className="group flex items-center justify-between gap-4 py-5 transition-colors hover:bg-black/[0.02] rounded-md px-2"
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate font-headline text-[1.08rem] font-semibold text-ink transition-colors group-hover:text-lens-blue">
                    {item.title}
                  </p>
                  <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[0.7rem] text-muted">
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
                {ctaLabel ? (
                  <span className="inline-flex items-center gap-1.5 rounded-pill bg-lens-blue/10 px-3 py-1.5 text-[0.72rem] font-semibold tracking-[0.06em] text-lens-blue transition-all duration-200 group-hover:bg-lens-blue/15 group-hover:translate-x-[2px]">
                    {ctaLabel}
                    <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
                  </span>
                ) : (
                  <ArrowRight
                    className="h-4 w-4 shrink-0 text-muted transition-all duration-200 group-hover:translate-x-1 group-hover:text-ink"
                    aria-hidden="true"
                  />
                )}
              </Link>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
