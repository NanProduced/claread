"use client";

import { useEffect, useMemo, useState } from "react";
import { AlertCircle, CheckCircle2 } from "lucide-react";

import { SegmentedControl } from "@/components/composed";
import { Button } from "@/components/primitives/button";
import {
  DEFAULT_READING_VARIANT_BY_GOAL,
  READING_GOAL_OPTIONS,
  READING_VARIANT_OPTIONS,
  type ReadingDefaultState,
  normalizeReadingDefaults,
} from "@/lib/reading-defaults";

type SaveState =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "saved"; message: string }
  | { kind: "error"; message: string };

interface ReadingDefaultsSectionProps extends ReadingDefaultState {
  canEdit: boolean;
}

export function ReadingDefaultsSection({
  readingGoal,
  readingVariant,
  canEdit,
}: ReadingDefaultsSectionProps) {
  const [draft, setDraft] = useState(() => normalizeReadingDefaults({ readingGoal, readingVariant }));
  const [saved, setSaved] = useState(() => normalizeReadingDefaults({ readingGoal, readingVariant }));
  const [state, setState] = useState<SaveState>({ kind: "idle" });

  const variantOptions = useMemo(() => READING_VARIANT_OPTIONS[draft.readingGoal], [draft.readingGoal]);
  const dirty =
    draft.readingGoal !== saved.readingGoal || draft.readingVariant !== saved.readingVariant;

  useEffect(() => {
    if (state.kind === "saved" || state.kind === "error") {
      const timer = setTimeout(() => setState({ kind: "idle" }), 3000);
      return () => clearTimeout(timer);
    }
  }, [state.kind]);

  async function handleSave() {
    if (!canEdit || !dirty || state.kind === "saving") return;

    setState({ kind: "saving" });
    try {
      const response = await fetch("/api/web/profile", {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          settings: {
            default_reading_goal: draft.readingGoal,
            default_reading_variant: draft.readingVariant,
          },
        }),
      });
      const payload = (await response.json().catch(() => ({}))) as { message?: string };
      if (!response.ok) {
        setState({ kind: "error", message: payload.message || "默认阅读偏好保存失败。" });
        return;
      }
      setSaved(draft);
      setState({ kind: "saved", message: "默认透读模式已保存。" });
    } catch {
      setState({ kind: "error", message: "网络异常，暂时无法保存默认透读模式。" });
    }
  }

  function handleGoalChange(nextGoal: ReadingDefaultState["readingGoal"]) {
    setDraft({
      readingGoal: nextGoal,
      readingVariant: DEFAULT_READING_VARIANT_BY_GOAL[nextGoal],
    });
    if (state.kind !== "idle") setState({ kind: "idle" });
  }

  function handleVariantChange(nextVariant: ReadingDefaultState["readingVariant"]) {
    setDraft((current) => ({ ...current, readingVariant: nextVariant }));
    if (state.kind !== "idle") setState({ kind: "idle" });
  }

  function handleReset() {
    setDraft(saved);
    setState({ kind: "idle" });
  }

  return (
    <div className="space-y-0">
      <section className="flex flex-col gap-3 border-b border-hairline py-5 first:pt-0 sm:flex-row sm:items-start sm:justify-between">
        <div className="max-w-sm">
          <h4 className="text-sm font-medium text-ink">阅读目标</h4>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">决定新阅读的学习意图。</p>
        </div>
        <SegmentedControl
          value={draft.readingGoal}
          onValueChange={handleGoalChange}
          options={READING_GOAL_OPTIONS}
          className="shrink-0"
        />
      </section>

      <section className="flex flex-col gap-3 border-b border-hairline py-5 sm:flex-row sm:items-start sm:justify-between">
        <div className="max-w-sm">
          <h4 className="text-sm font-medium text-ink">解析模式</h4>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">决定首次解析时呈现的阅读深度。</p>
        </div>
        <SegmentedControl
          value={draft.readingVariant}
          onValueChange={handleVariantChange}
          options={variantOptions}
          className="shrink-0"
        />
      </section>

      {canEdit && (dirty || state.kind === "saving") ? (
        <div className="flex flex-wrap items-center gap-3 pt-5">
          <Button
            variant="primary-ink"
            className="min-h-11 rounded-[var(--cl-radius-control-sm)] px-4 !shadow-none hover:!translate-y-0 hover:!shadow-none"
            disabled={state.kind === "saving"}
            onClick={handleSave}
          >
            {state.kind === "saving" ? "保存中..." : "保存默认值"}
          </Button>
          <Button
            variant="ghost"
            className="min-h-11 rounded-[var(--cl-radius-control-sm)] px-4"
            disabled={state.kind === "saving"}
            onClick={handleReset}
          >
            取消
          </Button>
        </div>
      ) : null}

      {canEdit && (state.kind === "saved" || state.kind === "error") ? (
        <div className="flex items-center gap-1.5 pt-4 text-xs" role="status" aria-live="polite">
          {state.kind === "saved" ? (
            <><CheckCircle2 className="size-4 text-feedback-success" /><span className="text-feedback-success">{state.message}</span></>
          ) : (
            <><AlertCircle className="size-4 text-destructive" /><span className="text-destructive">{state.message}</span></>
          )}
        </div>
      ) : null}

      {!canEdit ? (
        <p className="pt-5 text-xs leading-5 text-muted-foreground">
          当前会话未连接真实账户，无法保存共享默认值。
        </p>
      ) : null}
    </div>
  );
}