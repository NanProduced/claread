"use client";

import { useMemo, useState } from "react";

import type { ReviewAction, ReviewItemVm } from "@/types/view/ReviewItemVm";
import type { ReviewSubmitResultVm } from "@/types/view/ReviewQueueVm";

interface ReviewQueueClientProps {
  initialItems: ReviewItemVm[];
}

type SubmitState =
  | { kind: "idle" }
  | { kind: "success"; message: string }
  | { kind: "error"; message: string };

type SubmitResponse =
  | {
      ok: true;
      item: ReviewSubmitResultVm;
      message: string;
    }
  | {
      ok: false;
      status: number;
      code: string;
      message: string;
    };

const actionLabel: Record<ReviewAction, string> = {
  known: "认识",
  unfamiliar: "不熟",
};

function formatDate(value: string | undefined): string {
  if (!value) {
    return "未安排";
  }

  return new Date(value).toLocaleDateString("zh-CN", {
    month: "short",
    day: "numeric",
  });
}

export function ReviewQueueClient({ initialItems }: ReviewQueueClientProps) {
  const [items, setItems] = useState(initialItems);
  const [pendingItemId, setPendingItemId] = useState<string | null>(null);
  const [submitState, setSubmitState] = useState<SubmitState>({ kind: "idle" });

  const activeItem = items[0];
  const remainingCount = useMemo(() => Math.max(items.length - 1, 0), [items.length]);

  function submit(itemId: string, action: ReviewAction) {
    setPendingItemId(itemId);
    setSubmitState({ kind: "idle" });

    void (async () => {
      try {
        const response = await fetch(`/api/web/review/items/${encodeURIComponent(itemId)}/submit`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ result: action }),
        });
        const payload = (await response.json()) as SubmitResponse;

        if (!payload.ok) {
          setSubmitState({ kind: "error", message: payload.message });
          return;
        }

        setItems((current) => current.filter((item) => item.id !== itemId));
        setSubmitState({
          kind: "success",
          message: `${payload.item.lemma} 已标记为「${actionLabel[action]}」。`,
        });
      } catch {
        setSubmitState({
          kind: "error",
          message: "提交复习结果失败，请稍后重试。",
        });
      } finally {
        setPendingItemId(null);
      }
    })();
  }

  if (!activeItem) {
    return (
      <section className="rounded-note border border-hairline bg-surface p-8 text-center shadow-surface-quiet">
        <h2 className="font-headline text-xl font-semibold text-ink">本轮完成</h2>
        <p className="mt-2 text-sm leading-6 text-muted">
          当前队列已清空。稍后会根据复习计划生成新的待复习词条。
        </p>
      </section>
    );
  }

  const disabled = pendingItemId === activeItem.id;

  return (
    <section className="flex flex-col gap-4">
      {submitState.kind !== "idle" ? (
        <div
          className={`rounded-md border px-4 py-3 text-sm leading-6 ${
            submitState.kind === "success"
              ? "border-structure-green/30 bg-structure-green/10 text-ink-soft"
              : "border-vocab-amber/40 bg-vocab-amber/10 text-ink-soft"
          }`}
        >
          {submitState.message}
        </div>
      ) : null}

      <article className="rounded-note border border-hairline bg-surface p-6 shadow-surface-quiet md:p-8">
        <div className="flex flex-col gap-6">
          <div className="flex flex-col gap-2 border-b border-hairline pb-5">
            <div className="flex flex-wrap items-center gap-2 text-xs font-semibold text-muted">
              <span>Stage {activeItem.reviewStage}</span>
              <span aria-hidden="true">/</span>
              <span>{activeItem.reviewCount} 次复习</span>
              <span aria-hidden="true">/</span>
              <span>下次 {formatDate(activeItem.nextReviewAt)}</span>
            </div>
            <h2 className="font-display text-4xl font-semibold tracking-normal text-ink">
              {activeItem.displayWord}
            </h2>
            <div className="flex flex-wrap items-center gap-3 text-sm text-muted">
              {activeItem.phonetic ? <span>{activeItem.phonetic}</span> : null}
              {activeItem.partOfSpeech ? (
                <span className="rounded-pill border border-hairline bg-surface-warm px-2.5 py-1 text-xs">
                  {activeItem.partOfSpeech}
                </span>
              ) : null}
              <span>{activeItem.masteryStatus}</span>
            </div>
          </div>

          <div className="space-y-4">
            <p className="text-lg leading-8 text-ink-soft">{activeItem.meaning}</p>
            {activeItem.sourceSentence ? (
              <blockquote className="border-l-2 border-lens-blue/30 bg-lens-blue-soft px-4 py-3 text-sm leading-7 text-ink-soft">
                {activeItem.sourceSentence}
              </blockquote>
            ) : null}
            {activeItem.sourceContext && activeItem.sourceContext !== activeItem.sourceSentence ? (
              <p className="text-sm leading-6 text-muted">{activeItem.sourceContext}</p>
            ) : null}
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <button
              type="button"
              disabled={disabled}
              onClick={() => submit(activeItem.id, "unfamiliar")}
              className="rounded-md border border-hairline bg-surface-warm px-4 py-3 text-sm font-semibold text-ink transition-colors hover:border-muted disabled:cursor-not-allowed disabled:opacity-60"
            >
              不熟，明天再来
            </button>
            <button
              type="button"
              disabled={disabled}
              onClick={() => submit(activeItem.id, "known")}
              className="rounded-md border border-lens-blue bg-lens-blue px-4 py-3 text-sm font-semibold text-surface transition-colors hover:bg-lens-blue/90 disabled:cursor-not-allowed disabled:opacity-60"
            >
              认识，进入下一阶段
            </button>
          </div>
        </div>
      </article>

      <div className="text-center text-xs font-semibold text-muted">
        队列剩余 {remainingCount} 个
      </div>
    </section>
  );
}
