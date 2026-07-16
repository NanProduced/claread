"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Button } from "@/components/primitives/button";
import { EmptyState } from "@/components/composed";
import { BookOpenCheck } from "lucide-react";
import { cn } from "@/lib/cn";

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

function CardContent({
  item,
  isTop,
  onAction,
  disabled,
}: {
  item: ReviewItemVm;
  isTop: boolean;
  onAction: (action: ReviewAction) => void;
  disabled: boolean;
}) {
  return (
    <div
      className={cn(
        "flex h-full flex-col transition-opacity duration-500",
        isTop ? "opacity-100" : "opacity-60",
      )}
    >
      {/* ── Meta Header ── */}
      <div className="mb-6 flex flex-col gap-2 border-b border-hairline/60 pb-5">
        <div className="flex flex-wrap items-center gap-2 text-[0.68rem] font-bold tracking-[0.16em] text-subtle">
          <span>Stage {item.reviewStage}</span>
          <span className="opacity-40" aria-hidden="true">/</span>
          <span>{item.reviewCount} 次复习</span>
          <span className="opacity-40" aria-hidden="true">/</span>
          <span>下次 {formatDate(item.nextReviewAt)}</span>
        </div>

        <div className="mt-2 flex flex-wrap items-baseline gap-x-4 gap-y-2">
          <h2 className="font-headline text-[2.6rem] font-semibold leading-none tracking-tight text-ink md:text-[3.2rem]">
            {item.displayWord}
          </h2>
          <div className="flex items-center gap-2">
            {item.phonetic && (
              <span className="font-sans text-sm text-muted-foreground">{item.phonetic}</span>
            )}
            {item.partOfSpeech && (
              <span className="rounded-[6px] border border-hairline bg-surface-warm/50 px-2 py-0.5 font-sans text-[0.72rem] font-semibold text-muted-foreground">
                {item.partOfSpeech}
              </span>
            )}
            <span className="rounded-[6px] border border-lens-blue/10 bg-lens-blue/5 px-2 py-0.5 text-[0.72rem] font-semibold text-lens-blue/80">
              {item.masteryStatus}
            </span>
          </div>
        </div>
      </div>

      {/* ── Dictionary / Context Body ── */}
      <div className="min-h-[14rem] flex-1 space-y-6">
        <p className="text-[1.1rem] font-semibold leading-relaxed text-ink-soft">
          {item.meaning}
        </p>

        {item.sourceSentence && (
          <div className="mt-8 border-l-2 border-lens-blue/30 pl-4">
            <p className="font-serif text-[1.15rem] italic leading-[1.65] tracking-[0.01em] text-ink">
              {item.sourceSentence}
            </p>
            {item.sourceContext && item.sourceContext !== item.sourceSentence && (
              <p className="mt-3 font-reading text-[0.95rem] leading-relaxed text-ink-soft/80">
                {item.sourceContext}
              </p>
            )}
          </div>
        )}
      </div>

      {/* ── Action Buttons ── */}
      <div className="mt-10 grid gap-4 border-t border-hairline/40 pt-6 sm:grid-cols-2">
        <Button
          type="button"
          variant="outline"
          size="lg"
          className="h-14 rounded-[12px] text-[0.95rem] font-semibold tracking-[0.04em] text-ink-soft transition-colors hover:bg-surface-warm"
          disabled={!isTop || disabled}
          onClick={() => onAction("unfamiliar")}
        >
          不熟，明天再来
        </Button>
        <Button
          type="button"
          variant="primary-ink"
          size="lg"
          className="h-14 rounded-[12px] text-[0.95rem] font-semibold tracking-[0.04em] transition-colors"
          disabled={!isTop || disabled}
          onClick={() => onAction("known")}
        >
          认识，进入下一阶段
        </Button>
      </div>
    </div>
  );
}

