"use client";

import { AlertTriangle, Calendar, LogIn, Plus } from "lucide-react";
import Link from "next/link";
import { Button } from "@/components/primitives/button";
import { appReadResumeCandidateRoute } from "@/lib/routes";
import {
  formatReadingPlanSummary,
  normalizeReadingDefaults,
  type ReadingDefaultState,
} from "@/lib/reading-defaults";
import { appReadRoute, loginRouteBase } from "@/lib/routes";
import { looksLikeSafeUserCopy } from "@/lib/user-facing-error";
import { cn } from "@/lib/cn";
import { ReadingRecordActionsMenu } from "@/components/reading-records/ReadingRecordActionsMenu";
import type {
  ReadingRecordListItemVm,
  ReadingRecordsBffError,
} from "@/services/bff/reading-records";
import type { ReadingRecordProductState } from "@/types/api/reading-records";

function safeMessage(message: string | undefined, fallback: string): string {
  // 只放行 BFF 写好的中文文案；上游英文 detail 不透传。
  return message && looksLikeSafeUserCopy(message) ? message : fallback;
}

function formatDate(value: string): string {
  return new Date(value).toLocaleDateString("zh-CN");
}

function timeLabelFor(item: ReadingRecordListItemVm): string {
  if (item.lastOpenedAt) {
    return `上次阅读 ${formatDate(item.lastOpenedAt)}`;
  }
  return `导入于 ${formatDate(item.createdAt)}`;
}

function readingPlanLabelFor(item: ReadingRecordListItemVm): string | null {
  if (!item.readingGoal || !item.readingVariant) return null;
  const plan = normalizeReadingDefaults({
    readingGoal: item.readingGoal,
    readingVariant: item.readingVariant,
  } as Partial<ReadingDefaultState>);
  return formatReadingPlanSummary(plan.readingGoal, plan.readingVariant);
}

const NEEDS_ATTENTION_PRODUCT_STATES = [
  "needs_confirmation",
  "action_required",
  "failed",
] as const satisfies readonly ReadingRecordProductState[];

/**
 * 状态 chip：进行中的解析显示安静的「解析中」；只有需要用户行动的状态
 * 才用强调色；已就绪的记录保持安静，不再用灰字重复状态。
 */
function StatusChip({ item }: { item: ReadingRecordListItemVm }) {
  if (item.productState === "processing") {
    return (
      <span className="inline-flex items-center rounded-full bg-surface-raised px-2 py-0.5 text-[0.64rem] font-semibold tracking-[0.05em] text-muted-foreground">
        解析中
      </span>
    );
  }
  if (item.productState === "needs_confirmation") {
    return (
      <span className="inline-flex items-center rounded-full bg-lens-blue-soft px-2 py-0.5 text-[0.64rem] font-semibold tracking-[0.05em] text-lens-blue">
        待确认
      </span>
    );
  }
  if (item.productState === "action_required") {
    return (
      <span className="inline-flex items-center rounded-full bg-feedback-warning-soft px-2 py-0.5 text-[0.64rem] font-semibold tracking-[0.05em] text-ink/75">
        待处理
      </span>
    );
  }
  if (item.productState === "failed") {
    return (
      <span className="inline-flex items-center rounded-full bg-feedback-error/10 px-2 py-0.5 text-[0.64rem] font-semibold tracking-[0.05em] text-feedback-error">
        解析失败
      </span>
    );
  }
  return null;
}

function recordCtaLabel(item: ReadingRecordListItemVm): string | null {
  switch (item.productState) {
    case "needs_confirmation":
      return "继续确认";
    case "action_required":
      return "去处理";
    case "failed":
      return "查看详情";
    default:
      return null;
  }
}

function recordHrefFor(item: ReadingRecordListItemVm): string {
  return item.productState === "needs_confirmation"
    ? appReadResumeCandidateRoute(item.readingRecordId)
    : item.readerUrl;
}

export function ReadingRecordSection({
  readingRecords,
  status,
  message,
  hasQuery = false,
  onResetQuery,
  onRecordDeleted,
}: {
  readingRecords: ReadingRecordListItemVm[];
  status: "ready" | ReadingRecordsBffError["code"];
  message?: string;
  hasQuery?: boolean;
  onResetQuery?: () => void;
  onRecordDeleted?: (recordId: string) => void;
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
            <p>{safeMessage(message, copy)}</p>
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
            {safeMessage(message, "调试登录态无法访问阅读记录，请使用完整登录会话后再试。")}
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
          <p>{safeMessage(message, "无法加载阅读记录，请稍后重试。")}</p>
        </div>
      </section>
    );
  }

  if (readingRecords.length === 0) {
    return (
      <section className="px-1 py-10">
        {hasQuery ? (
          <div>
            <p className="text-[0.85rem] leading-6 text-muted-foreground">
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
            <p className="text-[0.85rem] leading-6 text-muted-foreground">
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
    // 行信息架构：标题 / 元信息（来源 · 阅读方案 · 时间）/ 右侧状态 chip
    // + 行动提示。整行是唯一 Link 点击区，操作菜单 hover 出现。
    const ctaLabel = recordCtaLabel(item);
    const planLabel = readingPlanLabelFor(item);

    return (
      <li key={item.readingRecordId} className="group relative">
        <Link
          href={recordHrefFor(item)}
          className="block rounded-md transition-colors hover:bg-black/[0.02]"
        >
          <div className="flex items-center justify-between gap-4 py-3 pl-2 pr-10">
            <div className="min-w-0 flex-1">
              <p className="truncate font-headline text-[1rem] font-semibold text-ink transition-colors group-hover:text-lens-blue">
                {item.title}
              </p>
              <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[0.7rem] text-muted-foreground">
                <span>{item.sourceLabel}</span>
                {planLabel ? (
                  <>
                    <span aria-hidden="true" className="text-subtle/60">·</span>
                    <span>{planLabel}</span>
                  </>
                ) : null}
                <span className="flex items-center gap-1">
                  <Calendar className="h-3 w-3 opacity-60" />
                  {timeLabelFor(item)}
                </span>
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <StatusChip item={item} />
              {ctaLabel ? (
                <span className="text-[0.72rem] font-medium text-lens-blue">
                  {ctaLabel}
                </span>
              ) : null}
            </div>
          </div>
        </Link>
        <ReadingRecordActionsMenu
          recordId={item.readingRecordId}
          title={item.title}
          onDeleted={onRecordDeleted}
          className={cn(
            "absolute right-1 top-1/2 -translate-y-1/2",
            // Touch devices have no hover: the trigger is always visible
            // below md.  From md up it stays quiet until the row is
            // hovered/focused or the menu is open (per-trigger
            // data-state=open).
            "opacity-100 md:opacity-0 md:group-hover:opacity-100 md:group-focus-within:opacity-100 md:focus-visible:opacity-100 data-[state=open]:opacity-100",
          )}
        />
      </li>
    );
  };

  return (
    <section className="pr-2">
      {priorityTop.length > 0 ? (
        <div data-testid="library-needs-attention" className="mb-4">
          <h2 className="px-2 pb-2 text-[0.72rem] font-semibold tracking-[0.08em] text-muted-foreground">
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
