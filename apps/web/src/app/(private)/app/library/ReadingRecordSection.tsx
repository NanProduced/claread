"use client";

import { AlertTriangle, ArrowRight, Calendar, LogIn, Plus } from "lucide-react";
import Link from "next/link";
import { Button } from "@/components/primitives/button";
import {
  readingRecordStatusKey,
  readingRecordStatusLabel,
} from "@/lib/reader-record-status";
import { appReadRoute, loginRouteBase } from "@/lib/routes";
import type {
  ReadingRecordListItemVm,
  ReadingRecordsBffError,
} from "@/services/bff/reading-records";
import type { ReadingRecordProductState } from "@/types/api/reading-records";

function formatDate(value: string): string {
  return new Date(value).toLocaleDateString("zh-CN");
}

const NEEDS_ATTENTION_PRODUCT_STATES = [
  "needs_confirmation",
  "action_required",
  "failed",
] as const satisfies readonly ReadingRecordProductState[];

function statusLabelFor(item: ReadingRecordListItemVm): string {
  return readingRecordStatusLabel(
    readingRecordStatusKey(item.productState, item.readinessState),
  );
}

function rowIsClickable(item: ReadingRecordListItemVm): boolean {
  return item.productState !== "needs_confirmation";
}

function recordCtaLabel(item: ReadingRecordListItemVm): string | null {
  switch (item.productState) {
    case "action_required":
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

  const { priorityTop, fullListItems } = splitForRender(readingRecords);

  const renderRow = (item: ReadingRecordListItemVm) => {
    const clickable = rowIsClickable(item);
    const ctaLabel = clickable ? recordCtaLabel(item) : null;

    const titleClass = clickable
      ? "truncate font-headline text-[1.08rem] font-semibold text-ink transition-colors group-hover:text-lens-blue"
      : "truncate font-headline text-[1.08rem] font-semibold text-ink";

    const trailing = !clickable ? (
      <span
        data-testid="library-needs-confirmation-note"
        className="max-w-[60%] text-right text-[0.72rem] leading-snug text-muted"
      >
        这篇内容需要在提交时确认后才能开始阅读。
      </span>
    ) : ctaLabel ? (
      <span className="inline-flex items-center gap-1.5 rounded-pill bg-lens-blue/10 px-3 py-1.5 text-[0.72rem] font-semibold tracking-[0.06em] text-lens-blue transition-all duration-200 group-hover:bg-lens-blue/15 group-hover:translate-x-[2px]">
        {ctaLabel}
        <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
      </span>
    ) : (
      <ArrowRight
        className="h-4 w-4 shrink-0 text-muted transition-all duration-200 group-hover:translate-x-1 group-hover:text-ink"
        aria-hidden="true"
      />
    );

    const inner = (
      <div className="group flex items-center justify-between gap-4 py-5 rounded-md px-2">
        <div className="min-w-0 flex-1">
          <p className={titleClass}>{item.title}</p>
          <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[0.7rem] text-muted">
            <span className="flex items-center gap-1">
              <Calendar className="h-3 w-3 opacity-60" />
              {formatDate(item.createdAt)}
            </span>
            <span>{statusLabelFor(item)}</span>
          </div>
        </div>
        {trailing}
      </div>
    );

    if (!rowIsClickable(item)) {
      return (
        <li
          key={item.readingRecordId}
          aria-disabled="true"
          title="请在原输入流程继续确认"
        >
          {inner}
        </li>
      );
    }

    return (
      <li key={item.readingRecordId}>
        <Link
          href={item.readerUrl}
          className="transition-colors hover:bg-black/[0.02]"
        >
          {inner}
        </Link>
      </li>
    );
  };

  return (
    <section className="pr-2">
      {priorityTop.length > 0 ? (
        <div data-testid="library-needs-attention" className="mb-4">
          <h2 className="px-2 pb-2 text-[0.72rem] font-semibold tracking-[0.08em] text-muted">
            需要处理
          </h2>
          <ul className="divide-y divide-hairline/40">
            {priorityTop.map(renderRow)}
          </ul>
        </div>
      ) : null}

      <ul className="divide-y divide-hairline/40">
        {fullListItems.map(renderRow)}
      </ul>
    </section>
  );
}

function splitForRender(items: ReadingRecordListItemVm[]): {
  priorityTop: ReadingRecordListItemVm[];
  fullListItems: ReadingRecordListItemVm[];
} {
  const priorityItems = items.filter((r) =>
    (NEEDS_ATTENTION_PRODUCT_STATES as readonly ReadingRecordProductState[]).includes(
      r.productState,
    ),
  );
  const priorityTop = priorityItems.slice(0, 3);
  const pinnedIds = new Set(priorityTop.map((r) => r.readingRecordId));
  // 主列表：从原 items 中过滤掉已置顶的前 3 条，保留 items 的原顺序（服务端排序
  // `last_opened_at DESC, created_at DESC` 不被打乱，剩余 priority 也保留在原位置）。
  const fullListItems = items.filter(
    (r) => !pinnedIds.has(r.readingRecordId),
  );
  return { priorityTop, fullListItems };
}

export { NEEDS_ATTENTION_PRODUCT_STATES };
