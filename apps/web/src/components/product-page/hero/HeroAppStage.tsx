"use client";

import Image from "next/image";
import {
  ArrowRight,
  BookMarked,
  BookOpen,
  Calendar,
  Check,
  ChevronsLeft,
  ClipboardPaste,
  Compass,
  Eye,
  FileText,
  FileUp,
  Globe,
  Heart,
  Link2,
  NotebookPen,
  Play,
  Plus,
  Search,
  Settings,
  SlidersHorizontal,
  Sparkles,
  X,
} from "lucide-react";
import {
  useEffect,
  useCallback,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
  type PointerEvent as ReactPointerEvent,
  type MouseEvent as ReactMouseEvent,
} from "react";

import type { DictionaryLookupSnapshot } from "@/components/reader/dictionary/contracts";
import { firstMeaning } from "@/components/reader/dictionary/contracts";
import { SelectionToolbar } from "@/components/reader/SelectionToolbar";
import {
  ImmersiveReaderSurface,
  IntensiveReaderSurface,
} from "@/components/reader/plate";
import type {
  ReaderLookupIntent,
  ReaderLookupPreviewAnchor,
  ReaderStructuredInspectIntent,
} from "@/lib/reader-plate/bridges/dictionary";
import { renderSceneToPlateDocument } from "@/lib/reader-plate/projection";
import {
  heroComposeText,
  heroDefaultRecord,
  heroImmersiveDensityClassName,
  heroImmersiveReadingClassName,
  heroReaderColumnClassName,
  heroReaderDensityClassName,
  heroReadingClassName,
  heroTranslationClassName,
  type HeroReaderMode,
  type HeroReaderRecord,
} from "@/lib/hero/hero-app-stage-data";
import { getReadingGoalOption } from "@/lib/reading-defaults";
import { buildHeroLookupFromIntent, buildHeroLookupFromMarkId } from "@/lib/hero/hero-lookups";

type HeroAppView = "compose" | "reader" | "library" | "vocabulary";
type HeroFloatingPlacement = "top" | "bottom";

interface HeroFloatingPosition {
  left: number;
  top: number;
  placement: HeroFloatingPlacement;
}

function isSameFloatingPosition(a: HeroFloatingPosition, b: HeroFloatingPosition) {
  return (
    a.placement === b.placement &&
    Math.abs(a.left - b.left) < 0.5 &&
    Math.abs(a.top - b.top) < 0.5
  );
}

interface HeroRecordListItem {
  id: string;
  title: string;
  statusLabel: string;
  goalLabel: string;
  variantLabel: string;
  sourceLabel: string;
  excerpt: string;
  dateLabel: string;
  wordCount: number;
  noteCount: number;
  vocabularyCount: number;
  favorited: boolean;
}

interface HeroVocabularyItem {
  id: string;
  word: string;
  phonetic: string;
  partOfSpeech: string;
  shortMeaning: string;
  contextSentence: string;
  contextTranslation: string;
  reviewLabel: string;
  sourceCount: string;
  mastered?: boolean;
}

interface HeroDailyPick {
  id: string;
  title: string;
  source: string;
  difficulty: string;
  readTime: string;
  subtitle: string;
  tags: string[];
}

const railItems: Array<{
  id?: HeroAppView;
  label: string;
  icon: React.ElementType;
}> = [
  { id: "compose", label: "新解读", icon: Plus },
  { id: "reader", label: "解析页", icon: BookOpen },
  { id: "library", label: "阅读记录", icon: FileText },
  { id: "vocabulary", label: "生词本", icon: BookMarked },
  { label: "设置", icon: Settings },
];

const intakeMethods = [
  { label: "贴入文本", icon: ClipboardPaste, active: true },
  { label: "链接导入", icon: Link2, active: false },
  { label: "上传文档", icon: FileUp, active: false },
  { label: "示例文章", icon: BookOpen, active: false },
];

const goalOptions = [
  {
    id: "daily_reading",
    label: getReadingGoalOption("daily_reading")?.label ?? "日常阅读",
    desc:
      getReadingGoalOption("daily_reading")?.description ??
      "兼顾理解、词汇与表达积累，适合持续阅读。",
    icon: BookOpen,
  },
  {
    id: "exam",
    label: getReadingGoalOption("exam")?.label ?? "备考精读",
    desc:
      getReadingGoalOption("exam")?.description ??
      "围绕考试要求，突出长难句、考点与题感。",
    icon: Sparkles,
  },
];

const heroDailyPicks: HeroDailyPick[] = [
  {
    id: "family-policy",
    title: "Why chronic absence became a policy problem",
    source: "Education Week",
    difficulty: "CET-6",
    readTime: "5 min read",
    subtitle: "A short education article about attendance, family pressure, and school support.",
    tags: ["education", "policy", "long sentence"],
  },
  {
    id: "summary-reading",
    title: "What students lose when summaries replace reading",
    source: "The Atlantic",
    difficulty: "Intermediate",
    readTime: "6 min read",
    subtitle: "A column on why original sentences still matter in an AI reading workflow.",
    tags: ["reading", "AI"],
  },
  {
    id: "work-writing",
    title: "Remote work changed the way teams write",
    source: "HBR",
    difficulty: "Academic",
    readTime: "7 min read",
    subtitle: "A practical article with dense noun phrases and contrast markers.",
    tags: ["work", "writing"],
  },
];

const heroRecords: HeroRecordListItem[] = [
  {
    id: "education-policy",
    title: "旷课背后的家庭危机与教育政策反思",
    statusLabel: "解析结果",
    goalLabel: "备考精读",
    variantLabel: "四六级",
    sourceLabel: "粘贴导入",
    excerpt:
      "Nationally, one in six children miss 15 or more days of school in a year. Education officials have deplored all this missed instruction.",
    dateLabel: "2026年6月10日",
    wordCount: 146,
    noteCount: 3,
    vocabularyCount: 7,
    favorited: true,
  },
  {
    id: "academic-absence",
    title: "Chronic absence and the hidden cost of missed instruction",
    statusLabel: "解析结果",
    goalLabel: "日常阅读",
    variantLabel: "精读",
    sourceLabel: "网页导入",
    excerpt:
      "Missing 10% of school days in a year is a primary cause of low academic achievement.",
    dateLabel: "2026年6月8日",
    wordCount: 132,
    noteCount: 2,
    vocabularyCount: 5,
    favorited: false,
  },
];

const heroVocabularyItems: HeroVocabularyItem[] = [
  {
    id: "nationally",
    word: "nationally",
    phonetic: "/ˈnæʃənəli/",
    partOfSpeech: "adv.",
    shortMeaning: "在全国范围内",
    contextSentence: "Nationally, one in six children miss 15 or more days of school in a year.",
    contextTranslation: "在全国范围内，每六个儿童中就有一个在一年内缺勤 15 天或更多。",
    reviewLabel: "今日复习",
    sourceCount: "2 个语境",
  },
  {
    id: "deplored",
    word: "deplored",
    phonetic: "/dɪˈplɔːrd/",
    partOfSpeech: "v.",
    shortMeaning: "强烈反对，痛惜",
    contextSentence: "Education officials have deplored all this missed instruction.",
    contextTranslation: "教育官员对所有这些缺失的教学活动表示痛惜。",
    reviewLabel: "学习中",
    sourceCount: "1 个语境",
  },
  {
    id: "chronically",
    word: "chronically",
    phonetic: "/ˈkrɑːnɪkli/",
    partOfSpeech: "adv.",
    shortMeaning: "长期地，反复地",
    contextSentence: "These chronically absent students suffer academically.",
    contextTranslation: "这些长期缺勤的学生在学业上遭受损失。",
    reviewLabel: "3天后",
    sourceCount: "3 个语境",
  },
  {
    id: "miss-out-on",
    word: "miss out on",
    phonetic: "",
    partOfSpeech: "phr.",
    shortMeaning: "错过，失去获得某事的机会",
    contextSentence: "They miss out on classroom instruction.",
    contextTranslation: "他们错过了课堂教学。",
    reviewLabel: "学习中",
    sourceCount: "2 个语境",
  },
];

interface HeroAppStageProps {
  className?: string;
  interactive?: boolean;
  variant?: "landing" | "device";
}

