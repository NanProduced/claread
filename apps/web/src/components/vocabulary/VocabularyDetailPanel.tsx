"use client";

import { Check, BookOpen, Trash2, Calendar, Play, Pause, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/primitives/button";
import { ScrollArea } from "@/components/primitives/scroll-area";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/primitives/tooltip";
import type { VocabularyItemVm } from "@/types/view/VocabularyItemVm";

function getReviewStatusLabel(item: VocabularyItemVm): string {
  if (item.mastered) return "已掌握";
  if (!item.nextReviewAt) return "待复习";
  const next = new Date(item.nextReviewAt).getTime();
  const now = Date.now();
  if (next <= now) return "今日复习";
  const diffDays = Math.ceil((next - now) / (24 * 60 * 60 * 1000));
  return `${diffDays}天后`;
}

function formatDate(value: string): string {
  return new Date(value).toLocaleDateString("zh-CN");
}

function formatNextReview(value: string): string {
  const next = new Date(value).getTime();
  const now = Date.now();
  if (next <= now) return "今天";
  const diffDays = Math.ceil((next - now) / (24 * 60 * 60 * 1000));
  if (diffDays === 1) return "明天";
  return formatDate(value);
}

export interface VocabularyDetailPanelProps {
  item: VocabularyItemVm;
  onToggleMastery?: (item: VocabularyItemVm) => void;
  onDelete?: (item: VocabularyItemVm) => void;
  onGoToSource?: (target: {
    readingRecordId?: string | null;
    sentenceId?: string;
  }) => void;
  onClose?: () => void;
}

export function VocabularyDetailPanel({
  item,
  onToggleMastery,
  onDelete,
  onGoToSource,
  onClose,
}: VocabularyDetailPanelProps) {
  const [audioPlaying, setAudioPlaying] = useState(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const sourceRefs = item.sourceRefs ?? [];
  const hasSourceRefs = sourceRefs.length > 0;
  const reviewLabel = getReviewStatusLabel(item);

  useEffect(() => {
    return () => {
      if (audioRef.current) {
        audioRef.current.pause();
        audioRef.current = null;
      }
    };
  }, []);

  const handlePlayAudio = useCallback(() => {
    if (!item.audioUrl) return;
    if (!audioRef.current) {
      audioRef.current = new Audio(item.audioUrl);
      audioRef.current.addEventListener("ended", () => setAudioPlaying(false));
      audioRef.current.addEventListener("error", () => setAudioPlaying(false));
    }
    if (audioPlaying) {
      audioRef.current.pause();
      setAudioPlaying(false);
    } else {
      audioRef.current.play();
      setAudioPlaying(true);
    }
  }, [item.audioUrl, audioPlaying]);

  const handleGoToRef = useCallback(
    (ref: (typeof sourceRefs)[number]) => {
      const readingRecordId = ref.reading_record_id ?? null;

      // Vocabulary can display legacy source metadata, but navigation is
      // fail-closed: only a Reading Record id may open the canonical Reader.
      if (!readingRecordId) return;

      onGoToSource?.({
        readingRecordId,
        sentenceId: ref.source_sentence_id ?? undefined,
      });
    },
    [onGoToSource],
  );

  type Tab = "meanings" | "phrases" | "forms";
  const [activeTab, setActiveTab] = useState<Tab>("meanings");

  const hasDetailMeanings = item.detailMeanings && item.detailMeanings.length > 0;
  
  const tabItems = [
    { id: "meanings", label: "释义", count: hasDetailMeanings || item.shortMeaning ? 1 : 0 },
    { id: "phrases", label: "搭配", count: item.detailPhrases?.length || 0 },
    { id: "forms", label: "词形", count: item.collectedForms?.length || 0 },
  ].filter((tab) => tab.count > 0);

  // Sync active tab if current tab becomes empty
  const [prevItemKey, setPrevItemKey] = useState(`${item.id}:${activeTab}:${tabItems.map(t => t.id).join(",")}`);
  const currentTabKey = `${item.id}:${activeTab}:${tabItems.map(t => t.id).join(",")}`;
  if (prevItemKey !== currentTabKey) {
    setPrevItemKey(currentTabKey);
    if (tabItems.length > 0 && !tabItems.some(t => t.id === activeTab)) {
      setActiveTab(tabItems[0].id as Tab);
    }
  }

  return (
    <ScrollArea className="h-full bg-surface">
      <div className="px-5 lg:px-8 pb-12 pt-6 lg:pt-10">
        {/* ── Layer 1: Header ── */}
        <div className="mb-5">
          <div className="flex items-start justify-between gap-4">
            <div className="flex flex-col gap-3 min-w-0 flex-1">
              <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1.5">
                <h2 className="font-headline text-[2.2rem] font-semibold leading-none tracking-tight text-ink">
                  {item.word}
                </h2>
                {item.phonetic && (
                  <span className="text-sm font-sans text-muted-foreground">{item.phonetic}</span>
                )}
                {item.partOfSpeech && (
                  <span className="rounded-pill border border-hairline/80 bg-surface/50 px-2 py-0.5 font-sans text-[0.75rem] font-semibold text-muted-foreground">
                    {item.partOfSpeech}
                  </span>
                )}
                {item.lemma && item.lemma.toLowerCase() !== item.word.toLowerCase() && (
                  <span className="text-[0.8rem] font-sans text-muted-foreground">
                    原形: {item.lemma}
                  </span>
                )}
                {item.audioUrl && (
                  <button
                    type="button"
                    onClick={handlePlayAudio}
                    className="inline-flex h-8 w-8 items-center justify-center rounded-full border border-hairline/80 bg-surface/50 text-muted-foreground transition-colors hover:bg-surface-warm hover:text-ink ml-1"
                    aria-label={audioPlaying ? "暂停" : "播放发音"}
                  >
                    {audioPlaying ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
                  </button>
                )}
              </div>
              
              {/* Status Tags */}
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[0.72rem] font-semibold tracking-[0.06em] text-muted-foreground">
                <span className="inline-flex items-center gap-1.5">
                  <Calendar className="h-3 w-3" />
                  {formatDate(item.createdAt)}
                </span>
                <span
                  className={`inline-flex items-center gap-1.5 rounded-pill border px-2 py-0.5 ${
                    item.mastered
                      ? "border-structure-green/30 bg-structure-green/10 text-structure-green"
                      : reviewLabel === "今日复习"
                        ? "border-vocab-amber/60 bg-vocab-amber/15 text-vocab-amber"
                        : "border-hairline/60 bg-surface/50 text-muted-foreground"
                  }`}
                >
                  {item.mastered && <Check className="h-3 w-3" />}
                  {reviewLabel}
                </span>
                {item.reviewCount != null && item.reviewCount > 0 && (
                  <span>复习 {item.reviewCount} 次</span>
                )}
                {item.nextReviewAt && !item.mastered && (
                  <span className="text-muted-foreground/70">下次: {formatNextReview(item.nextReviewAt)}</span>
                )}
              </div>
            </div>

            {/* Actions */}
            <div className="flex items-center gap-2 shrink-0">
              <Button
                variant={item.mastered ? "outline" : "primary-ink"}
                className={item.mastered 
                  ? "rounded-pill text-[0.78rem] font-semibold tracking-[0.04em]" 
                  : "relative overflow-hidden group rounded-pill text-[0.78rem] font-semibold tracking-[0.04em] shadow-[0_4px_12px_rgba(28,24,18,0.15)] transition-transform hover:scale-[1.02] active:scale-[0.98]"}
                onClick={() => onToggleMastery?.(item)}
              >
                <Check className="mr-1.5 h-3.5 w-3.5 relative z-10" />
                <span className="relative z-10">{item.mastered ? "取消掌握" : "标记已掌握"}</span>
                {!item.mastered && <div className="absolute inset-0 z-0 bg-white/20 blur-md rounded-full translate-x-[-100%] group-hover:animate-[shimmer_1.5s_infinite]" />}
              </Button>
              
              <TooltipProvider>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <button
                      type="button"
                      className="inline-flex h-9 w-9 items-center justify-center rounded-full text-muted-foreground transition-all hover:bg-surface-warm hover:text-error-red hover:font-bold outline-none"
                      onClick={() => onDelete?.(item)}
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </TooltipTrigger>
                  <TooltipContent>删除生词</TooltipContent>
                </Tooltip>

                {onClose && (
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <button
                        type="button"
                        onClick={onClose}
                        className="inline-flex h-9 w-9 items-center justify-center rounded-full text-muted-foreground transition-all hover:bg-surface-warm hover:text-ink hover:font-bold outline-none"
                      >
                        <X className="h-5 w-5" />
                      </button>
                    </TooltipTrigger>
                    <TooltipContent>关闭详情</TooltipContent>
                  </Tooltip>
                )}
              </TooltipProvider>
            </div>
          </div>
        </div>

        {/* ── Layer 2: Personal Context ── */}
        {(hasSourceRefs || item.contextSentence) && (
          <div className="mb-8">
            {hasSourceRefs ? (
              <div className="space-y-3">
                {sourceRefs.map((ref, i) => (
                  <div key={i} className="group relative rounded-[12px] border border-hairline bg-[linear-gradient(to_bottom,color-mix(in_srgb,var(--reader-paper)_30%,transparent),transparent)] p-4 pr-12 shadow-[0_2px_8px_rgba(28,24,18,0.02)]">
                    {ref.reading_record_id && (
                      <div className="absolute top-3 right-3">
                        <TooltipProvider>
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <button
                                type="button"
                                onClick={() => handleGoToRef(ref)}
                                className="inline-flex h-8 w-8 items-center justify-center rounded-full border border-hairline/60 bg-surface/80 text-lens-blue shadow-sm transition-all hover:scale-105 hover:bg-surface hover:text-lens-blue/80 hover:border-lens-blue/30 outline-none"
                              >
                                <BookOpen className="h-3.5 w-3.5" strokeWidth={2.5} />
                              </button>
                            </TooltipTrigger>
                            <TooltipContent>在原文中定位</TooltipContent>
                          </Tooltip>
                        </TooltipProvider>
                      </div>
                    )}
                    <div className="border-l-2 border-lens-blue/30 pl-3">
                      {item.sourceRecordTitle && (
                        <p className="mb-1.5 text-[0.65rem] font-bold tracking-[0.08em] text-subtle">
                          {item.sourceRecordTitle}
                        </p>
                      )}
                      {ref.source_sentence && (
                        <p className="font-serif text-[1.05rem] italic leading-[1.65] tracking-[0.01em] text-ink">
                          {ref.source_sentence}
                        </p>
                      )}
                      {ref.source_context && (
                        <p className="mt-2 font-reading text-[0.88rem] leading-relaxed text-ink-soft/80">
                          {ref.source_context}
                        </p>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            ) : item.contextSentence ? (
              <div className="group relative rounded-[12px] border border-hairline bg-[linear-gradient(to_bottom,color-mix(in_srgb,var(--reader-paper)_30%,transparent),transparent)] p-4 shadow-[0_2px_8px_rgba(28,24,18,0.02)]">
                <div className="border-l-2 border-lens-blue/30 pl-3">
                  <p className="font-serif text-[1.05rem] italic leading-[1.65] tracking-[0.01em] text-ink">
                    {item.contextSentence}
                  </p>
                  {item.contextTranslation && (
                    <p className="mt-2 font-reading text-[0.88rem] leading-relaxed text-ink-soft/80">
                      {item.contextTranslation}
                    </p>
                  )}
                </div>
              </div>
            ) : null}
          </div>
        )}

        {/* ── Layer 3: Dictionary Tools ── */}
        <div>
          
          {/* Custom Tabs List */}
          {tabItems.length > 1 && (
            <div className="flex gap-6 border-b border-hairline/60 mb-6">
              {tabItems.map((tab) => (
                <button
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id as Tab)}
                  className={`pb-3 text-[0.88rem] font-semibold transition-colors border-b-2 outline-none ${
                    activeTab === tab.id
                      ? "border-ink text-ink"
                      : "border-transparent text-muted-foreground hover:text-ink-soft"
                  }`}
                >
                  {tab.label}
                </button>
              ))}
            </div>
          )}

          {/* Meanings Tab */}
          {activeTab === "meanings" && (
            <div className="animate-in fade-in slide-in-from-bottom-2 duration-300">
              {hasDetailMeanings ? (
                <div className="space-y-8">
                  {item.detailMeanings!.map((group, gi) => (
                    <div key={gi}>
                      {group.partOfSpeech && (
                        <span className="inline-flex mb-4 px-2 py-0.5 rounded-[6px] bg-ink/[0.03] text-muted-foreground text-[0.75rem] font-mono font-semibold leading-none border border-hairline/50">
                          {group.partOfSpeech}
                        </span>
                      )}
                      <ol className="space-y-6">
                        {group.definitions.map((def, di) => (
                          <li key={di} className="flex flex-col">
                            <div className="flex items-start gap-3">
                              <span className="shrink-0 w-[1.35rem] h-[1.35rem] rounded-full border border-hairline/80 bg-ink/[0.02] text-ink-soft text-[0.72rem] font-semibold flex items-center justify-center mt-0.5 select-none">
                                {di + 1}
                              </span>
                              <div className="flex-1 min-w-0 select-text">
                                <p className="text-[1rem] font-semibold text-ink leading-snug">
                                  {def.meaning}
                                </p>
                              </div>
                            </div>
                            {def.example && (
                              <div className="mt-3 ml-2.5 pl-5 border-l-2 border-hairline/50 space-y-1">
                                <p className="font-serif italic text-[0.95rem] leading-relaxed text-ink-soft/90">
                                  {def.example}
                                </p>
                                {def.exampleTranslation && (
                                  <p className="text-[0.85rem] leading-normal text-muted-foreground/95">
                                    {def.exampleTranslation}
                                  </p>
                                )}
                              </div>
                            )}
                          </li>
                        ))}
                      </ol>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-[1rem] font-semibold leading-relaxed text-ink-soft">
                  {item.shortMeaning ?? "暂无释义"}
                </p>
              )}
            </div>
          )}

          {/* Phrases Tab */}
          {activeTab === "phrases" && (
            <div className="animate-in fade-in slide-in-from-bottom-2 duration-300">
              <div className="rounded-[8px] border border-hairline/75 bg-ink/[0.01] px-5 py-2">
                {item.detailPhrases?.map((p, i) => (
                  <div key={i} className="border-b border-hairline/55 py-4 last:border-b-0">
                    <p className="text-[0.95rem] font-semibold leading-6 text-ink">{p.phrase}</p>
                    {p.meaning && (
                      <p className="mt-1.5 text-[0.85rem] leading-6 text-ink-soft/82">{p.meaning}</p>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Forms Tab */}
          {activeTab === "forms" && (
            <div className="animate-in fade-in slide-in-from-bottom-2 duration-300">
              <div className="flex flex-wrap gap-2">
                {item.collectedForms?.map((form, i) => (
                  <span
                    key={i}
                    className="rounded-[6px] border border-hairline bg-surface px-3 py-1 text-[0.82rem] font-semibold text-ink-soft hover:border-vocab-amber/40 transition-colors select-all"
                  >
                    {form}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </ScrollArea>
  );
}
