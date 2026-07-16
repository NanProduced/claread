"use client";

import { useEffect, useMemo, useState } from "react";
import { CheckCircle2, AlertCircle } from "lucide-react";

import { SegmentedControl } from "@/components/composed";
import { Button } from "@/components/primitives/button";
import { Alert, AlertDescription } from "@/components/primitives/alert";
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
    <div className="space-y-6">

      <SegmentedControl
        label="阅读目标"
        value={draft.readingGoal}
        onValueChange={handleGoalChange}
        options={READING_GOAL_OPTIONS}
      />

      <SegmentedControl
        label="解析模式"
        value={draft.readingVariant}
        onValueChange={handleVariantChange}
        options={variantOptions}
      />
      
      <div className="flex flex-wrap items-center gap-3 pt-4">
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
      </div>
      
      {/* Feedback Messages */}
      {canEdit && (state.kind === "saved" || state.kind === "error") && (
        <div className="pt-2 max-w-sm transition-all animate-in fade-in slide-in-from-top-1 duration-300">
          <Alert 
            variant={state.kind === "error" ? "destructive" : "default"} 
            className={`py-2 px-3 flex items-center ${state.kind === "saved" ? "border-structure-green/30 bg-structure-green/5 text-structure-green [&>svg]:text-structure-green" : ""}`}
          >
            {state.kind === "saved" ? <CheckCircle2 className="h-4 w-4" /> : <AlertCircle className="h-4 w-4" />}
            <AlertDescription className="text-xs font-medium ml-1">
              {state.message}
            </AlertDescription>
          </Alert>
        </div>
      )}
      
      {!canEdit && (
        <div className="pt-2">
          <p className="text-xs leading-5 text-muted-foreground">
            当前会话未连接真实账户，无法保存共享默认值。
          </p>
        </div>
      )}
    </div>
  );
}