export function HeroAppStage({
  className,
  interactive = true,
  variant = "landing",
}: HeroAppStageProps = {}) {
  const stageRef = useRef<HTMLDivElement | null>(null);
  const stageWindowRef = useRef<HTMLDivElement | null>(null);
  const [view, setView] = useState<HeroAppView>("reader");
  const [readerMode, setReaderMode] = useState<HeroReaderMode>("intensive");
  const [selectedVocabularyId, setSelectedVocabularyId] = useState(heroVocabularyItems[0].id);
  const [selectedGoal, setSelectedGoal] = useState("exam");
  const [expandedEntryIds, setExpandedEntryIds] = useState<string[]>(
    heroDefaultRecord.expandedEntryIds,
  );
  const [peek, setPeek] = useState<{
    anchorElement: HTMLElement;
    snapshot?: DictionaryLookupSnapshot;
    inspect?: ReaderStructuredInspectIntent;
    position: HeroFloatingPosition;
  } | null>(null);
  const [selectionToolbar, setSelectionToolbar] = useState<{
    selectedText: string;
    position: HeroFloatingPosition;
  } | null>(null);

  const activeRecord = heroDefaultRecord;
  const selectedVocabulary =
    heroVocabularyItems.find((item) => item.id === selectedVocabularyId) ?? heroVocabularyItems[0];
  const plateDocument = useMemo(
    () => renderSceneToPlateDocument(activeRecord.scene),
    [activeRecord],
  );

  const floatingSafeTopForContainer = useCallback((containerRect: DOMRect) => {
    const headerRect = stageWindowRef.current
      ?.querySelector(".hero-reader-header")
      ?.getBoundingClientRect();
    if (!headerRect) {
      return 12;
    }

    return Math.max(12, headerRect.bottom - containerRect.top + 14);
  }, []);

  function activateView(nextView: HeroAppView) {
    setView(nextView);
    setPeek(null);
    setSelectionToolbar(null);
  }

  const floatingPositionForRect = useCallback(
    (
      rect: DOMRect,
      {
        offset = 10,
        panelHeight = 226,
        panelWidth = 376,
        preferredPlacement = "bottom",
      }: {
        offset?: number;
        panelHeight?: number;
        panelWidth?: number;
        preferredPlacement?: HeroFloatingPlacement;
      } = {},
    ): HeroFloatingPosition => {
      const containerRect = stageWindowRef.current?.getBoundingClientRect();
      if (!containerRect) {
        return {
          left: rect.left + rect.width / 2,
          placement: preferredPlacement,
          top: preferredPlacement === "top" ? rect.top - offset : rect.bottom + offset,
        };
      }

      const margin = 12;
      const safeTop = floatingSafeTopForContainer(containerRect);
      const safeBottom = containerRect.height - margin;
      if (safeBottom - safeTop < panelHeight) {
        return {
          left: rect.left + rect.width / 2 - containerRect.left,
          placement: preferredPlacement,
          top: safeTop,
        };
      }
      const halfPanelWidth = Math.min(panelWidth, containerRect.width - margin * 2) / 2;
      const center = rect.left + rect.width / 2 - containerRect.left;
      const left = Math.min(
        Math.max(center, halfPanelWidth + margin),
        containerRect.width - halfPanelWidth - margin,
      );

      const topAnchor = rect.top - containerRect.top - offset;
      const bottomTop = rect.bottom - containerRect.top + offset;
      const hasRoomAbove = topAnchor - panelHeight >= safeTop;
      const hasRoomBelow = bottomTop + panelHeight <= safeBottom;
      const shouldPlaceAbove =
        preferredPlacement === "top" ? hasRoomAbove || !hasRoomBelow : !hasRoomBelow && hasRoomAbove;

      if (shouldPlaceAbove) {
        return { left, placement: "top", top: Math.max(topAnchor, safeTop + panelHeight) };
      }

      return {
        left,
        placement: "bottom",
        top: Math.min(Math.max(bottomTop, safeTop), safeBottom - panelHeight),
      };
    },
    [floatingSafeTopForContainer],
  );

  function floatingTransform(position: HeroFloatingPosition) {
    return position.placement === "top" ? "translate(-50%, -100%)" : "translateX(-50%)";
  }

  useEffect(() => {
    if (!peek) {
      return;
    }

    const onPointer = (event: PointerEvent) => {
      const target = event.target as Node | null;
      const peekNode = stageWindowRef.current?.querySelector(".hero-app-quick-peek");
      if (target && peekNode?.contains(target)) {
        return;
      }
      setPeek(null);
    };

    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setPeek(null);
      }
    };

    const timeoutId = window.setTimeout(() => {
      document.addEventListener("pointerdown", onPointer);
      document.addEventListener("keydown", onKey);
    }, 0);

    return () => {
      window.clearTimeout(timeoutId);
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [peek]);

  useEffect(() => {
    if (!peek && !selectionToolbar) {
      return;
    }

    let animationFrameId = 0;

    const isRectInsideStage = (rect: DOMRect) => {
      const containerRect = stageWindowRef.current?.getBoundingClientRect();
      if (!containerRect || rect.width === 0 || rect.height === 0) {
        return false;
      }

      const safeTop = floatingSafeTopForContainer(containerRect);

      return (
        rect.right >= containerRect.left &&
        rect.left <= containerRect.right &&
        rect.bottom >= containerRect.top &&
        rect.top <= containerRect.bottom &&
        rect.bottom >= containerRect.top + safeTop
      );
    };

    const updateFloatingPositions = () => {
      animationFrameId = 0;

      setPeek((current) => {
        if (!current) {
          return current;
        }

        if (!stageWindowRef.current?.contains(current.anchorElement)) {
          return null;
        }

        const rect = current.anchorElement.getBoundingClientRect();
        if (!isRectInsideStage(rect)) {
          return null;
        }

        const nextPosition = floatingPositionForRect(rect, {
          panelHeight: 226,
          panelWidth: 376,
          preferredPlacement: "bottom",
        });

        if (isSameFloatingPosition(current.position, nextPosition)) {
          return current;
        }

        return { ...current, position: nextPosition };
      });

      setSelectionToolbar((current) => {
        if (!current) {
          return current;
        }

        const selection = window.getSelection();
        const selectedText = selection?.toString().trim() ?? "";
        if (!selection || selection.isCollapsed || selectedText.length < 2 || selection.rangeCount === 0) {
          return null;
        }

        const rangeRect = selection.getRangeAt(0).getBoundingClientRect();
        if (!isRectInsideStage(rangeRect)) {
          return null;
        }

        const nextPosition = floatingPositionForRect(rangeRect, {
          offset: 8,
          panelHeight: 46,
          panelWidth: 390,
          preferredPlacement: "top",
        });

        if (current.selectedText === selectedText && isSameFloatingPosition(current.position, nextPosition)) {
          return current;
        }

        return { ...current, selectedText, position: nextPosition };
      });
    };

    const scheduleUpdate = () => {
      if (animationFrameId) {
        return;
      }

      animationFrameId = window.requestAnimationFrame(updateFloatingPositions);
    };

    const stageWindow = stageWindowRef.current;
    stageWindow?.addEventListener("scroll", scheduleUpdate, true);
    window.addEventListener("resize", scheduleUpdate);
    window.visualViewport?.addEventListener("resize", scheduleUpdate);
    window.visualViewport?.addEventListener("scroll", scheduleUpdate);

    return () => {
      if (animationFrameId) {
        window.cancelAnimationFrame(animationFrameId);
      }
      stageWindow?.removeEventListener("scroll", scheduleUpdate, true);
      window.removeEventListener("resize", scheduleUpdate);
      window.visualViewport?.removeEventListener("resize", scheduleUpdate);
      window.visualViewport?.removeEventListener("scroll", scheduleUpdate);
    };
  }, [floatingPositionForRect, floatingSafeTopForContainer, peek, selectionToolbar]);

  function handleLookupIntent(
    intent: ReaderLookupIntent,
    _anchor: ReaderLookupPreviewAnchor | null,
    triggerEl?: HTMLElement | null,
  ) {
    if (!triggerEl) {
      return;
    }
    const snapshot = buildHeroLookupFromIntent(intent);
    if (!snapshot) {
      return;
    }

    setPeek({
      anchorElement: triggerEl,
      snapshot,
      position: floatingPositionForRect(triggerEl.getBoundingClientRect(), {
        panelHeight: 226,
        panelWidth: 376,
        preferredPlacement: "bottom",
      }),
    });
  }

  function handleInspectIntent(
    intent: ReaderStructuredInspectIntent,
    _anchor: ReaderLookupPreviewAnchor | null,
    triggerEl?: HTMLElement | null,
  ) {
    if (!triggerEl) {
      return;
    }

    setPeek({
      anchorElement: triggerEl,
      inspect: intent,
      position: floatingPositionForRect(triggerEl.getBoundingClientRect(), {
        panelHeight: 226,
        panelWidth: 376,
        preferredPlacement: "bottom",
      }),
    });
  }

  function handleStagePointerDownCapture(event: ReactPointerEvent<HTMLDivElement>) {
    if (!interactive) {
      return;
    }

    const target = event.target;
    const targetEl =
      target instanceof Element
        ? target
        : target instanceof Node
          ? target.parentElement
          : null;
    if (!targetEl) {
      return;
    }

    if (targetEl.closest(".hero-app-selection-toolbar")) {
      return;
    }

    const markEl = targetEl.closest<HTMLElement>(
      ".reader-mark--interactive[data-reader-mark-id]",
    );
    if (!markEl || !stageRef.current?.contains(markEl)) {
      return;
    }

    const markId = markEl.dataset.readerMarkId;
    const snapshot = markId ? buildHeroLookupFromMarkId(markId, activeRecord.scene) : null;
    if (!snapshot) {
      return;
    }

    setSelectionToolbar(null);
    setPeek({
      anchorElement: markEl,
      snapshot,
      position: floatingPositionForRect(markEl.getBoundingClientRect(), {
        panelHeight: 226,
        panelWidth: 376,
        preferredPlacement: "bottom",
      }),
    });
  }

  function handleStageMouseUpCapture(event: ReactMouseEvent<HTMLDivElement>) {
    if (!interactive) {
      return;
    }

    if (view !== "reader") {
      return;
    }
    const target = event.target;
    const targetEl =
      target instanceof Element
        ? target
        : target instanceof Node
          ? target.parentElement
          : null;
    if (!targetEl?.closest(".reader-reading-stage")) {
      return;
    }
    if (targetEl?.closest("button,a,[role='dialog'],.hero-app-selection-toolbar")) {
      return;
    }

    window.setTimeout(() => {
      const selection = window.getSelection();
      const selectedText = selection?.toString().trim() ?? "";
      if (!selection || selection.isCollapsed || selectedText.length < 2 || selection.rangeCount === 0) {
        setSelectionToolbar(null);
        return;
      }

      const range = selection.getRangeAt(0);
      const rangeRect = range.getBoundingClientRect();
      const containerRect = stageWindowRef.current?.getBoundingClientRect();
      if (!containerRect || rangeRect.width === 0 || rangeRect.height === 0) {
        setSelectionToolbar(null);
        return;
      }
      if (
        rangeRect.right < containerRect.left ||
        rangeRect.left > containerRect.right ||
        rangeRect.bottom < containerRect.top ||
        rangeRect.top > containerRect.bottom
      ) {
        setSelectionToolbar(null);
        return;
      }

      setPeek(null);
      setSelectionToolbar({
        selectedText,
        position: floatingPositionForRect(rangeRect, {
          offset: 8,
          panelHeight: 46,
          panelWidth: 390,
          preferredPlacement: "top",
        }),
      });
    }, 0);
  }

  function toggleAnalysisEntry(entryId: string) {
    setExpandedEntryIds((current) =>
      current.includes(entryId) ? current.filter((id) => id !== entryId) : [...current, entryId],
    );
  }

  return (
    <div
      ref={stageRef}
      data-hero-app-stage={variant}
      onPointerDownCapture={handleStagePointerDownCapture}
      onMouseUpCapture={handleStageMouseUpCapture}
      className={`${
        variant === "device"
          ? "relative h-full w-full overflow-hidden"
          : "relative z-20 mx-auto mt-16 hidden w-[min(94vw,1440px)] lg:block xl:mt-20"
      } ${interactive ? "" : "pointer-events-none select-none"} ${className ?? ""}`}
      inert={interactive ? undefined : true}
    >
      <HeroAppStageStyles />
      <div
        ref={stageWindowRef}
        className={`hero-app-window relative overflow-hidden bg-[#F7F5F0] text-ink ${
          variant === "device"
            ? "h-full rounded-none border-0 shadow-none"
            : "h-[690px] rounded-[18px] border border-hairline/80 xl:h-[750px] 2xl:h-[800px]"
        }`}
      >
        <div className="grid h-full grid-cols-[76px_minmax(0,1fr)]">
          <HeroSidebar view={view} onActivateView={activateView} />
          <div className="relative min-w-0 overflow-hidden bg-reader-paper">
            {view === "compose" ? (
              <HeroComposeView
                selectedGoal={selectedGoal}
                onGoalChange={setSelectedGoal}
                onStart={() => activateView("reader")}
              />
            ) : view === "library" ? (
              <HeroLibraryView records={heroRecords} onOpenReader={() => activateView("reader")} />
            ) : view === "vocabulary" ? (
              <HeroVocabularyView
                items={heroVocabularyItems}
                selectedItem={selectedVocabulary}
                selectedId={selectedVocabularyId}
                onSelectItem={setSelectedVocabularyId}
                onOpenSource={() => activateView("reader")}
              />
            ) : (
              <HeroReaderView
                record={activeRecord}
                isFavorited={activeRecord.isFavorited}
                mode={readerMode}
                plateDocument={plateDocument}
                expandedEntryIds={expandedEntryIds}
                onModeChange={(nextMode) => {
                  setPeek(null);
                  setReaderMode(nextMode);
                }}
                onAnalysisToggle={toggleAnalysisEntry}
                onLookupIntent={handleLookupIntent}
                onInspectIntent={handleInspectIntent}
              />
            )}
          </div>
        </div>

        {selectionToolbar ? (
          <div
            className="hero-app-selection-toolbar z-50"
            data-placement={selectionToolbar.position.placement}
            style={
              {
                position: "absolute",
                left: `${selectionToolbar.position.left}px`,
                top: `${selectionToolbar.position.top}px`,
                transform: floatingTransform(selectionToolbar.position),
              } satisfies CSSProperties
            }
          >
            <SelectionToolbar
              className="hero-app-selection-toolbar-inner"
              selectedText={selectionToolbar.selectedText}
              hasHighlight
              hasAnnotation
              onAsk={() => undefined}
              onSelectSentence={() => undefined}
              onHighlight={() => undefined}
              onNote={() => undefined}
              onClearAnnotation={() => undefined}
              onLookup={() => undefined}
              onFeedback={() => undefined}
            />
          </div>
        ) : null}

        {peek ? (
          <HeroQuickPeek
            placement={peek.position.placement}
            style={
              {
                position: "absolute",
                left: `${peek.position.left}px`,
                top: `${peek.position.top}px`,
                transform: floatingTransform(peek.position),
              } satisfies CSSProperties
            }
            className="hero-app-quick-peek z-50 max-w-[min(23rem,calc(100%_-_2rem))]"
            lookup={peek.snapshot}
            inspect={peek.inspect}
            onDismiss={() => setPeek(null)}
          />
        ) : null}
      </div>
    </div>
  );
}