export function ReviewQueueClient({ initialItems }: ReviewQueueClientProps) {
  const [items, setItems] = useState(initialItems);
  const [pendingItemId, setPendingItemId] = useState<string | null>(null);
  const [submitState, setSubmitState] = useState<SubmitState>({ kind: "idle" });

  const [animatingId, setAnimatingId] = useState<string | null>(null);
  const [animatingAction, setAnimatingAction] = useState<ReviewAction | null>(null);
  const animatingIdRef = useRef<string | null>(null);

  const activeItem = items[0];
  const remainingCount = useMemo(() => Math.max(items.length - 1, 0), [items.length]);

  const submit = useCallback((itemId: string, action: ReviewAction) => {
    if (animatingIdRef.current) return;

    animatingIdRef.current = itemId;
    setAnimatingId(itemId);
    setAnimatingAction(action);
    setPendingItemId(itemId);
    setSubmitState({ kind: "idle" });

    // Animate out for 350ms, then remove from items
    setTimeout(() => {
      setItems((current) => current.filter((item) => item.id !== itemId));
      setAnimatingId(null);
      setAnimatingAction(null);
      animatingIdRef.current = null;
      setPendingItemId(null); // allow interaction with next card

      // Fire API in background
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
        } catch {
          setSubmitState({
            kind: "error",
            message: "提交复习结果失败，请稍后重试。",
          });
        }
      })();
    }, 400); // 400ms CSS transition
  }, []);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (animatingIdRef.current || !activeItem) return;
      if (e.key === "ArrowLeft") {
        submit(activeItem.id, "unfamiliar");
      } else if (e.key === "ArrowRight") {
        submit(activeItem.id, "known");
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [activeItem, submit]);

  if (!activeItem && !animatingId) {
    return (
      <EmptyState
        icon={BookOpenCheck}
        title="本轮完成"
        description="当前队列已清空。稍后会根据复习计划生成新的待复习词条。"
        className="border-t-0 py-0 mt-20"
      />
    );
  }

  const stackItems = items.slice(0, 3);
  const disabled = pendingItemId !== null;

  return (
    <section className="relative flex w-full max-w-4xl mx-auto flex-col items-center pt-8 pb-12 overflow-x-clip">
      {/* ── Error Message ── */}
      {submitState.kind === "error" && (
        <div className="mb-6 animate-in fade-in slide-in-from-top-2 rounded-pill border border-vocab-amber/40 bg-vocab-amber/10 px-5 py-2 text-[0.82rem] font-semibold text-vocab-amber">
          {submitState.message}
        </div>
      )}

      {/* ── Flashcard Stack ── */}
      <div className="relative w-full max-w-[42rem] min-h-[34rem] sm:min-h-[30rem] md:min-h-[32rem]">
        {stackItems
          .map((item, rawIndex) => {
            const isAnimatingThis = animatingId === item.id;

            // If the top item is animating away, the remaining items visually step up
            let visualIndex = rawIndex;
            if (animatingId && animatingId !== item.id) {
              visualIndex = rawIndex - 1;
            }

            const isTop = visualIndex === 0 && !isAnimatingThis;

            return (
              <div
                key={item.id}
                className={cn(
                  "absolute inset-x-0 top-0 w-full transition-all duration-[450ms] ease-[cubic-bezier(0.23,1,0.32,1)] rounded-[1.5rem] border border-hairline/80 bg-[linear-gradient(to_bottom,color-mix(in_srgb,var(--surface)_90%,white),color-mix(in_srgb,var(--reader-paper)_90%,white))] p-6 shadow-sm md:p-10",
                  
                  // Exiting Animation
                  isAnimatingThis &&
                    animatingAction === "known" &&
                    "pointer-events-none z-40 translate-x-[80%] rotate-[8deg] scale-95 opacity-0 shadow-none md:translate-x-[100%]",
                  isAnimatingThis &&
                    animatingAction === "unfamiliar" &&
                    "pointer-events-none z-40 translate-x-[-80%] rotate-[-8deg] scale-95 opacity-0 shadow-none md:translate-x-[-100%]",

                  // Normal Stack Logic
                  !isAnimatingThis &&
                    visualIndex === 0 &&
                    "z-30 translate-y-0 scale-100 opacity-100 shadow-[0_16px_40px_rgba(28,24,18,0.06)]",
                  !isAnimatingThis &&
                    visualIndex === 1 &&
                    "pointer-events-none z-20 translate-y-6 scale-[0.96] opacity-[0.85] shadow-[0_8px_20px_rgba(28,24,18,0.04)]",
                  !isAnimatingThis &&
                    visualIndex === 2 &&
                    "pointer-events-none z-10 translate-y-12 scale-[0.92] border-transparent opacity-50",
                  !isAnimatingThis &&
                    visualIndex > 2 &&
                    "pointer-events-none opacity-0"
                )}
              >
                <CardContent
                  item={item}
                  isTop={isTop}
                  onAction={(action) => submit(item.id, action)}
                  disabled={disabled}
                />
              </div>
            );
          })
          .reverse()}
      </div>
    </section>
  );
}
