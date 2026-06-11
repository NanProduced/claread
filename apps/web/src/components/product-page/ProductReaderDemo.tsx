"use client";

import { useMemo, useState } from "react";
import { BookOpenText, Check, Route, ScanText } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { PlateReaderSurface } from "@/components/reader/plate";
import { renderSceneToPlateDocument } from "@/lib/reader-plate/projection";
import {
  productDemoExamVariants,
  productDemoGoals,
  productDemoScenes,
  productDemoSceneState,
  type ProductDemoExamVariant,
  type ProductDemoGoalId,
  type ProductDemoSceneId,
} from "@/lib/product-page/reader-demo-scenes";

function goalScene(goalId: ProductDemoGoalId, examVariant: ProductDemoExamVariant): ProductDemoSceneId {
  if (goalId === "exam") {
    return productDemoExamVariants.find((variant) => variant.id === examVariant)?.sceneId ?? "exam_cet";
  }

  return productDemoGoals.find((goal) => goal.id === goalId)?.sceneId ?? "exam_cet";
}

export function ProductReaderDemo() {
  const [goalId, setGoalId] = useState<ProductDemoGoalId>("exam");
  const [examVariant, setExamVariant] = useState<ProductDemoExamVariant>("cet");
  const sceneId = goalScene(goalId, examVariant);
  const scene = productDemoScenes[sceneId];
  const sceneState = productDemoSceneState[sceneId];
  const [expandedEntryIds, setExpandedEntryIds] = useState(sceneState.expandedEntryIds);
  const [activeEntryId, setActiveEntryId] = useState(sceneState.expandedEntryIds[0] ?? null);
  const [hoveredTargetKey, setHoveredTargetKey] = useState<string | null>(null);

  const document = useMemo(() => renderSceneToPlateDocument(scene), [scene]);
  const activeGoal = productDemoGoals.find((goal) => goal.id === goalId) ?? productDemoGoals[1];
  const demoStrengths: Array<{ title: string; copy: string; icon: LucideIcon }> = [
    { title: "原文优先", copy: "句子始终可见，解释不脱离上下文。", icon: BookOpenText },
    { title: "按目标切换", copy: activeGoal.description, icon: Route },
    { title: "可回源标注", copy: "每个词义、结构和逻辑笔记都能回到原文位置。", icon: ScanText },
  ];

  function selectGoal(nextGoalId: ProductDemoGoalId) {
    const nextSceneId = goalScene(nextGoalId, examVariant);
    const nextState = productDemoSceneState[nextSceneId];
    setGoalId(nextGoalId);
    setExpandedEntryIds(nextState.expandedEntryIds);
    setActiveEntryId(nextState.expandedEntryIds[0] ?? null);
  }

  function selectExamVariant(nextVariant: ProductDemoExamVariant) {
    const nextSceneId = goalScene("exam", nextVariant);
    const nextState = productDemoSceneState[nextSceneId];
    setGoalId("exam");
    setExamVariant(nextVariant);
    setExpandedEntryIds(nextState.expandedEntryIds);
    setActiveEntryId(nextState.expandedEntryIds[0] ?? null);
  }

  function toggleEntry(entryId: string) {
    setActiveEntryId(entryId);
    setExpandedEntryIds((current) =>
      current.includes(entryId)
        ? current.filter((id) => id !== entryId)
        : [...current, entryId],
    );
  }

  return (
    <section id="reader-demo" className="relative mx-auto max-w-7xl px-5 py-20 sm:px-6 lg:py-28">
      <div className="grid gap-10 lg:grid-cols-[0.78fr_1.22fr] lg:items-start">
        <div className="lg:sticky lg:top-8">
          <p className="text-sm font-semibold text-lens-blue">Goal-Based Reader Demo</p>
          <h2 className="mt-4 max-w-xl font-headline text-4xl font-semibold leading-[1.08] text-ink sm:text-5xl">
            同一篇英文，按阅读目标展开不同解释。
          </h2>
          <p className="mt-5 max-w-lg text-base leading-8 text-muted">
            Claread 不把文章压成一句答案。它先保留原文，再把词义、句法、题目信号和学术逻辑锚定到具体句子上。
          </p>

          <div className="mt-8 space-y-4">
            <div className="flex flex-wrap gap-2" role="tablist" aria-label="阅读目标">
              {productDemoGoals.map((goal) => {
                const active = goal.id === goalId;

                return (
                  <button
                    key={goal.id}
                    type="button"
                    role="tab"
                    aria-selected={active}
                    onClick={() => selectGoal(goal.id)}
                    style={active ? { color: "#ffffff" } : undefined}
                    className={`focus-ring inline-flex min-h-10 items-center rounded-pill border px-4 text-sm font-semibold transition ${
                      active
                        ? "border-lens-blue bg-lens-blue text-[rgb(255,255,255)] shadow-[0_12px_28px_rgba(40,92,255,0.18)]"
                        : "border-hairline bg-surface/70 text-ink-soft hover:border-lens-blue/40 hover:text-ink"
                    }`}
                  >
                    {goal.label}
                  </button>
                );
              })}
            </div>

            {goalId === "exam" ? (
              <div className="flex flex-wrap gap-2" aria-label="考试类型">
                {productDemoExamVariants.map((variant) => {
                  const active = variant.id === examVariant;

                  return (
                    <button
                      key={variant.id}
                      type="button"
                      onClick={() => selectExamVariant(variant.id)}
                      style={active ? { color: "#ffffff" } : undefined}
                      className={`focus-ring inline-flex min-h-9 items-center rounded-md border px-3 text-xs font-semibold transition ${
                        active
                          ? "border-ink bg-ink text-[rgb(255,255,255)]"
                          : "border-hairline bg-reader-paper/70 text-muted hover:text-ink"
                      }`}
                    >
                      {variant.label}
                    </button>
                  );
                })}
              </div>
            ) : null}
          </div>

          <div className="mt-8 grid gap-3 text-sm leading-6 text-ink-soft">
            {demoStrengths.map(({ title, copy, icon: Icon }) => (
              <div key={title} className="flex gap-3">
                <span className="mt-0.5 inline-flex h-7 w-7 flex-none items-center justify-center rounded-md bg-lens-blue-soft text-lens-blue">
                  <Icon aria-hidden="true" className="h-4 w-4" />
                </span>
                <span>
                  <strong className="font-semibold text-ink">{title}</strong>
                  <span className="ml-2 text-muted">{copy}</span>
                </span>
              </div>
            ))}
          </div>
        </div>

        <div className="relative">
          <div className="absolute -left-6 -top-6 hidden h-28 w-28 rounded-full border border-lens-blue/20 bg-lens-blue-soft/50 lg:block" />
          <div className="relative overflow-hidden rounded-[1.75rem] border border-hairline bg-reader-paper shadow-[0_14px_44px_rgba(28,24,18,0.12)] paper-grain">
            <div className="flex items-center justify-between border-b border-hairline/60 px-4 py-3 sm:px-5">
              <div className="flex items-center gap-2 text-xs font-semibold text-muted">
                <Check aria-hidden="true" className="h-3.5 w-3.5 text-lens-blue" />
                Reader Workspace
              </div>
            </div>
            <div className="max-h-[640px] overflow-y-auto">
              <PlateReaderSurface
                activeAnalysisEntryId={activeEntryId}
                activeSentenceId={sceneState.selectedSentenceId}
                columnClassName="max-w-[64ch]"
                document={document}
                expandedAnalysisEntryIds={expandedEntryIds}
                hoveredAnnotationTargetKey={hoveredTargetKey}
                onAnalysisFocusChange={(entryId, focused) => {
                  if (focused) setActiveEntryId(entryId);
                }}
                onAnalysisToggle={toggleEntry}
                onHoverAnnotationTargetKeyChange={setHoveredTargetKey}
                paragraphDensityClassName="reader-density-intensive"
                readingClassName="reader-font-reading text-[1.02rem] leading-[1.9] text-ink"
                showTranslation
                themeClassName="reader-shell--intensive"
                translationClassName="reader-font-sans text-[0.82rem] leading-[1.7]"
              />
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