function HeroSidebar({
  view,
  onActivateView,
}: {
  view: HeroAppView;
  onActivateView: (view: HeroAppView) => void;
}) {
  return (
    <aside className="app-nav-surface hero-app-sidebar flex h-full flex-col border-r border-hairline/80 px-3 py-5">
      <button
        type="button"
        className="focus-ring flex min-h-12 items-center justify-center rounded-note transition-colors hover:bg-[var(--app-control-quiet)] active:scale-[0.98]"
        onClick={() => onActivateView("compose")}
        aria-label="Claread 演示"
      >
        <Image
          src="/brand/claread-icon-fullcolor.png"
          alt=""
          width={36}
          height={36}
          className="brand-aperture-shell brand-aperture-mark h-9 w-9 rounded-full border"
        />
      </button>

      <nav className="mt-7 flex flex-1 flex-col gap-0.5 px-1" aria-label="Claread 演示导航">
        {railItems.map((item) => {
          const itemId = item.id;
          const Icon = item.icon;
          const active = itemId === view;
          return (
            <button
              key={item.label}
              type="button"
              aria-label={item.label}
              aria-current={active ? "page" : undefined}
              className={`focus-ring relative flex min-h-[38px] items-center justify-center rounded-[8px] transition-colors active:scale-[0.97] ${
                active
                  ? "font-bold text-ink"
                  : "font-semibold text-muted-foreground hover:bg-[var(--app-control-quiet)] hover:text-ink"
              }`}
              onClick={itemId ? () => onActivateView(itemId) : undefined}
            >
              {active ? (
                <span className="absolute bottom-1.5 left-[-12px] top-1.5 w-[3px] rounded-r-full bg-ink" />
              ) : null}
              <Icon aria-hidden="true" className="h-[18px] w-[18px]" strokeWidth={active ? 2.5 : 2} />
            </button>
          );
        })}
      </nav>

      <div className="mt-auto flex flex-col gap-0.5 px-1 pb-2">
        <HeroIconButton
          label="公共首页"
          icon={<Compass aria-hidden="true" className="h-[18px] w-[18px]" />}
        />
        <HeroIconButton
          label="折叠导航"
          icon={<ChevronsLeft aria-hidden="true" className="h-[18px] w-[18px]" />}
        />
      </div>
    </aside>
  );
}

function HeroIconButton({
  label,
  icon,
}: {
  label: string;
  icon: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      className="focus-ring flex min-h-[38px] w-full items-center justify-center rounded-[8px] text-sm font-semibold text-muted-foreground transition-colors hover:bg-[var(--app-control-quiet)] hover:text-ink active:scale-[0.97]"
    >
      {icon}
    </button>
  );
}

