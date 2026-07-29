"use client";

import { useEffect, useState } from "react";
import { AlertCircle, CheckCircle2 } from "lucide-react";

import { ReadingPlanFields } from "@/components/composed";
import { Button } from "@/components/primitives/button";
import {
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
      setState({ kind: "saved", message: "默认阅读方案已保存。" });
    } catch {
      setState({ kind: "error", message: "网络异常，暂时无法保存默认阅读方案。" });
    }
  }

  function handlePlanChange(nextPlan: ReadingDefaultState) {
    setDraft(nextPlan);
    if (state.kind !== "idle") setState({ kind: "idle" });
  }

  function handleReset() {
    setDraft(saved);
    setState({ kind: "idle" });
  }

  return (
    <div>
      <ReadingPlanFields
        value={draft}
        onValueChange={handlePlanChange}
        layout="settings"
        disabled={!canEdit || state.kind === "saving"}
        idPrefix="settings-reading-plan"
      />

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
