"use client";

import { useMemo, useState } from "react";

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
      <div>
        <h3 className="text-sm font-semibold text-ink">默认透读模式</h3>
        <p className="mt-1 text-sm leading-6 text-muted">
          新建透读任务时，Web 输入页会优先带入这里保存的默认阅读目标与难度。
        </p>
      </div>

      <SegmentedControl
        label="阅读目标"
        value={draft.readingGoal}
        onValueChange={handleGoalChange}
        options={READING_GOAL_OPTIONS}
      />

      <SegmentedControl
        label="默认难度"
        value={draft.readingVariant}
        onValueChange={handleVariantChange}
        options={variantOptions}
      />

      <div className="flex flex-wrap items-center gap-3 border-t border-hairline pt-4">
        <Button
          variant="primary-ink"
          className="min-w-[128px] justify-center"
          disabled={!canEdit || !dirty || state.kind === "saving"}
          onClick={handleSave}
        >
          {state.kind === "saving" ? "保存中..." : "保存默认值"}
        </Button>
        <Button
          variant="ghost"
          className="min-w-[96px] justify-center"
          disabled={!dirty || state.kind === "saving"}
          onClick={handleReset}
        >
          取消
        </Button>
        <p className="text-xs leading-5 text-muted">
          {canEdit
            ? state.kind === "saved"
              ? state.message
              : state.kind === "error"
                ? state.message
                : "只影响 Web 默认带入值；提交前仍可在输入页临时切换。"
            : "当前会话未连接真实账户，无法保存共享默认值。"}
        </p>
      </div>
    </div>
  );
}