function HeroComposeView({
  selectedGoal,
  onGoalChange,
  onStart,
}: {
  selectedGoal: string;
  onGoalChange: (goal: string) => void;
  onStart: () => void;
}) {
  const selectedGoalMeta =
    goalOptions.find((goal) => goal.id === selectedGoal) ?? goalOptions[0];
  const selectedVariantLabel =
    selectedGoal === "exam" ? "四六级" : selectedGoal === "academic" ? "通用学术" : "自然阅读";
  const characterCount = heroComposeText.length.toLocaleString("en-US");

  return (
    <div className="grid h-full min-h-0 grid-cols-[minmax(0,1fr)_23.5rem] gap-0 overflow-hidden xl:grid-cols-[minmax(0,1fr)_25.5rem]">
      <section className="flex min-w-0 flex-col px-8 py-7 xl:px-11 xl:py-10">
        <div className="max-w-[58rem]">
          <span className="mb-3 inline-block text-[0.72rem] font-bold tracking-[0.14em] text-lens-blue">
            Paste to Begin
          </span>
          <h2 className="text-balance font-headline text-[clamp(2.45rem,3.72vw,4rem)] font-semibold leading-[0.94] tracking-[-0.035em] text-ink">
            <span className="block">Bring it to Claread.</span>
            <span className="block">Read It Deeply.</span>
          </h2>
          <p className="mt-4 max-w-[28rem] font-reading text-[1.04rem] leading-[1.65] text-muted-foreground">
            从粘贴开始，进入深度阅读。
          </p>
        </div>

        <div className="mt-8 flex min-h-0 flex-1 flex-col xl:mt-10">
          <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden rounded-[10px] bg-[linear-gradient(180deg,rgba(251,247,238,0.62),rgba(251,247,238,0.18)_48%,rgba(251,247,238,0)_100%)] ring-1 ring-hairline/35 transition-[ring-color] duration-200 hover:ring-hairline/55">
            <div className="pointer-events-none absolute left-4 top-5 h-[calc(100%-2.5rem)] w-px bg-hairline/75 xl:left-5" />
            <div className="pointer-events-none absolute left-12 top-9 h-[3.4rem] w-[2px] bg-lens-blue/48 xl:left-16" />
            <button
              type="button"
              aria-label="清空文本"
              className="focus-ring absolute right-3 top-3 z-20 inline-flex h-9 w-9 items-center justify-center rounded-[9px] text-muted-foreground transition-colors hover:bg-reader-paper/50 hover:text-ink active:scale-[0.98]"
            >
              <X aria-hidden="true" className="h-4 w-4" />
            </button>
            <div
              role="textbox"
              aria-readonly="true"
              tabIndex={0}
              className="relative z-10 min-h-0 flex-1 overflow-hidden px-16 py-10 font-reading text-[1.08rem] leading-[2.08] text-ink outline-none selection:bg-lens-blue/15 sm:text-[1.16rem] xl:px-24 xl:py-12 xl:text-[1.2rem]"
            >
              {heroComposeText}
              <span className="ml-0.5 inline-block h-[1.1em] w-px translate-y-[0.18em] bg-lens-blue" />
            </div>

            <div className="relative z-20 mx-5 mb-4 shrink-0 border-t border-hairline/70 px-0 pt-3 sm:mx-10 xl:mx-14">
              <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_auto] xl:items-center">
                <div className="flex min-w-0 flex-wrap items-center gap-x-5 gap-y-2 font-sans">
                  {intakeMethods.map((method) => {
                    const Icon = method.icon;
                    return (
                      <button
                        key={method.label}
                        type="button"
                        aria-pressed={method.active}
                        className={`focus-ring group/source inline-flex min-h-9 items-center gap-2 px-0 text-[0.78rem] font-medium leading-none transition-colors active:scale-[0.98] ${
                          method.active ? "text-ink hover:text-lens-blue" : "text-subtle/70 hover:text-muted-foreground"
                        }`}
                      >
                        <span
                          className={`inline-flex h-6 w-6 items-center justify-center rounded-[7px] border transition-colors ${
                            method.active
                              ? "border-ink/12 bg-reader-paper/54 text-ink group-hover/source:border-lens-blue/34 group-hover/source:text-lens-blue"
                              : "border-transparent bg-transparent text-subtle/65"
                          }`}
                        >
                          <Icon aria-hidden="true" className="h-3.5 w-3.5" />
                        </span>
                        <span>{method.label}</span>
                      </button>
                    );
                  })}
                </div>

                <div className="flex flex-col-reverse items-stretch gap-3 sm:flex-row sm:items-center sm:justify-end">
                  <button
                    type="button"
                    className="focus-ring inline-flex min-h-10 items-center justify-center gap-2 whitespace-nowrap rounded-[10px] border border-transparent px-3 text-[0.78rem] font-semibold text-muted-foreground transition-colors hover:bg-reader-paper/55 hover:text-ink active:scale-[0.98]"
                    onClick={() => onGoalChange(selectedGoal)}
                  >
                    <SlidersHorizontal aria-hidden="true" className="h-4 w-4" />
                    <span>
                      {selectedGoalMeta.label} · {selectedVariantLabel}
                    </span>
                  </button>
                  <span className="hidden text-[0.72rem] font-medium tracking-[0.03em] text-muted-foreground xl:inline">
                    {characterCount} chars
                  </span>
                  <button
                    type="button"
                    className="aperture-corner-cta aperture-corner-cta--ready group/aperture shrink-0 font-sans"
                    onClick={onStart}
                  >
                    <span className="aperture-corner-cta__mark" aria-hidden="true">
                      <span className="aperture-corner-cta__asset aperture-corner-cta__asset--default" />
                      <span className="aperture-corner-cta__asset aperture-corner-cta__asset--focus" />
                    </span>
                    <span className="aperture-corner-cta__content">
                      <span className="aperture-corner-cta__label">开始透读</span>
                      <ArrowRight aria-hidden="true" className="aperture-corner-cta__arrow" />
                    </span>
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <aside className="h-full min-h-0 min-w-0 overflow-hidden border-l border-hairline/70 px-7 py-8 xl:px-9 xl:py-10">
        <div className="h-full min-h-0 overflow-hidden">
          <HeroDailyPicks />
        </div>
      </aside>
    </div>
  );
}

