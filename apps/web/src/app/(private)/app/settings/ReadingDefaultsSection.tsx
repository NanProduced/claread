"use client";

import { useEffect, useMemo, useState } from "react";
import { CheckCircle2, AlertCircle } from "lucide-react";

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
      const timer = setTimeout(() => {
        setState({ kind: "idle" });
      }, 3000);
      return () => clearTimeout(timer);
    }
  }, [state.kind]);

  async function handleSave() {
    if (!canEdit || !dirty || state.kind === "saving") {
      return;
    }

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
    if (state.kind !== "idle") {
      setState({ kind: "idle" });
    }
  }

  function handleVariantChange(nextVariant: ReadingDefaultState["readingVariant"]) {
    setDraft((current) => ({ ...current, readingVariant: nextVariant }));
    if (state.kind !== "idle") {
      setState({ kind: "idle" });
    }
  }

  function handleReset() {
    setDraft(saved);
    setState({ kind: "idle" });
  }

  return (
    <div className="space-y-5">
      <p className="text-xs text-muted-foreground">
        这里的设置仅作为每次新建阅读时的初始默认值。在实际解析文章前，您依然可以针对单篇文章自由调整。
      </p>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-[6rem_1fr]">
        <span className="pt-2 text-xs text-muted-foreground">阅读目标</span>
        <SegmentedControl
          value={draft.readingGoal}
          onValueChange={handleGoalChange}
          options={READING_GOAL_OPTIONS}
          className="[&_button]:min-h-11"
        />
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-[6rem_1fr]">
        <span className="pt-2 text-xs text-muted-foreground">解析模式</span>
        <SegmentedControl
          value={draft.readingVariant}
          onValueChange={handleVariantChange}
          options={variantOptions}
          className="[&_button]:min-h-11"
        />
      </div>

      <div className="flex flex-wrap items-center gap-3 pt-2">
        <Button
          variant="primary-ink"
          className="min-h-11 min-w-[128px] justify-center"
          disabled={!canEdit || !dirty || state.kind === "saving"}
          onClick={handleSave}
        >
          {state.kind === "saving" ? "保存中..." : "保存默认值"}
        </Button>
        <Button
          variant="ghost"
          className="min-h-11 min-w-[96px] justify-center"
          disabled={!dirty || state.kind === "saving"}
          onClick={handleReset}
        >
          取消
        </Button>
      </div>

      {canEdit && (state.kind === "saved" || state.kind === "error") && (
        <div className="flex items-center gap-1.5 text-xs">
          {state.kind === "saved" ? (
            <>
              <CheckCircle2 className="size-4 text-muted-foreground" />
              <span className="text-muted-foreground">{state.message}</span>
            </>
          ) : (
            <>
              <AlertCircle className="size-4 text-destructive" />
              <span className="text-destructive">{state.message}</span>
            </>
          )}
        </div>
      )}

      {!canEdit && (
        <p className="text-xs text-muted-foreground">
          当前会话未连接真实账户，无法保存共享默认值。
        </p>
      )}
    </div>
  );
}