function HeroDailyPicks() {
  const lead = heroDailyPicks[0];
  const rest = heroDailyPicks.slice(1);

  return (
    <div className="h-full overflow-hidden">
      <div className="mb-4 flex items-center justify-between gap-4">
        <h3 className="text-[0.68rem] font-bold tracking-[0.12em] text-ink">
          Editor&apos;s Picks
        </h3>
        <button
          type="button"
          className="focus-ring text-[0.72rem] font-medium tracking-[0.02em] text-muted-foreground transition-colors hover:text-ink"
        >
          查看全部 →
        </button>
      </div>

      <h4 className="border-b border-hairline/75 pb-4 font-headline text-[1.72rem] leading-[1.08] tracking-[-0.02em] text-ink">
        今日值得透读
      </h4>

      <button type="button" className="focus-ring group block w-full border-b border-hairline/70 py-5 text-left">
        <div className="grid grid-cols-[minmax(0,1fr)_5.6rem] gap-4">
          <div className="min-w-0">
            <p className="mb-3 font-sans text-[0.68rem] font-semibold tracking-[0.08em] text-muted-foreground">
              Featured
            </p>
            <h5 className="text-balance font-headline text-[1.33rem] leading-[1.1] tracking-[-0.025em] text-ink transition-colors group-hover:text-lens-blue">
              {lead.title}
            </h5>
          </div>
          <HeroDailyPickThumbnail index={0} />
        </div>
        <p className="mt-4 line-clamp-2 font-reading text-[0.92rem] leading-[1.58] text-muted-foreground">
          {lead.subtitle}
        </p>
        <div className="mt-4 font-sans text-[0.72rem] font-medium tracking-[0.01em] text-muted-foreground">
          {lead.source} · {lead.readTime} · {lead.difficulty}
        </div>
        <HeroTagList tags={lead.tags} />
      </button>

      <div className="divide-y divide-hairline/70">
        {rest.map((article, index) => (
          <button key={article.id} type="button" className="focus-ring group grid w-full grid-cols-[4.4rem_minmax(0,1fr)] gap-4 py-4 text-left">
            <HeroDailyPickThumbnail index={index + 1} compact />
            <div className="min-w-0">
              <h5 className="text-balance font-headline text-[1.03rem] leading-[1.16] tracking-[-0.02em] text-ink transition-colors group-hover:text-lens-blue">
                {article.title}
              </h5>
              <div className="mt-2 font-sans text-[0.7rem] font-medium tracking-[0.01em] text-muted-foreground">
                {article.source} · {article.readTime}
              </div>
              <HeroTagList tags={article.tags} />
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}

function HeroDailyPickThumbnail({ index, compact = false }: { index: number; compact?: boolean }) {
  return (
    <span
      aria-hidden="true"
      className={`relative block overflow-hidden rounded-[8px] border border-hairline/70 bg-reader-paper shadow-[inset_0_1px_0_rgba(255,255,255,0.62)] ${
        compact ? "h-[4.4rem] w-[4.4rem]" : "h-[5.6rem] w-[5.6rem]"
      }`}
    >
      <span className="absolute inset-0 bg-[linear-gradient(135deg,rgba(19,19,20,0.92)_0%,rgba(19,19,20,0.92)_36%,rgba(37,99,235,0.9)_36%,rgba(37,99,235,0.9)_52%,rgba(251,247,238,0.64)_52%,rgba(251,247,238,0.64)_100%)]" />
      <span
        className={`absolute rounded-full border border-white/55 ${
          index % 2 === 0
            ? "-left-5 top-3 h-16 w-16"
            : "-right-5 -top-3 h-16 w-16"
        }`}
      />
      <span className="absolute bottom-2 left-2 h-1.5 w-10 rounded-full bg-white/42" />
    </span>
  );
}

function HeroTagList({ tags }: { tags: string[] }) {
  return (
    <div className="mt-3 flex flex-wrap gap-1.5">
      {tags.map((tag) => (
        <span
          key={tag}
          className="rounded-pill border border-hairline/70 bg-surface/48 px-2 py-0.5 text-[0.62rem] font-semibold text-muted-foreground"
        >
          {tag}
        </span>
      ))}
    </div>
  );
}

function HeroLibraryView({
  records,
  onOpenReader,
}: {
  records: HeroRecordListItem[];
  onOpenReader: () => void;
}) {
  return (
    <div className="grid h-full min-h-0 grid-cols-[minmax(0,1fr)_19rem] gap-8 overflow-hidden px-10 py-8 xl:px-12">
      <main className="flex min-h-0 flex-col">
        <div className="mb-5 flex shrink-0 items-end justify-between gap-4 border-b border-hairline pb-5 pl-2">
          <div>
            <div className="mb-2 flex items-center gap-3">
              <p className="text-[0.6rem] font-bold tracking-[0.2em] text-lens-blue">Library</p>
              <div className="h-px w-8 bg-hairline" />
            </div>
            <h2 className="font-headline text-[2.55rem] font-semibold leading-none tracking-tight text-ink">
              Reading Archive.
            </h2>
          </div>
          <button
            type="button"
            className="focus-ring inline-flex min-h-11 items-center justify-center rounded-[10px] bg-ink px-5 text-[0.82rem] font-semibold tracking-[0.08em] text-white transition-transform hover:-translate-y-0.5 active:translate-y-0"
          >
            <Plus aria-hidden="true" className="mr-2 h-4 w-4" />
            新解读
          </button>
        </div>

        <div className="mb-4 flex shrink-0 items-center justify-between pl-2">
          <div className="flex w-full max-w-sm items-center gap-3">
            <Search className="h-4 w-4 text-muted-foreground" />
            <input
              type="text"
              aria-label="搜索标题、原文片段或阅读目标"
              placeholder="搜索标题、原文片段或阅读目标..."
              className="w-full bg-transparent text-[0.95rem] text-ink outline-none placeholder:text-muted-foreground"
              readOnly
            />
          </div>
          <p className="text-[0.72rem] font-semibold tracking-[0.08em] text-ink">
            共 {records.length} 篇记录
          </p>
        </div>

        <div className="mb-4 flex shrink-0 items-center justify-between pl-2">
          <div className="flex flex-wrap items-center gap-1.5">
            {["全部", "仅收藏"].map((label, index) => (
              <button
                key={label}
                type="button"
                aria-pressed={index === 0}
                className={`focus-ring inline-flex items-center gap-1.5 rounded-pill px-3 py-1.5 text-[0.72rem] font-semibold tracking-[0.06em] transition-colors ${
                  index === 0
                    ? "bg-white text-ink shadow-[0_1px_3px_rgba(28,24,18,0.06)] ring-1 ring-hairline"
                    : "text-muted-foreground hover:bg-black/[0.03] hover:text-ink"
                }`}
              >
                {label === "仅收藏" ? <Heart className="h-3.5 w-3.5" /> : null}
                {label}
              </button>
            ))}
          </div>
          <button
            type="button"
            className="focus-ring rounded-pill bg-white px-3 py-1.5 text-[0.72rem] font-semibold tracking-[0.06em] text-ink ring-1 ring-hairline"
          >
            最近阅读
          </button>
        </div>

        <section className="min-h-0 flex-1 overflow-hidden pr-4">
          {records.map((record) => (
            <article
              key={record.id}
              className="group relative flex items-stretch justify-between gap-8 border-b border-hairline/45 py-7 first:pt-3"
            >
              <button type="button" className="focus-ring min-w-0 flex-1 text-left" onClick={onOpenReader}>
                <div className="mb-2.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[0.66rem] font-semibold tracking-[0.1em] text-muted-foreground">
                  <span className="inline-flex items-center gap-1.5 font-bold text-vocab-amber">
                    <Sparkles className="h-3.5 w-3.5" />
                    {record.statusLabel}
                  </span>
                  <span className="text-muted-foreground/30">·</span>
                  <span>{record.goalLabel}</span>
                  <span className="text-muted-foreground/30">·</span>
                  <span>{record.variantLabel}</span>
                  <span className="text-muted-foreground/30">·</span>
                  <span>{record.sourceLabel}</span>
                </div>
                <h3 className="font-headline text-[1.45rem] font-bold leading-[1.28] tracking-tight text-ink transition-colors group-hover:text-lens-blue">
                  {record.title}
                </h3>
                <p className="mt-3 line-clamp-2 max-w-3xl font-reading text-[1rem] leading-[1.7] text-muted-foreground/95">
                  {record.excerpt}
                </p>
                <div className="mt-4 flex flex-wrap items-center gap-x-2.5 gap-y-1.5 text-[0.72rem] font-medium tracking-[0.05em] text-muted-foreground">
                  <span className="flex items-center gap-1 text-muted-foreground/75">
                    <Calendar className="h-3.5 w-3.5 opacity-60" />
                    {record.dateLabel}
                  </span>
                  <span className="text-muted-foreground/30">·</span>
                  <span className="flex items-center gap-1 text-muted-foreground/75">
                    <FileText className="h-3.5 w-3.5 opacity-60" />
                    {record.wordCount} 词
                  </span>
                  <span className="text-muted-foreground/30">·</span>
                  <span className="flex items-center gap-1 font-semibold text-vocab-amber">
                    <NotebookPen className="h-3.5 w-3.5" />
                    {record.noteCount} 笔记
                  </span>
                  <span className="text-muted-foreground/30">·</span>
                  <span className="flex items-center gap-1 font-semibold text-grammar-violet">
                    <BookMarked className="h-3.5 w-3.5" />
                    {record.vocabularyCount} 生词
                  </span>
                </div>
              </button>
              <div className="flex shrink-0 flex-col items-end justify-between py-1">
                <button
                  type="button"
                  aria-label={record.favorited ? "已收藏" : "收藏"}
                  className={`focus-ring inline-flex h-8 w-8 items-center justify-center rounded-md transition-colors hover:bg-black/[0.035] ${
                    record.favorited ? "text-vocab-amber" : "text-muted-foreground hover:text-ink"
                  }`}
                >
                  <Heart className={record.favorited ? "h-4 w-4 fill-current" : "h-4 w-4"} />
                </button>
                <button
                  type="button"
                  title="继续阅读"
                  className="focus-ring inline-flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground opacity-100 transition-all hover:translate-x-1 hover:text-ink"
                  onClick={onOpenReader}
                >
                  <ArrowRight className="h-4.5 w-4.5" />
                </button>
              </div>
            </article>
          ))}
        </section>
      </main>

      <aside className="hidden min-w-0 border-l border-hairline/70 pl-8 lg:block">
        <div className="pt-12">
          <p className="text-[0.58rem] font-bold tracking-[0.18em] text-subtle">Archive Summary</p>
          <h3 className="mt-3 font-headline text-[1.8rem] font-semibold leading-tight text-ink">
            最近读过的文章
          </h3>
          <div className="mt-6 grid gap-3 text-[0.82rem] font-medium text-muted-foreground">
            <div className="flex justify-between border-b border-hairline/70 pb-3">
              <span>总记录</span>
              <strong className="text-ink">{records.length}</strong>
            </div>
            <div className="flex justify-between border-b border-hairline/70 pb-3">
              <span>收藏</span>
              <strong className="text-ink">1</strong>
            </div>
            <div className="flex justify-between border-b border-hairline/70 pb-3">
              <span>生词</span>
              <strong className="text-ink">12</strong>
            </div>
          </div>
        </div>
      </aside>
    </div>
  );
}

function HeroVocabularyView({
  items,
  selectedItem,
  selectedId,
  onSelectItem,
  onOpenSource,
}: {
  items: HeroVocabularyItem[];
  selectedItem: HeroVocabularyItem;
  selectedId: string;
  onSelectItem: (id: string) => void;
  onOpenSource: () => void;
}) {
  const visibleItems = items.slice(0, 2);
  const reviewCount = visibleItems.filter((item) => item.reviewLabel === "今日复习").length;

  return (
    <div className="grid h-full min-h-0 grid-cols-[minmax(0,1fr)_24rem] gap-0 overflow-hidden px-10 py-8 xl:px-12">
      <main className="flex min-h-0 flex-col pr-8">
        <div className="mb-5 flex shrink-0 items-end justify-between gap-4 border-b border-hairline pb-5 pl-2">
          <div>
            <div className="mb-2 flex items-center gap-3">
              <p className="text-[0.6rem] font-bold tracking-[0.2em] text-lens-blue">Vocabulary</p>
              <div className="h-px w-8 bg-hairline" />
            </div>
            <h2 className="font-headline text-[2.55rem] font-semibold leading-none tracking-tight text-ink">
              Vocabulary Book.
            </h2>
            <p className="mt-3 max-w-[32ch] font-reading text-[1rem] leading-[1.75] text-muted-foreground">
              阅读中留下的重点词汇与语境。
            </p>
          </div>
          <button
            type="button"
            className="focus-ring inline-flex min-h-11 items-center justify-center rounded-[10px] border border-hairline bg-surface px-5 text-[0.82rem] font-semibold tracking-[0.08em] text-muted-foreground"
          >
            <Play aria-hidden="true" className="mr-2 h-3.5 w-3.5" />
            开始复习 1 个
          </button>
        </div>

        <div className="mb-4 flex shrink-0 items-center justify-between pb-2 pl-2">
          <div className="flex w-full max-w-sm items-center gap-3">
            <Search className="h-4 w-4 text-muted-foreground" />
            <input
              type="text"
              aria-label="搜索单词、释义或来源文章"
              placeholder="搜索单词、释义或来源文章..."
              className="w-full bg-transparent text-[0.95rem] text-ink outline-none placeholder:text-muted-foreground"
              readOnly
            />
          </div>
          <p className="text-[0.72rem] font-semibold tracking-[0.08em] text-muted-foreground">
            共 {visibleItems.length} 个生词 · {reviewCount} 个待复习
          </p>
        </div>

        <section className="min-h-0 flex-1 overflow-hidden pr-5">
          {visibleItems.map((item) => {
            const selected = item.id === selectedId;
            return (
              <button
                key={item.id}
                type="button"
                data-selected={selected ? "true" : "false"}
                onClick={() => onSelectItem(item.id)}
                className={`focus-ring group relative w-full border-b border-hairline/40 py-6 pl-3 pr-2 text-left transition-colors first:pt-2 ${
                  selected ? "bg-[rgba(251,249,244,0.7)]" : "hover:bg-[rgba(251,249,244,0.35)]"
                }`}
              >
                <div className="mb-2 flex flex-wrap items-baseline gap-x-3 gap-y-1.5">
                  <h3 className="font-headline text-[1.38rem] font-semibold leading-none tracking-tight text-ink">
                    {item.word}
                  </h3>
                  {item.phonetic ? <span className="text-xs text-muted-foreground">{item.phonetic}</span> : null}
                  <span className="rounded-pill border border-hairline/80 bg-surface/50 px-2 py-0.5 text-[0.68rem] font-semibold text-muted-foreground">
                    {item.partOfSpeech}
                  </span>
                </div>
                <p className="text-[0.95rem] font-semibold leading-relaxed text-ink-soft">
                  {item.shortMeaning}
                </p>
                <div className="mt-2.5 pl-4">
                  <p className="line-clamp-1 font-reading text-[0.92rem] italic leading-relaxed text-muted-foreground">
                    {item.contextSentence}
                  </p>
                  <p className="mt-1 line-clamp-1 text-[0.85rem] leading-normal text-subtle">
                    {item.contextTranslation}
                  </p>
                </div>
                <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-1 text-[0.68rem] font-semibold tracking-[0.08em] text-muted-foreground">
                  <span>{item.reviewLabel}</span>
                  <span className="text-muted-foreground/30">·</span>
                  <span>{item.sourceCount}</span>
                  <span className="text-muted-foreground/30">·</span>
                  <span className="text-lens-blue">查看来源语境</span>
                </div>
              </button>
            );
          })}
        </section>
      </main>

      <aside className="min-w-0 border-l border-hairline/80 bg-surface/88">
        <div className="flex h-full flex-col p-7">
          <div className="flex items-start justify-between gap-4">
            <div>
              <p className="text-[0.58rem] font-bold tracking-[0.18em] text-subtle">
                Claread Vocabulary
              </p>
              <h3 className="mt-4 font-headline text-[2.1rem] font-semibold leading-none text-ink">
                {selectedItem.word}
              </h3>
              {selectedItem.phonetic ? (
                <p className="mt-2 text-sm font-medium text-muted-foreground">{selectedItem.phonetic}</p>
              ) : null}
            </div>
            <button
              type="button"
              className="focus-ring inline-flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground hover:bg-black/[0.035] hover:text-ink"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          <div className="mt-6 flex flex-wrap items-center gap-2">
            <span className="rounded-pill border border-hairline bg-reader-paper px-2.5 py-1 text-[0.72rem] font-semibold text-muted-foreground">
              {selectedItem.partOfSpeech}
            </span>
            <span className="rounded-pill border border-vocab-amber/20 bg-vocab-amber/10 px-2.5 py-1 text-[0.72rem] font-semibold text-vocab-amber">
              {selectedItem.reviewLabel}
            </span>
          </div>

          <section className="mt-7 border-t border-hairline pt-5">
            <p className="text-[0.68rem] font-bold tracking-[0.14em] text-subtle">释义</p>
            <p className="mt-3 text-[1rem] font-semibold leading-7 text-ink-soft">
              {selectedItem.shortMeaning}
            </p>
          </section>

          <section className="mt-7 border-t border-hairline pt-5">
            <p className="text-[0.68rem] font-bold tracking-[0.14em] text-subtle">来源语境</p>
            <p className="mt-3 font-reading text-[1rem] italic leading-7 text-ink">
              {selectedItem.contextSentence}
            </p>
            <p className="mt-2 text-[0.88rem] leading-6 text-muted-foreground">
              {selectedItem.contextTranslation}
            </p>
          </section>

          <div className="mt-auto flex gap-2 border-t border-hairline pt-5">
            <button
              type="button"
              className="focus-ring inline-flex min-h-9 flex-1 items-center justify-center rounded-[8px] bg-ink px-3 text-[0.76rem] font-semibold text-white transition-transform active:scale-[0.98]"
            >
              <Check className="mr-1.5 h-3.5 w-3.5" />
              标为掌握
            </button>
            <button
              type="button"
              className="focus-ring inline-flex min-h-9 flex-1 items-center justify-center rounded-[8px] border border-hairline bg-reader-paper px-3 text-[0.76rem] font-semibold text-ink transition-colors hover:bg-surface"
              onClick={onOpenSource}
            >
              回到原文
            </button>
          </div>
        </div>
      </aside>
    </div>
  );
}

function HeroQuickPeek({
  className,
  inspect = null,
  lookup = null,
  onDismiss,
  placement = "bottom",
  style,
}: {
  lookup?: DictionaryLookupSnapshot | null;
  inspect?: ReaderStructuredInspectIntent | null;
  className?: string;
  placement?: HeroFloatingPlacement;
  style?: CSSProperties;
  onDismiss: () => void;
}) {
  const inspectTitle = inspect ? inspect.lookupText ?? inspect.anchorText : "";
  const inspectMeaning = inspect?.glossary?.zh ?? inspect?.glossary?.gloss ?? "";
  const inspectReason = inspect?.glossary?.reason;
  const lookupMeaning =
    lookup?.state.kind === "ready" ? firstMeaning(lookup.state.result) : "";
  const lookupPartOfSpeech =
    lookup?.state.kind === "ready" && lookup.state.result.kind === "entry"
      ? lookup.state.result.entry.meanings.at(0)?.partOfSpeech
      : null;
  const title = lookup?.title ?? inspectTitle;
  const eyebrow =
    lookup?.label ??
    (inspect?.annotationType === "phrase_gloss"
      ? "动词短语"
      : inspect?.annotationType === "context_gloss"
        ? "语境说明"
        : "词典");
  const body = lookupMeaning || inspectMeaning;

  return (
    <div
      role="dialog"
      aria-modal="false"
      data-placement={placement}
      className={`reader-lookup-preview rounded-[14px] border border-hairline bg-surface/98 p-4 text-ink shadow-[0_18px_44px_rgba(28,24,18,0.13)] backdrop-blur ${className ?? ""}`}
      style={style}
      onClick={(event) => event.stopPropagation()}
      onPointerDown={(event) => event.stopPropagation()}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="text-[0.7rem] font-semibold tracking-[0.12em] text-phrase-lavender">
            {eyebrow}
          </div>
          <div className="mt-1 text-[1.12rem] font-semibold leading-tight text-ink">
            {title}
          </div>
        </div>
        <button
          type="button"
          className="focus-ring inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-[0.55rem] text-subtle hover:bg-black/[0.04] hover:text-ink"
          onClick={onDismiss}
          aria-label="关闭预览卡片"
        >
          <X aria-hidden="true" className="h-3.5 w-3.5" />
        </button>
      </div>

      {body ? (
        <div className="mt-3">
          <div className="flex flex-wrap items-center gap-2">
            {lookupPartOfSpeech ? (
              <span className="rounded-pill border border-hairline bg-reader-paper px-2 py-0.5 text-[0.68rem] font-semibold text-muted-foreground">
                {lookupPartOfSpeech}
              </span>
            ) : null}
            <p className="text-[0.92rem] font-medium leading-6 text-ink-soft">{body}</p>
          </div>
          {inspectReason ? (
            <p className="mt-2 text-[0.78rem] leading-5 text-muted-foreground">{inspectReason}</p>
          ) : null}
          {lookup?.contextSentence ? (
            <p className="mt-3 border-t border-hairline/60 pt-2.5 font-reading text-[0.82rem] leading-5 text-muted-foreground">
              {lookup.contextSentence}
            </p>
          ) : null}
        </div>
      ) : null}

      <div className="mt-3 flex items-center gap-1 border-t border-hairline/60 pt-2.5">
        <button
          type="button"
          className="focus-ring inline-flex h-8 w-8 items-center justify-center rounded-[0.7rem] text-muted-foreground hover:bg-black/[0.04] hover:text-ink active:scale-[0.97]"
          aria-label="打开词典"
        >
          <BookOpen className="h-3.5 w-3.5" />
        </button>
        <button
          type="button"
          className="focus-ring inline-flex h-8 w-8 items-center justify-center rounded-[0.7rem] text-lens-blue/80 hover:bg-lens-blue/5 hover:text-lens-blue active:scale-[0.97]"
          aria-label="带入 Ask"
        >
          <Sparkles className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  );
}

function HeroReaderView({
  record,
  isFavorited,
  mode,
  plateDocument,
  expandedEntryIds,
  onModeChange,
  onAnalysisToggle,
  onLookupIntent,
  onInspectIntent,
}: {
  record: HeroReaderRecord;
  isFavorited: boolean;
  mode: HeroReaderMode;
  plateDocument: ReturnType<typeof renderSceneToPlateDocument>;
  expandedEntryIds: string[];
  onModeChange: (mode: HeroReaderMode) => void;
  onAnalysisToggle: (entryId: string) => void;
  onLookupIntent: (
    intent: ReaderLookupIntent,
    anchor: ReaderLookupPreviewAnchor | null,
    triggerEl?: HTMLElement | null,
  ) => void;
  onInspectIntent: (
    intent: ReaderStructuredInspectIntent,
    anchor: ReaderLookupPreviewAnchor | null,
    triggerEl?: HTMLElement | null,
  ) => void;
}) {
  const isImmersive = mode === "immersive";

  return (
    <article className="relative h-full overflow-hidden bg-reader-paper">
      <div className="hero-app-scroll h-full overflow-y-auto">
        <header className="hero-reader-header sticky top-0 z-20 border-b border-hairline bg-background/96 px-8 py-4 backdrop-blur xl:px-10">
          <div className="reader-header-band-inner mx-auto flex w-full max-w-[82ch] flex-col gap-5">
            <div className="flex items-center gap-1.5 text-[0.8rem] font-semibold leading-none tracking-wide">
              <span className="text-lens-blue">{isImmersive ? "沉浸阅读" : "精读模式"}</span>
              <span className="text-muted-foreground/60">·</span>
              <span className="font-medium text-muted-foreground">{record.date}</span>
              <span className="text-muted-foreground/60">·</span>
              <span className="font-medium text-muted-foreground">{record.readingGoalLabel}</span>
            </div>

            <div className="min-w-0">
              <h2 className="font-headline text-[clamp(2rem,3.4vw,3rem)] font-bold leading-[1.08] tracking-tight text-ink">
                {record.title}
              </h2>
            </div>

            <div className="flex min-h-[56px] w-full flex-col items-stretch justify-between border-y border-hairline bg-transparent sm:flex-row">
              <div className="flex items-center gap-3.5 py-3 sm:py-0">
                <span className="flex items-center gap-1.5 rounded-[0.5rem] border border-hairline/80 bg-surface-warm px-3 py-1 text-[0.75rem] font-semibold text-ink-soft shadow-[inset_0_1px_0_rgba(255,255,255,0.8),0_1px_2px_rgba(0,0,0,0.03)]">
                  <Sparkles className="h-3.5 w-3.5 fill-vocab-amber/10 text-vocab-amber" />
                  解析结果
                </span>
                <div className="h-3.5 w-px bg-hairline" />
                <span className="text-[0.8rem] font-semibold text-muted-foreground">
                  {record.scene.article.sentences.length} 句
                </span>
                <div className="h-3.5 w-px bg-hairline" />
                <span className="text-[0.8rem] font-semibold text-muted-foreground">{record.sourceName}</span>
              </div>

              <div className="flex select-none items-stretch divide-x divide-hairline border-t border-hairline sm:border-t-0">
                <ReaderActionButton
                  active={isFavorited}
                  label="收藏"
                  subLabel="加入阅读资产"
                  icon={<Heart className={isFavorited ? "h-[18px] w-[18px] fill-current" : "h-[18px] w-[18px]"} />}
                  onClick={() => undefined}
                />
                <ReaderActionButton
                  active={mode === "intensive"}
                  label="精读"
                  subLabel="逐句研读"
                  icon={<BookOpen className="h-[18px] w-[18px]" />}
                  onClick={() => onModeChange("intensive")}
                />
                <ReaderActionButton
                  active={mode === "immersive"}
                  label="沉浸"
                  subLabel="专注阅读"
                  icon={<Eye className="h-[18px] w-[18px]" />}
                  onClick={() => onModeChange("immersive")}
                />
                <ReaderActionButton
                  active={false}
                  label="设置"
                  subLabel="版式与偏好"
                  icon={<SlidersHorizontal className="h-[18px] w-[18px]" />}
                  onClick={() => undefined}
                />
              </div>
            </div>

            <div className="flex w-full flex-col justify-between gap-3 text-[0.78rem] leading-normal tracking-wide text-muted-foreground sm:flex-row sm:items-center">
              <div className="flex flex-wrap items-center gap-1.5 font-medium">
                <span>来源 {record.sourceName}</span>
                <span className="text-muted-foreground/60">·</span>
                <span>{record.date}</span>
                <span className="text-muted-foreground/60">·</span>
                <span>约 {Math.max(1, Math.ceil(record.scene.article.sentences.length / 5))} 分钟阅读</span>
              </div>
              <span className="inline-flex items-center gap-1.5 text-muted-foreground/60">
                <Globe className="h-4 w-4" />
                粘贴导入
              </span>
            </div>
          </div>
        </header>

        <div
          key={mode}
          className={`hero-reader-mode-layer reader-reading-stage ${
            isImmersive ? "reader-reading-stage--immersive" : "reader-reading-stage--intensive"
          }`}
        >
          {isImmersive ? (
            <ImmersiveReaderSurface
              document={plateDocument}
              readingClassName={heroImmersiveReadingClassName}
              columnClassName={heroReaderColumnClassName}
              paragraphDensityClassName={heroImmersiveDensityClassName}
              onLookupIntent={onLookupIntent}
              onInspectIntent={onInspectIntent}
            />
          ) : (
            <IntensiveReaderSurface
              document={plateDocument}
              showTranslation
              readingClassName={heroReadingClassName}
              translationClassName={heroTranslationClassName}
              columnClassName={heroReaderColumnClassName}
              paragraphDensityClassName={heroReaderDensityClassName}
              activeSentenceId={record.selectedSentenceId}
              selectedSentenceId={record.selectedSentenceId}
              activeAnalysisEntryId={expandedEntryIds[0] ?? null}
              expandedAnalysisEntryIds={expandedEntryIds}
              onAnalysisToggle={onAnalysisToggle}
              onLookupIntent={onLookupIntent}
              onInspectIntent={onInspectIntent}
            />
          )}
        </div>
      </div>

    </article>
  );
}

function ReaderActionButton({
  active,
  label,
  subLabel,
  icon,
  onClick,
}: {
  active: boolean;
  label: string;
  subLabel: string;
  icon: ReactNode;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className={`relative flex flex-1 items-center justify-center gap-2 px-3.5 py-2.5 text-left transition-colors sm:py-3.5 md:px-5 ${
        active
          ? "text-vocab-amber after:absolute after:bottom-0 after:left-0 after:right-0 after:h-0.5 after:bg-vocab-amber"
          : "text-ink hover:text-ink-soft"
      }`}
      onClick={onClick}
    >
      <span className={active ? "text-vocab-amber" : "text-muted-foreground"}>{icon}</span>
      <span className="flex min-w-0 flex-col items-start leading-none">
        <span className="whitespace-nowrap text-[0.85rem] font-semibold">{label}</span>
        <span className="mt-1 hidden whitespace-nowrap text-[0.65rem] font-medium text-subtle sm:block">
          {subLabel}
        </span>
      </span>
    </button>
  );
}

function HeroAppStageStyles() {
  return (
    <style>{`
      @keyframes hero-app-window-in {
        from {
          opacity: 0;
          transform: translateY(0.8rem) scale(0.992);
        }
        to {
          opacity: 1;
          transform: translateY(0) scale(1);
        }
      }

      @keyframes hero-app-mark-focus {
        0% {
          filter: saturate(0.78);
        }
        48% {
          filter: saturate(1.18);
        }
        100% {
          filter: saturate(1);
        }
      }

      @keyframes hero-reader-mode-fade {
        from {
          opacity: 0.62;
          transform: translateY(0.25rem);
        }
        to {
          opacity: 1;
          transform: translateY(0);
        }
      }

      [data-hero-app-stage] .hero-app-window {
        box-shadow:
          0 1px 2px rgba(23, 21, 17, 0.04),
          0 20px 48px rgba(28, 24, 18, 0.105);
        animation: hero-app-window-in 520ms cubic-bezier(0.22, 1, 0.36, 1) both;
      }

      [data-hero-app-stage="device"] .hero-app-window {
        box-shadow: none;
        animation: none;
      }

      [data-hero-app-stage] .hero-app-sidebar {
        background:
          linear-gradient(180deg, rgba(255,255,255,0.46), rgba(248,244,234,0.18)),
          #F7F5F0;
      }

      [data-hero-app-stage] .hero-app-scroll {
        scrollbar-width: thin;
        scrollbar-color: rgba(23, 21, 17, 0.14) transparent;
      }

      [data-hero-app-stage] .hero-app-scroll::-webkit-scrollbar {
        width: 8px;
      }

      [data-hero-app-stage] .hero-app-scroll::-webkit-scrollbar-track {
        background: transparent;
      }

      [data-hero-app-stage] .hero-app-scroll::-webkit-scrollbar-thumb {
        border: 2px solid transparent;
        border-radius: 999px;
        background: rgba(23, 21, 17, 0.16);
        background-clip: content-box;
      }

      [data-hero-app-stage] .reader-lookup-preview,
      [data-hero-app-stage] .reader-selection-toolbar {
        pointer-events: auto;
      }

      [data-hero-app-stage] .hero-app-quick-peek {
        transform-origin: 50% 0;
      }

      [data-hero-app-stage] .hero-app-quick-peek[data-placement="top"] {
        transform-origin: 50% 100%;
      }

      [data-hero-app-stage] .hero-app-quick-peek::before {
        content: "";
        position: absolute;
        left: 50%;
        z-index: -1;
        width: 0.72rem;
        height: 0.72rem;
        border: 1px solid var(--hairline);
        background: color-mix(in srgb, var(--surface) 98%, transparent);
        transform: translateX(-50%) rotate(45deg);
      }

      [data-hero-app-stage] .hero-app-quick-peek[data-placement="bottom"]::before {
        top: -0.38rem;
        border-bottom: 0;
        border-right: 0;
      }

      [data-hero-app-stage] .hero-app-quick-peek[data-placement="top"]::before {
        bottom: -0.38rem;
        border-left: 0;
        border-top: 0;
      }

      [data-hero-app-stage] .hero-app-selection-toolbar {
        pointer-events: auto;
        transform-origin: 50% 0;
      }

      [data-hero-app-stage] .hero-app-selection-toolbar[data-placement="top"] {
        transform-origin: 50% 100%;
      }

      [data-hero-app-stage] .hero-app-selection-toolbar::after {
        content: "";
        position: absolute;
        left: 50%;
        width: 0.52rem;
        height: 0.52rem;
        border: 1px solid color-mix(in srgb, var(--hairline) 80%, transparent);
        background: color-mix(in srgb, var(--surface-warm) 96%, transparent);
        transform: translateX(-50%) rotate(45deg);
      }

      [data-hero-app-stage] .hero-app-selection-toolbar[data-placement="bottom"]::after {
        top: -0.27rem;
        border-bottom: 0;
        border-right: 0;
      }

      [data-hero-app-stage] .hero-app-selection-toolbar[data-placement="top"]::after {
        bottom: -0.27rem;
        border-left: 0;
        border-top: 0;
      }

      [data-hero-app-stage] .hero-app-selection-toolbar-inner {
        max-width: min(24.5rem, calc(100vw - 1rem));
      }

      [data-hero-app-stage] .hero-app-selection-toolbar-inner [role="toolbar"] {
        max-width: min(24.5rem, calc(100vw - 1rem));
        gap: 0.125rem;
        border-radius: 999px;
        padding: 0.1875rem;
        box-shadow:
          0 8px 22px rgba(28, 24, 18, 0.1),
          inset 0 1px 0 rgba(255, 255, 255, 0.66);
      }

      [data-hero-app-stage] .hero-app-selection-toolbar-inner button {
        min-width: 1.75rem;
        min-height: 1.75rem;
        height: 1.75rem;
        gap: 0.25rem;
        padding: 0.25rem 0.45rem;
        font-size: 0.68rem;
        line-height: 1;
      }

      [data-hero-app-stage] .hero-app-selection-toolbar-inner button svg {
        width: 0.82rem;
        height: 0.82rem;
      }

      [data-hero-app-stage] .hero-app-selection-toolbar-inner [role="separator"] {
        height: 0.85rem;
        margin-left: 0.125rem;
        margin-right: 0.125rem;
      }

      [data-hero-app-stage] .hero-reader-mode-layer {
        animation: hero-reader-mode-fade 240ms ease both;
      }

      [data-hero-app-stage] .reader-reading-stage {
        min-height: 720px;
        padding-bottom: 4.25rem;
      }

      [data-hero-app-stage] .reader-shell--intensive {
        padding-top: clamp(1.6rem, 2.5vw, 2.4rem);
        padding-bottom: 4rem;
      }

      [data-hero-app-stage] .reader-shell--immersive {
        padding-top: clamp(1.9rem, 3vw, 3rem);
        padding-bottom: 4rem;
      }

      [data-hero-app-stage] .reader-paragraph {
        grid-template-columns: minmax(3.2rem, 4.3rem) minmax(0, 1fr);
        gap: clamp(1rem, 2vw, 1.55rem);
      }

      [data-hero-app-stage] .reader-paragraph::before {
        opacity: 0.54;
      }

      [data-hero-app-stage] .reader-paragraph-index {
        color: color-mix(in srgb, var(--ink) 45%, transparent);
      }

      [data-hero-app-stage] .reader-sentence-shell {
        padding-right: clamp(0.7rem, 1.7vw, 1.2rem);
      }

      [data-hero-app-stage] .reader-sentence-shell--active {
        background: transparent;
        box-shadow: none;
      }

      [data-hero-app-stage] .reader-entry-note {
        width: min(100%, 39rem);
        max-width: 39rem;
      }

      [data-hero-app-stage] .reader-entry-note.reader-entry-note--expanded {
        margin-top: 1.2rem;
        border-radius: 0.72rem;
        background:
          linear-gradient(180deg, rgba(255, 255, 255, 0.46), rgba(251, 250, 246, 0.72)),
          color-mix(in srgb, var(--reader-entry-accent) 4.5%, transparent);
        box-shadow:
          inset 0 1px 0 rgba(255, 255, 255, 0.66),
          0 8px 18px rgba(28, 24, 18, 0.045);
      }

      [data-hero-app-stage] .reader-entry-note-body {
        border-top-style: dashed;
      }

      [data-hero-app-stage] .reader-mark--grammar,
      [data-hero-app-stage] .reader-mark--grammar-segment,
      [data-hero-app-stage] .reader-mark--phrase,
      [data-hero-app-stage] .reader-mark--context,
      [data-hero-app-stage] .reader-mark--vocab,
      [data-hero-app-stage] .reader-mark--term {
        animation: hero-app-mark-focus 760ms cubic-bezier(0.22, 1, 0.36, 1) 260ms both;
      }

      @media (prefers-reduced-motion: reduce) {
        [data-hero-app-stage] .hero-app-window,
        [data-hero-app-stage] .hero-reader-mode-layer,
        [data-hero-app-stage] .reader-mark--grammar,
        [data-hero-app-stage] .reader-mark--grammar-segment,
        [data-hero-app-stage] .reader-mark--phrase,
        [data-hero-app-stage] .reader-mark--context,
        [data-hero-app-stage] .reader-mark--vocab,
        [data-hero-app-stage] .reader-mark--term {
          animation: none !important;
        }
      }
    `}</style>
  );
}
