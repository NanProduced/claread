"use client";

import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";

import { cn } from "@/lib/cn";
import {
  projectReaderRecordNavigation,
  type ReaderRecordNavigationItem,
  type ReaderRecordNavigationMode,
} from "@/lib/reader-plate/projection/reader-record-navigation";
import {
  projectReaderSemanticOutlineNav,
  selectMostSpecificCoveringNode,
  type ReaderOutlineSurface,
  type ReaderSemanticOutlineNavItem,
} from "@/lib/reader-plate/projection/semantic-outline-nav";
import type { ReaderRecordPlateDocument } from "@/lib/reader-plate/projection/reader-record-plate-document";
import type { ReaderPlateSnapshotDto } from "@/types/api/reader-plate";

const TOPBAR_SAFE_HEIGHT = 56; // px, sticky topbar + small gap
const SCROLL_LOCK_MS = 700;
const ACTIVE_SAFE_OFFSET = 8;

const PLATE_DOCUMENT_SELECTOR = ".reader-record-plate-document";

/** Row ref map keys — never bare unitId/nodeId (collision when node_id === unitId). */
function detRowRefKey(unitId: string): string {
  return `deterministic:${unitId}`;
}
function semRowRefKey(nodeId: string): string {
  return `semantic:${nodeId}`;
}

function getPlateDocumentRoot(): HTMLElement | null {
  if (typeof document === "undefined") return null;
  return document.querySelector<HTMLElement>(PLATE_DOCUMENT_SELECTOR);
}

/**
 * Locate the best scroll target for a reading unit inside the Reader Record
 * body. We never fall back to global `[data-unit-id]` because the rail itself
 * and other chrome elements may carry that attribute.
 *
 * Priority:
 * 1. paragraph with `data-reader-record-unit-start="true"` and matching unitId
 * 2. first paragraph with matching unitId
 */
function findUnitTarget(
  unitId: string,
  plateRoot: HTMLElement | null = getPlateDocumentRoot(),
): HTMLElement | null {
  const body = plateRoot;
  if (!body) return null;

  const paragraphs = body.querySelectorAll<HTMLElement>(
    '[data-reader-record-node="paragraph"]',
  );

  let fallback: HTMLElement | null = null;
  for (const paragraph of paragraphs) {
    if (paragraph.getAttribute("data-unit-id") !== unitId) continue;
    if (paragraph.getAttribute("data-reader-record-unit-start") === "true") {
      return paragraph;
    }
    if (fallback === null) {
      fallback = paragraph;
    }
  }

  return fallback;
}

/**
 * Prefer start_anchor_segment_id when it matches start_unit_id on the DOM;
 * otherwise unit-start paragraph.
 */
function findOutlineNodeTarget(
  item: ReaderSemanticOutlineNavItem,
  plateRoot: HTMLElement | null = getPlateDocumentRoot(),
): HTMLElement | null {
  const body = plateRoot;
  if (!body) return null;

  if (item.startAnchorSegmentId) {
    const paragraphs = body.querySelectorAll<HTMLElement>(
      '[data-reader-record-node="paragraph"]',
    );
    for (const paragraph of paragraphs) {
      if (
        paragraph.getAttribute("data-anchor-segment-id") ===
          item.startAnchorSegmentId &&
        paragraph.getAttribute("data-unit-id") === item.startUnitId
      ) {
        return paragraph;
      }
    }
  }

  return findUnitTarget(item.startUnitId, body);
}

/**
 * Cache entry is valid only when it is still a live paragraph for `unitId`
 * under the current plate document root. Detached or remounted nodes (common
 * after Plate setValue) must not drive scroll spy or click positioning.
 */
function isValidCachedUnitTarget(
  unitId: string,
  el: HTMLElement,
  plateRoot: HTMLElement | null,
): boolean {
  if (!el.isConnected) return false;
  if (!plateRoot || !plateRoot.contains(el)) return false;
  if (el.getAttribute("data-reader-record-node") !== "paragraph") return false;
  if (el.getAttribute("data-unit-id") !== unitId) return false;
  return true;
}

/**
 * Single validated target resolver for scroll spy and click.
 * Invalid cache entries are deleted and re-resolved immediately.
 */
function resolveValidatedUnitTarget(
  unitId: string,
  map: Map<string, HTMLElement>,
  plateRoot: HTMLElement | null = getPlateDocumentRoot(),
): HTMLElement | null {
  const cached = map.get(unitId);
  if (cached) {
    if (isValidCachedUnitTarget(unitId, cached, plateRoot)) {
      return cached;
    }
    map.delete(unitId);
  }

  const resolved = findUnitTarget(unitId, plateRoot);
  if (resolved && isValidCachedUnitTarget(unitId, resolved, plateRoot)) {
    map.set(unitId, resolved);
    return resolved;
  }
  return null;
}

function isValidCachedOutlineTarget(
  item: ReaderSemanticOutlineNavItem,
  el: HTMLElement,
  plateRoot: HTMLElement | null,
): boolean {
  if (!el.isConnected) return false;
  if (!plateRoot || !plateRoot.contains(el)) return false;
  if (el.getAttribute("data-reader-record-node") !== "paragraph") return false;
  if (el.getAttribute("data-unit-id") !== item.startUnitId) return false;
  if (item.startAnchorSegmentId) {
    if (
      el.getAttribute("data-anchor-segment-id") !== item.startAnchorSegmentId
    ) {
      return false;
    }
  }
  return true;
}

function resolveValidatedOutlineTarget(
  item: ReaderSemanticOutlineNavItem,
  map: Map<string, HTMLElement>,
  plateRoot: HTMLElement | null = getPlateDocumentRoot(),
): HTMLElement | null {
  const cacheKey = `outline:${item.nodeId}`;
  const cached = map.get(cacheKey);
  if (cached) {
    if (isValidCachedOutlineTarget(item, cached, plateRoot)) {
      return cached;
    }
    map.delete(cacheKey);
  }

  const resolved = findOutlineNodeTarget(item, plateRoot);
  if (resolved && isValidCachedOutlineTarget(item, resolved, plateRoot)) {
    map.set(cacheKey, resolved);
    return resolved;
  }
  return null;
}

/**
 * Active unit for scroll spy.
 * - L0: last unit above safeTop, else first below, else first item.
 * - L1: only last heading above safeTop; lead zone (all headings below) → null.
 *   Body coverage keeps the previous heading active because body is not a candidate.
 */
function computeActiveUnitId(
  items: ReaderRecordNavigationItem[],
  targetMap: Map<string, HTMLElement>,
  safeTop: number,
  mode: ReaderRecordNavigationMode,
): string | null {
  const plateRoot = getPlateDocumentRoot();
  let lastAbove: string | null = null;
  let firstBelow: string | null = null;

  for (const item of items) {
    const target = resolveValidatedUnitTarget(
      item.unitId,
      targetMap,
      plateRoot,
    );
    if (!target) continue;

    const top = target.getBoundingClientRect().top;
    if (top <= safeTop) {
      lastAbove = item.unitId;
    } else if (firstBelow === null) {
      firstBelow = item.unitId;
      break;
    }
  }

  if (mode === "L1") {
    return lastAbove;
  }

  return lastAbove ?? firstBelow ?? items[0]?.unitId ?? null;
}

/**
 * L2 active node:
 * - lead: every depth=1 root start is still below safeTop → null
 * - else: unit under safeTop, then most specific covering node
 */
function computeActiveOutlineNodeId(
  panelItems: ReaderSemanticOutlineNavItem[],
  tickItems: ReaderSemanticOutlineNavItem[],
  orderedUnitIds: string[],
  unitOrderById: Map<string, number>,
  targetMap: Map<string, HTMLElement>,
  safeTop: number,
): string | null {
  const plateRoot = getPlateDocumentRoot();

  let anyRootAbove = false;
  for (const root of tickItems) {
    const target = resolveValidatedUnitTarget(
      root.startUnitId,
      targetMap,
      plateRoot,
    );
    if (!target) continue;
    if (target.getBoundingClientRect().top <= safeTop) {
      anyRootAbove = true;
      break;
    }
  }
  if (!anyRootAbove) {
    return null;
  }

  let currentUnitId: string | null = null;
  for (const unitId of orderedUnitIds) {
    const target = resolveValidatedUnitTarget(unitId, targetMap, plateRoot);
    if (!target) continue;
    if (target.getBoundingClientRect().top <= safeTop) {
      currentUnitId = unitId;
    } else if (currentUnitId !== null) {
      break;
    }
  }

  return selectMostSpecificCoveringNode(
    panelItems,
    unitOrderById,
    currentUnitId,
  );
}

function buildNavigationTriggerLabel(
  mode: ReaderRecordNavigationMode,
  panelOpen: boolean,
  activeIndex: number | null,
): string {
  if (mode === "L1") {
    const action = panelOpen ? "关闭章节导航" : "打开章节导航";
    if (activeIndex === null) {
      return action;
    }
    return `${action}，当前第 ${activeIndex} 项`;
  }

  const action = panelOpen ? "关闭段落导航" : "打开段落导航";
  return `${action}，当前第 ${activeIndex ?? 1} 段`;
}

function buildSemanticTriggerLabel(
  panelOpen: boolean,
  activeIndex: number | null,
): string {
  const action = panelOpen ? "关闭内容大纲" : "打开内容大纲";
  if (activeIndex === null) {
    return action;
  }
  return `${action}，当前第 ${activeIndex} 项`;
}

function scrollElementIntoSafeView(target: HTMLElement) {
  const scrollContainer = getScrollContainer() ?? window;
  const isWindow = scrollContainer === window;
  const scrollTop = isWindow
    ? window.scrollY
    : (scrollContainer as HTMLElement).scrollTop;
  const containerTop = isWindow
    ? 0
    : (scrollContainer as HTMLElement).getBoundingClientRect().top;
  const targetRect = target.getBoundingClientRect();
  const targetOffset = targetRect.top - containerTop + scrollTop;
  const safeOffset = Math.max(
    0,
    TOPBAR_SAFE_HEIGHT + ACTIVE_SAFE_OFFSET - containerTop,
  );
  const top = targetOffset - safeOffset;

  if (isWindow) {
    window.scrollTo({ top: Math.max(0, top), behavior: "smooth" });
  } else {
    (scrollContainer as HTMLElement).scrollTo({
      top: Math.max(0, top),
      behavior: "smooth",
    });
  }
}

/**
 * Find the real scroll container for the Reader Record body. The app shell
 * wraps content in a Radix ScrollArea, so `window` is not always the element
 * that scrolls. We walk up from the plate document until we find an element
 * with overflow auto/scroll, falling back to `window`.
 */
function getScrollContainer(): Window | HTMLElement | null {
  if (typeof window === "undefined" || typeof document === "undefined") {
    return null;
  }

  const body = document.querySelector<HTMLElement>(
    ".reader-record-plate-document",
  );
  if (!body) return window;

  let el: HTMLElement | null = body.parentElement;
  while (el && el !== document.body && el !== document.documentElement) {
    const style = window.getComputedStyle(el);
    if (/(auto|scroll)/.test(style.overflowY + style.overflow)) {
      return el;
    }
    el = el.parentElement;
  }

  return window;
}

// ---------------------------------------------------------------------------
// Visual ticks — purely decorative, aria-hidden.
// ---------------------------------------------------------------------------

interface VisualTicksProps {
  surface: ReaderOutlineSurface;
  tickKeys: string[];
  activeKey: string | null;
  onTickMouseEnter: () => void;
}

function VisualTicks({
  surface,
  tickKeys,
  activeKey,
  onTickMouseEnter,
}: VisualTicksProps) {
  return (
    <span
      data-testid="reader-record-mini-rail"
      className="reader-record-mini-rail flex h-full w-full flex-col items-end justify-center gap-[2px] overflow-hidden py-4"
      aria-hidden="true"
    >
      {tickKeys.map((key) => (
        <span
          key={`${surface}:${key}`}
          className="group relative flex min-h-[7px] w-10 flex-1 max-h-4 shrink items-center justify-end rounded-sm px-1"
          data-navigation-tick-key={key}
          {...(surface === "deterministic"
            ? { "data-navigation-unit-id": key }
            : { "data-outline-node-id": key })}
          onMouseEnter={onTickMouseEnter}
        >
          <span
            className={cn(
              "block h-[1.5px] rounded-full transition-all duration-150 ease-[var(--cl-ease-standard)]",
              key === activeKey
                ? "w-5 bg-ink/60"
                : "w-3.5 bg-ink/18 group-hover:bg-ink/40",
            )}
          />
        </span>
      ))}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Mode switch (not a menu)
// ---------------------------------------------------------------------------

interface OutlineModeSwitchProps {
  surface: ReaderOutlineSurface;
  onChange: (surface: ReaderOutlineSurface) => void;
}

function OutlineModeSwitch({ surface, onChange }: OutlineModeSwitchProps) {
  return (
    <div
      role="group"
      className="flex items-center gap-1 border-b border-hairline/40 px-2 py-1.5"
      data-testid="reader-record-outline-mode-switch"
      aria-label="导航方式"
    >
      <button
        type="button"
        data-testid="reader-record-outline-mode-deterministic"
        aria-pressed={surface === "deterministic"}
        className={cn(
          "flex-1 rounded-md px-2 py-1 text-[10px] leading-snug transition-colors",
          surface === "deterministic"
            ? "bg-[var(--app-control-current)] font-medium text-ink"
            : "text-ink/55 hover:bg-ink/[0.035] hover:text-ink",
        )}
        onClick={() => onChange("deterministic")}
      >
        定位
      </button>
      <button
        type="button"
        data-testid="reader-record-outline-mode-semantic"
        aria-pressed={surface === "semantic"}
        className={cn(
          "flex-1 rounded-md px-2 py-1 text-[10px] leading-snug transition-colors",
          surface === "semantic"
            ? "bg-[var(--app-control-current)] font-medium text-ink"
            : "text-ink/55 hover:bg-ink/[0.035] hover:text-ink",
        )}
        onClick={() => onChange("semantic")}
      >
        大纲
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Navigation panel
// ---------------------------------------------------------------------------

interface NavigationPanelProps {
  panelId: string;
  surface: ReaderOutlineSurface;
  hasL2: boolean;
  isPartial: boolean;
  detMode: ReaderRecordNavigationMode;
  detItems: ReaderRecordNavigationItem[];
  semItems: ReaderSemanticOutlineNavItem[];
  activeKey: string | null;
  focusedKey: string | null;
  panelOpen: boolean;
  className?: string;
  getRowRef: (key: string) => HTMLButtonElement | null;
  onDetItemClick: (unitId: string) => void;
  onSemItemClick: (nodeId: string) => void;
  onDetItemKeyDown: (
    event: React.KeyboardEvent<HTMLButtonElement>,
    unitId: string,
  ) => void;
  onSemItemKeyDown: (
    event: React.KeyboardEvent<HTMLButtonElement>,
    nodeId: string,
  ) => void;
  onSurfaceChange: (surface: ReaderOutlineSurface) => void;
  onMouseEnter: () => void;
  onMouseLeave: (event: React.MouseEvent<HTMLElement>) => void;
  registerRowRef: (key: string, el: HTMLButtonElement | null) => void;
  // T5.6c: per-row section-translation state + handler. Forwarded to
  // SemanticPanelRow only (L0/L1 rows are unaffected).
  sectionTranslationStates: Map<string, SectionTranslationRowState>;
  onResolveSection: (item: ReaderSemanticOutlineNavItem) => void;
}

function NavigationPanel({
  panelId,
  surface,
  hasL2,
  isPartial,
  detMode,
  detItems,
  semItems,
  activeKey,
  focusedKey,
  panelOpen,
  className,
  getRowRef,
  onDetItemClick,
  onSemItemClick,
  onDetItemKeyDown,
  onSemItemKeyDown,
  onSurfaceChange,
  onMouseEnter,
  onMouseLeave,
  registerRowRef,
  sectionTranslationStates,
  onResolveSection,
}: NavigationPanelProps) {
  const scrollAreaRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!panelOpen) return;
    const toRefKey = (id: string) =>
      surface === "semantic" ? semRowRefKey(id) : detRowRefKey(id);
    const row =
      (focusedKey ? getRowRef(toRefKey(focusedKey)) : null) ??
      (activeKey ? getRowRef(toRefKey(activeKey)) : null);
    const scrollArea = scrollAreaRef.current;
    if (
      row &&
      scrollArea &&
      scrollArea.contains(row) &&
      typeof row.scrollIntoView === "function"
    ) {
      row.scrollIntoView({ block: "nearest", inline: "nearest" });
    }
  }, [panelOpen, activeKey, focusedKey, getRowRef, surface]);

  return (
    <div
      id={panelId}
      data-testid="reader-record-navigation-panel"
      data-reader-record-navigation-panel="true"
      data-outline-surface={surface}
      className={cn(
        "reader-record-navigation-panel motion-reduce:transition-none",
        "transition-[transform,opacity,visibility] duration-200 ease-[var(--cl-ease-standard)]",
        "absolute right-[calc(100%+8px)] top-1/2 z-10 max-h-[min(72vh,42rem)] origin-right -translate-y-1/2",
        panelOpen
          ? "visible translate-x-0 opacity-100"
          : "invisible translate-x-2 scale-[0.98] opacity-0 pointer-events-none",
        className,
      )}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
      aria-hidden={!panelOpen}
    >
      <div
        className={cn(
          "flex max-h-[min(72vh,42rem)] flex-col overflow-hidden rounded-lg border border-hairline/50",
          "bg-surface-raised/95 shadow-[var(--app-panel-shadow-quiet)] backdrop-blur-sm",
          "w-64",
        )}
      >
        {hasL2 ? (
          <OutlineModeSwitch surface={surface} onChange={onSurfaceChange} />
        ) : null}
        {surface === "semantic" && isPartial ? (
          <div
            className="border-b border-hairline/30 px-2.5 py-1 text-[9px] leading-snug text-muted-foreground/80"
            data-testid="reader-record-outline-partial-hint"
          >
            部分内容大纲
          </div>
        ) : null}
        <div
          ref={scrollAreaRef}
          className="max-h-[min(72vh,42rem)] overflow-y-auto py-2"
        >
          {surface === "deterministic" ? (
            <ol className="flex flex-col">
              {detItems.map((item) => (
                <DeterministicPanelRow
                  key={item.unitId}
                  item={item}
                  mode={detMode}
                  active={item.unitId === activeKey}
                  tabIndex={
                    panelOpen && item.unitId === focusedKey ? 0 : -1
                  }
                  onClick={() => onDetItemClick(item.unitId)}
                  onKeyDown={(event) => onDetItemKeyDown(event, item.unitId)}
                  registerRef={(el) =>
                    registerRowRef(detRowRefKey(item.unitId), el)
                  }
                />
              ))}
            </ol>
          ) : (
            <ol className="flex flex-col">
              {semItems.map((item) => (
                <SemanticPanelRow
                  key={item.nodeId}
                  item={item}
                  active={item.nodeId === activeKey}
                  tabIndex={
                    panelOpen && item.nodeId === focusedKey ? 0 : -1
                  }
                  onClick={() => onSemItemClick(item.nodeId)}
                  onKeyDown={(event) => onSemItemKeyDown(event, item.nodeId)}
                  registerRef={(el) =>
                    registerRowRef(semRowRefKey(item.nodeId), el)
                  }
                  sectionTranslationState={
                    sectionTranslationStates.get(item.nodeId) ?? null
                  }
                  onResolve={() => onResolveSection(item)}
                />
              ))}
            </ol>
          )}
        </div>
      </div>
    </div>
  );
}

export interface ReaderRecordNavigationRailProps {
  snapshot: ReaderPlateSnapshotDto;
  plateDocument: ReaderRecordPlateDocument;
  askOpen?: boolean;
  className?: string;
  /**
   * `viewport` keeps the rail fixed to the viewport right edge (legacy).
   * `canvas` fills the Reader Canvas outline slot; the slot owns responsive
   * visibility and viewport pinning inside ReaderRecordPlateSurface.
   */
  layout?: "viewport" | "canvas";
  /**
   * T5.6c: invoked after a successful explicit-section translation command
   * so the page can fetch a fresh snapshot and the body's translation layer
   * refreshes naturally. Optional — when omitted the rail still submits the
   * command but cannot refresh the body.
   */
  onRequestSnapshotReload?: () => void | Promise<void>;
}

// ---------------------------------------------------------------------------
// T5.6c — explicit-section translation per-row state
//
// Only one in-flight request per row. Succeeded → trigger snapshot reload
// (body translation layer refreshes naturally) and clear row state. Other
// outcomes (retry_later / already_covered_or_inflight / budget_exhausted /
// rejected / superseded) surface as a concise inline accessible message
// that stays until the user retries or the snapshot identity changes.
// ---------------------------------------------------------------------------

type SectionTranslationRowState =
  | { kind: "loading" }
  | { kind: "feedback"; outcome: string; message: string };

const SECTION_TRANSLATION_FEEDBACK: Record<string, string> = {
  retry_later: "稍后重试",
  already_covered_or_inflight: "已在解析中",
  budget_exhausted: "解析额度已用完",
  rejected: "无法解析此段",
  superseded: "已过期，请刷新",
};

function feedbackMessageForOutcome(outcome: string): string {
  return SECTION_TRANSLATION_FEEDBACK[outcome] ?? "无法解析此段";
}

export function ReaderRecordNavigationRail({
  snapshot,
  plateDocument,
  askOpen = false,
  className,
  layout = "viewport",
  onRequestSnapshotReload,
}: ReaderRecordNavigationRailProps) {
  const panelDomId = useId();
  const panelId = `reader-record-nav-panel-${panelDomId.replace(/:/g, "")}`;

  const detProjection = useMemo(
    () => projectReaderRecordNavigation(snapshot, plateDocument),
    [snapshot, plateDocument],
  );
  const semProjection = useMemo(
    () => projectReaderSemanticOutlineNav(snapshot, plateDocument),
    [snapshot, plateDocument],
  );

  const { mode: detMode, items: detItems, sourceIdentityKey } = detProjection;
  const hasL2 = semProjection.available;
  const semItems = semProjection.panelItems;
  const semTicks = semProjection.tickItems;

  // T5.6c: per-row section-translation state. Keyed by nodeId so each row
  // tracks its own loading / feedback lifecycle. Cleared on snapshot identity
  // or outline_revision change so stale feedback never persists across
  // projections.
  const [sectionTranslationStates, setSectionTranslationStates] = useState<
    Map<string, SectionTranslationRowState>
  >(() => new Map());
  const sectionTranslationStatesRef = useRef(sectionTranslationStates);
  useEffect(() => {
    sectionTranslationStatesRef.current = sectionTranslationStates;
  }, [sectionTranslationStates]);
  const sectionTranslationInFlightRef = useRef<Set<string>>(new Set());

  // Clear all row state when the snapshot identity or outline revision
  // changes — the projection is now a different tree.
  useEffect(() => {
    setSectionTranslationStates(new Map());
    sectionTranslationInFlightRef.current = new Set();
  }, [sourceIdentityKey, semProjection.outlineRevision]);

  const handleResolveSection = useCallback(
    async (item: ReaderSemanticOutlineNavItem) => {
      // Per-row re-entry guard: if a request is already in flight for this
      // node, ignore the additional click. This complements the loading-state
      // visual disabling and protects against double-submit race conditions.
      if (sectionTranslationInFlightRef.current.has(item.nodeId)) {
        return;
      }
      sectionTranslationInFlightRef.current.add(item.nodeId);
      setSectionTranslationStates((prev) => {
        const next = new Map(prev);
        next.set(item.nodeId, { kind: "loading" });
        return next;
      });

      const recordId = snapshot.record_id;
      const payload = {
        startUnitId: item.startUnitId,
        endUnitId: item.endUnitId,
        startAnchorSegmentId: item.startAnchorSegmentId,
        endAnchorSegmentId: item.endAnchorSegmentId,
        nodeId: item.nodeId,
        outlineRevision: semProjection.outlineRevision,
      };

      try {
        const response = await fetch(
          `/api/web/reader-plate/records/${encodeURIComponent(recordId)}/section-translation`,
          {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify(payload),
          },
        );
        const data = (await response.json().catch(() => null)) as
          | {
              ok: true;
              outcome: string;
              job_id: string | null;
              detail: string | null;
            }
          | { ok: false; status?: number; code?: string; message?: string }
          | null;

        if (data && data.ok) {
          if (data.outcome === "succeeded") {
            // Clear row state; the snapshot reload will refresh the body's
            // translation layer naturally.
            setSectionTranslationStates((prev) => {
              const next = new Map(prev);
              next.delete(item.nodeId);
              return next;
            });
            // Trigger the page's snapshot reload. Void-wrap so a rejected
            // promise does not surface as an unhandled rejection; the user
            // can retry the row.
            void Promise.resolve(onRequestSnapshotReload?.()).catch(() => {
              /* noop — reload failure is surfaced via the page's own error
                 toast; the row stays clickable for retry. */
            });
          } else {
            setSectionTranslationStates((prev) => {
              const next = new Map(prev);
              next.set(item.nodeId, {
                kind: "feedback",
                outcome: data.outcome,
                message: feedbackMessageForOutcome(data.outcome),
              });
              return next;
            });
          }
        } else {
          // BFF error — surface a concise inline message. The BFF never
          // leaks upstream exception messages; the generic fallback is safe.
          setSectionTranslationStates((prev) => {
            const next = new Map(prev);
            next.set(item.nodeId, {
              kind: "feedback",
              outcome: "rejected",
              message: "无法解析此段",
            });
            return next;
          });
        }
      } catch {
        setSectionTranslationStates((prev) => {
          const next = new Map(prev);
          next.set(item.nodeId, {
            kind: "feedback",
            outcome: "rejected",
            message: "网络异常，请稍后重试",
          });
          return next;
        });
      } finally {
        sectionTranslationInFlightRef.current.delete(item.nodeId);
      }
    },
    [snapshot.record_id, semProjection.outlineRevision, onRequestSnapshotReload],
  );

  const [outlineSurface, setOutlineSurface] =
    useState<ReaderOutlineSurface>("deterministic");
  const [activeUnitId, setActiveUnitId] = useState<string | null>(null);
  const [activeNodeId, setActiveNodeId] = useState<string | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const [focusedKey, setFocusedKey] = useState<string | null>(null);

  const wrapperRef = useRef<HTMLElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const rowRefsRef = useRef<Map<string, HTMLButtonElement>>(new Map());
  const closeTimerRef = useRef<number | null>(null);
  const scrollLockTimerRef = useRef<number | null>(null);
  const lockedKeyRef = useRef<string | null>(null);
  const targetMapRef = useRef<Map<string, HTMLElement>>(new Map());
  const sourceIdentityKeyRef = useRef(sourceIdentityKey);
  const outlineRevisionRef = useRef(semProjection.outlineRevision);
  const outlineSurfaceRef = useRef(outlineSurface);

  useEffect(() => {
    outlineSurfaceRef.current = outlineSurface;
  }, [outlineSurface]);

  // No L2 → force deterministic surface.
  useEffect(() => {
    if (!hasL2 && outlineSurface === "semantic") {
      setOutlineSurface("deterministic");
      setActiveNodeId(null);
    }
  }, [hasL2, outlineSurface]);

  // Invalidate cache when det items or semantic tree identity changes.
  useEffect(() => {
    targetMapRef.current = new Map();
  }, [detItems, semProjection.outlineRevision, semItems.length]);

  // Source identity reset: base_id:generation change clears all rail state.
  useEffect(() => {
    if (sourceIdentityKeyRef.current === sourceIdentityKey) {
      return;
    }
    sourceIdentityKeyRef.current = sourceIdentityKey;
    setOutlineSurface("deterministic");
    setActiveUnitId(null);
    setActiveNodeId(null);
    setFocusedKey(null);
    lockedKeyRef.current = null;
    if (scrollLockTimerRef.current !== null) {
      window.clearTimeout(scrollLockTimerRef.current);
      scrollLockTimerRef.current = null;
    }
    // Cancel pending hover-close from the previous source so it cannot close
    // a panel re-opened under the new identity.
    if (closeTimerRef.current !== null) {
      window.clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
    }
    targetMapRef.current = new Map();
  }, [sourceIdentityKey]);

  // Same-source outline_revision refresh: drop missing active/focus; keep panel.
  useEffect(() => {
    if (outlineRevisionRef.current === semProjection.outlineRevision) {
      return;
    }
    outlineRevisionRef.current = semProjection.outlineRevision;
    if (!semProjection.available) {
      setActiveNodeId(null);
      if (outlineSurfaceRef.current === "semantic") {
        setFocusedKey(null);
      }
      return;
    }
    const ids = new Set(semProjection.panelItems.map((n) => n.nodeId));
    setActiveNodeId((prev) => (prev && ids.has(prev) ? prev : null));
    setFocusedKey((prev) => {
      if (outlineSurfaceRef.current !== "semantic") return prev;
      if (prev && ids.has(prev)) return prev;
      return null;
    });
    targetMapRef.current = new Map();
  }, [semProjection.outlineRevision, semProjection.available, semProjection.panelItems]);

  const clearCloseTimer = useCallback(() => {
    if (closeTimerRef.current !== null) {
      window.clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
    }
  }, []);

  const openPanel = useCallback(() => {
    clearCloseTimer();
    setPanelOpen(true);
  }, [clearCloseTimer]);

  const closePanel = useCallback(() => {
    clearCloseTimer();
    setPanelOpen(false);
  }, [clearCloseTimer]);

  const keepOpenPanel = useCallback(() => {
    if (!panelOpen) return;
    openPanel();
  }, [openPanel, panelOpen]);

  const scheduleClose = useCallback(() => {
    clearCloseTimer();
    closeTimerRef.current = window.setTimeout(() => {
      setPanelOpen(false);
    }, 220);
  }, [clearCloseTimer]);

  const lockActiveKey = useCallback((key: string) => {
    lockedKeyRef.current = key;
    if (scrollLockTimerRef.current !== null) {
      window.clearTimeout(scrollLockTimerRef.current);
    }
    scrollLockTimerRef.current = window.setTimeout(() => {
      lockedKeyRef.current = null;
    }, SCROLL_LOCK_MS);
  }, []);

  // --- Scroll-based active (deterministic or semantic) -------------------
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (outlineSurface === "deterministic" && detItems.length === 0) return;
    if (outlineSurface === "semantic" && semItems.length === 0) return;

    const fenceSourceIdentityKey = sourceIdentityKey;
    const fenceSurface = outlineSurface;
    const scrollContainer = getScrollContainer() ?? window;
    let rafId: number | null = null;
    let pending = false;

    const updateActive = () => {
      if (pending) return;
      pending = true;
      rafId = window.requestAnimationFrame(() => {
        pending = false;
        if (sourceIdentityKeyRef.current !== fenceSourceIdentityKey) {
          return;
        }
        if (outlineSurfaceRef.current !== fenceSurface) {
          return;
        }
        if (lockedKeyRef.current) return;

        const safeTop = TOPBAR_SAFE_HEIGHT + ACTIVE_SAFE_OFFSET;

        if (fenceSurface === "semantic") {
          const activeId = computeActiveOutlineNodeId(
            semItems,
            semTicks,
            semProjection.orderedUnitIds,
            semProjection.unitOrderById,
            targetMapRef.current,
            safeTop,
          );
          setActiveNodeId(activeId);
        } else {
          const activeId = computeActiveUnitId(
            detItems,
            targetMapRef.current,
            safeTop,
            detMode,
          );
          setActiveUnitId(activeId);
        }
      });
    };

    scrollContainer.addEventListener("scroll", updateActive, { passive: true });
    window.addEventListener("resize", updateActive);
    updateActive();

    return () => {
      scrollContainer.removeEventListener("scroll", updateActive);
      window.removeEventListener("resize", updateActive);
      if (rafId !== null) {
        window.cancelAnimationFrame(rafId);
      }
    };
  }, [
    detItems,
    detMode,
    outlineSurface,
    semItems,
    semTicks,
    semProjection.orderedUnitIds,
    semProjection.unitOrderById,
    sourceIdentityKey,
  ]);

  // L0 only: default active to first unit.
  useEffect(() => {
    if (outlineSurface !== "deterministic") return;
    if (detMode !== "L0") return;
    if (detItems.length > 0 && activeUnitId === null) {
      setActiveUnitId(detItems[0].unitId);
    }
  }, [outlineSurface, detMode, detItems, activeUnitId]);

  // Initialize focused key when the panel opens.
  useEffect(() => {
    if (panelOpen && focusedKey === null) {
      if (outlineSurface === "semantic") {
        setFocusedKey(activeNodeId ?? semItems[0]?.nodeId ?? null);
      } else {
        setFocusedKey(activeUnitId ?? detItems[0]?.unitId ?? null);
      }
    }
    if (!panelOpen) {
      setFocusedKey(null);
    }
  }, [
    panelOpen,
    focusedKey,
    outlineSurface,
    activeNodeId,
    activeUnitId,
    semItems,
    detItems,
  ]);

  // Focus the row matching focusedKey when it changes (keyboard nav).
  // Map key is surface-namespaced so node_id === unitId cannot collide.
  useEffect(() => {
    if (!panelOpen || focusedKey === null) return;
    const mapKey =
      outlineSurfaceRef.current === "semantic"
        ? semRowRefKey(focusedKey)
        : detRowRefKey(focusedKey);
    const row = rowRefsRef.current.get(mapKey);
    row?.focus();
  }, [focusedKey, panelOpen, outlineSurface]);

  useEffect(() => {
    return () => {
      if (closeTimerRef.current !== null) {
        window.clearTimeout(closeTimerRef.current);
      }
      if (scrollLockTimerRef.current !== null) {
        window.clearTimeout(scrollLockTimerRef.current);
      }
    };
  }, []);

  // --- Deterministic click (existing semantics: set active even if no DOM) --
  const handleDetItemClick = useCallback(
    (unitId: string) => {
      const target = resolveValidatedUnitTarget(unitId, targetMapRef.current);
      if (target) {
        scrollElementIntoSafeView(target);
      }
      setActiveUnitId(unitId);
      setFocusedKey(unitId);
      lockActiveKey(unitId);
    },
    [lockActiveKey],
  );

  // --- Semantic click (Phase 0 C: no target → no active / lock / scroll) ---
  const handleSemItemClick = useCallback(
    (nodeId: string) => {
      const item = semItems.find((n) => n.nodeId === nodeId);
      if (!item) return;
      const target = resolveValidatedOutlineTarget(
        item,
        targetMapRef.current,
      );
      if (!target) {
        // Fail closed for activation; keep keyboard focus on current row.
        return;
      }
      scrollElementIntoSafeView(target);
      setActiveNodeId(nodeId);
      setFocusedKey(nodeId);
      lockActiveKey(nodeId);
    },
    [lockActiveKey, semItems],
  );

  const handleDetItemKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLButtonElement>, unitId: string) => {
      const currentIndex = detItems.findIndex((item) => item.unitId === unitId);
      if (currentIndex === -1) return;

      switch (event.key) {
        case "Enter":
        case " ": {
          event.preventDefault();
          handleDetItemClick(unitId);
          break;
        }
        case "ArrowDown": {
          event.preventDefault();
          const nextIndex = Math.min(detItems.length - 1, currentIndex + 1);
          setFocusedKey(detItems[nextIndex].unitId);
          break;
        }
        case "ArrowUp": {
          event.preventDefault();
          const prevIndex = Math.max(0, currentIndex - 1);
          setFocusedKey(detItems[prevIndex].unitId);
          break;
        }
        case "Home": {
          event.preventDefault();
          setFocusedKey(detItems[0].unitId);
          break;
        }
        case "End": {
          event.preventDefault();
          setFocusedKey(detItems[detItems.length - 1].unitId);
          break;
        }
        case "Escape": {
          event.preventDefault();
          closePanel();
          triggerRef.current?.focus();
          break;
        }
      }
    },
    [detItems, handleDetItemClick, closePanel],
  );

  const handleSemItemKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLButtonElement>, nodeId: string) => {
      const currentIndex = semItems.findIndex((item) => item.nodeId === nodeId);
      if (currentIndex === -1) return;

      switch (event.key) {
        case "Enter":
        case " ": {
          event.preventDefault();
          handleSemItemClick(nodeId);
          break;
        }
        case "ArrowDown": {
          event.preventDefault();
          const nextIndex = Math.min(semItems.length - 1, currentIndex + 1);
          setFocusedKey(semItems[nextIndex].nodeId);
          break;
        }
        case "ArrowUp": {
          event.preventDefault();
          const prevIndex = Math.max(0, currentIndex - 1);
          setFocusedKey(semItems[prevIndex].nodeId);
          break;
        }
        case "Home": {
          event.preventDefault();
          setFocusedKey(semItems[0].nodeId);
          break;
        }
        case "End": {
          event.preventDefault();
          setFocusedKey(semItems[semItems.length - 1].nodeId);
          break;
        }
        case "Escape": {
          event.preventDefault();
          closePanel();
          triggerRef.current?.focus();
          break;
        }
      }
    },
    [semItems, handleSemItemClick, closePanel],
  );

  const handleSurfaceChange = useCallback(
    (next: ReaderOutlineSurface) => {
      if (next === "semantic" && !hasL2) return;
      setOutlineSurface(next);
      setFocusedKey(null);
      lockedKeyRef.current = null;
      if (scrollLockTimerRef.current !== null) {
        window.clearTimeout(scrollLockTimerRef.current);
        scrollLockTimerRef.current = null;
      }
    },
    [hasL2],
  );

  const handleTriggerKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLButtonElement>) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        if (panelOpen) {
          closePanel();
        } else {
          openPanel();
        }
      }
    },
    [panelOpen, openPanel, closePanel],
  );

  const handleTriggerClick = useCallback(() => {
    if (panelOpen) {
      closePanel();
    } else {
      openPanel();
    }
  }, [panelOpen, openPanel, closePanel]);

  const handleBlur = useCallback((event: React.FocusEvent<HTMLElement>) => {
    if (!wrapperRef.current?.contains(event.relatedTarget as Node)) {
      setPanelOpen(false);
    }
  }, []);

  const handleMouseLeave = useCallback(
    (event: React.MouseEvent<HTMLElement>) => {
      const related = event.relatedTarget;
      if (
        related instanceof Element &&
        wrapperRef.current?.contains(related as Node)
      ) {
        return;
      }
      scheduleClose();
    },
    [scheduleClose],
  );

  const handleTickMouseEnter = useCallback(() => {
    // Panel has a single stable vertical position; hover only opens it.
    openPanel();
  }, [openPanel]);

  const registerRowRef = useCallback(
    (key: string, el: HTMLButtonElement | null) => {
      if (el) {
        rowRefsRef.current.set(key, el);
      } else {
        rowRefsRef.current.delete(key);
      }
    },
    [],
  );

  const getRowRef = useCallback(
    (key: string) => rowRefsRef.current.get(key) ?? null,
    [],
  );

  if (detItems.length === 0) {
    return null;
  }

  const isCanvas = layout === "canvas";
  const effectiveSurface: ReaderOutlineSurface =
    outlineSurface === "semantic" && hasL2 ? "semantic" : "deterministic";

  const tickKeys =
    effectiveSurface === "semantic"
      ? semTicks.map((t) => t.nodeId)
      : detItems.map((i) => i.unitId);

  const activeKey =
    effectiveSurface === "semantic" ? activeNodeId : activeUnitId;

  const activeItemIndex =
    activeKey === null
      ? -1
      : effectiveSurface === "semantic"
        ? semItems.findIndex((item) => item.nodeId === activeKey)
        : detItems.findIndex((item) => item.unitId === activeKey);
  const activeIndexForLabel =
    activeItemIndex >= 0 ? activeItemIndex + 1 : null;

  const triggerLabel =
    effectiveSurface === "semantic"
      ? buildSemanticTriggerLabel(panelOpen, activeIndexForLabel)
      : buildNavigationTriggerLabel(detMode, panelOpen, activeIndexForLabel);

  const navAriaLabel =
    effectiveSurface === "semantic" ? "内容大纲" : "阅读定位";

  return (
    <nav
      ref={wrapperRef}
      aria-label={navAriaLabel}
      data-testid="reader-record-navigation-rail"
      data-navigation-mode={
        effectiveSurface === "semantic" ? "L2" : detMode
      }
      data-outline-surface={effectiveSurface}
      data-has-semantic-outline={hasL2 ? "true" : "false"}
      data-layout={layout}
      className={cn(
        "hidden md:flex",
        panelOpen
          ? "z-[var(--reader-z-outline-panel-expanded,50)]"
          : "z-[var(--reader-z-outline-rail,30)]",
        isCanvas
          ? "reader-record-navigation-rail--canvas absolute right-0 top-1/2 h-[min(72vh,42rem)] w-full -translate-y-1/2"
          : "fixed right-3 top-1/2 h-[min(72vh,42rem)] -translate-y-1/2",
        !isCanvas &&
          askOpen &&
          "2xl:right-[clamp(31.75rem,calc((100vw-124px-96ch)/2+0.25rem),38.25rem)]",
        className,
      )}
      onMouseLeave={handleMouseLeave}
      onBlur={handleBlur}
    >
      <NavigationPanel
        panelId={panelId}
        surface={effectiveSurface}
        hasL2={hasL2}
        isPartial={semProjection.isPartial}
        detMode={detMode}
        detItems={detItems}
        semItems={semItems}
        activeKey={activeKey}
        focusedKey={focusedKey}
        panelOpen={panelOpen}
        getRowRef={getRowRef}
        onDetItemClick={handleDetItemClick}
        onSemItemClick={handleSemItemClick}
        onDetItemKeyDown={handleDetItemKeyDown}
        onSemItemKeyDown={handleSemItemKeyDown}
        onSurfaceChange={handleSurfaceChange}
        onMouseEnter={keepOpenPanel}
        onMouseLeave={handleMouseLeave}
        registerRowRef={registerRowRef}
        sectionTranslationStates={sectionTranslationStates}
        onResolveSection={handleResolveSection}
      />

      <button
        ref={triggerRef}
        type="button"
        data-testid="reader-record-outline-trigger"
        data-reader-record-outline-trigger="true"
        className="relative flex min-h-[24px] min-w-[24px] cursor-pointer items-center justify-end"
        aria-label={triggerLabel}
        aria-expanded={panelOpen}
        aria-controls={panelId}
        onClick={handleTriggerClick}
        onKeyDown={handleTriggerKeyDown}
      >
        <VisualTicks
          surface={effectiveSurface}
          tickKeys={tickKeys}
          activeKey={
            effectiveSurface === "semantic"
              ? // Highlight root tick when active is root or descendant of root.
                activeNodeId
                  ? (semTicks.find(
                      (t) =>
                        t.nodeId === activeNodeId ||
                        semItems.some(
                          (n) =>
                            n.nodeId === activeNodeId &&
                            (n.nodeId === t.nodeId ||
                              isDescendantOf(semItems, n.nodeId, t.nodeId)),
                        ),
                    )?.nodeId ?? null)
                  : null
              : activeUnitId
          }
          onTickMouseEnter={handleTickMouseEnter}
        />
      </button>
    </nav>
  );
}

function isDescendantOf(
  items: ReaderSemanticOutlineNavItem[],
  nodeId: string,
  ancestorId: string,
): boolean {
  let current = items.find((n) => n.nodeId === nodeId);
  const seen = new Set<string>();
  while (current?.parentNodeId) {
    if (current.parentNodeId === ancestorId) return true;
    if (seen.has(current.parentNodeId)) return false;
    seen.add(current.parentNodeId);
    current = items.find((n) => n.nodeId === current!.parentNodeId);
  }
  return false;
}

interface DeterministicPanelRowProps {
  item: ReaderRecordNavigationItem;
  mode: ReaderRecordNavigationMode;
  active: boolean;
  tabIndex?: number;
  onClick: () => void;
  onKeyDown: (event: React.KeyboardEvent<HTMLButtonElement>) => void;
  registerRef: (el: HTMLButtonElement | null) => void;
}

function DeterministicPanelRow({
  item,
  mode,
  active,
  tabIndex = -1,
  onClick,
  onKeyDown,
  registerRef,
}: DeterministicPanelRowProps) {
  const indexLabel =
    mode === "L1"
      ? `第 ${item.fallbackIndex + 1} 项`
      : `第 ${item.fallbackIndex + 1} 段`;

  return (
    <li>
      <button
        ref={registerRef}
        type="button"
        aria-current={active ? "true" : undefined}
        tabIndex={tabIndex}
        onClick={onClick}
        onKeyDown={onKeyDown}
        className={cn(
          "relative w-full px-2.5 py-1.5 text-left transition-colors duration-150 ease-[var(--cl-ease-standard)]",
          "focus-visible:outline-none focus-visible:bg-ink/[0.035] focus-visible:ring-1 focus-visible:ring-lens-blue/30",
          active
            ? "bg-[var(--app-control-current)] font-medium text-ink"
            : "text-ink/60 hover:bg-ink/[0.035] hover:text-ink",
        )}
      >
        <span className="block truncate text-[11px] leading-snug">
          {item.label}
        </span>
        <span className="block text-[9px] leading-snug text-muted-foreground/75">
          {indexLabel}
        </span>
      </button>
    </li>
  );
}

interface SemanticPanelRowProps {
  item: ReaderSemanticOutlineNavItem;
  active: boolean;
  tabIndex?: number;
  onClick: () => void;
  onKeyDown: (event: React.KeyboardEvent<HTMLButtonElement>) => void;
  registerRef: (el: HTMLButtonElement | null) => void;
  // T5.6c: per-row section-translation state. null = idle.
  sectionTranslationState: SectionTranslationRowState | null;
  onResolve: () => void;
}

function SemanticPanelRow({
  item,
  active,
  tabIndex = -1,
  onClick,
  onKeyDown,
  registerRef,
  sectionTranslationState,
  onResolve,
}: SemanticPanelRowProps) {
  const depth = Math.min(Math.max(item.depth, 1), 3);
  const levelLabel =
    depth === 1 ? "一级" : depth === 2 ? "二级" : "三级";
  const isLoading = sectionTranslationState?.kind === "loading";
  const feedback =
    sectionTranslationState?.kind === "feedback"
      ? sectionTranslationState
      : null;

  return (
    <li className="relative">
      <button
        ref={registerRef}
        type="button"
        data-testid={`reader-record-outline-node-${item.nodeId}`}
        data-outline-node-id={item.nodeId}
        data-outline-depth={item.depth}
        aria-label={`${levelLabel}，${item.title}`}
        aria-current={active ? "true" : undefined}
        tabIndex={tabIndex}
        onClick={onClick}
        onKeyDown={onKeyDown}
        style={{ paddingLeft: `${8 + (depth - 1) * 12}px` }}
        className={cn(
          "relative w-full py-1.5 text-left transition-colors duration-150 ease-[var(--cl-ease-standard)]",
          // Reserve room on the right for the T5.6c action chip so the title
          // truncates before it can underlap the chip. T5.6c-P2: the feedback
          // state renders an inline feedback span (max-w 3.25rem) + "重试"
          // button + gap + right inset 1.5 alongside the title; pr-[6rem]
          // covers both idle (single chip) and feedback/retry (span + button)
          // right-side states without clipping the title.
          "pr-[6rem]",
          "focus-visible:outline-none focus-visible:bg-ink/[0.035] focus-visible:ring-1 focus-visible:ring-lens-blue/30",
          active
            ? "bg-[var(--app-control-current)] font-medium text-ink"
            : "text-ink/60 hover:bg-ink/[0.035] hover:text-ink",
        )}
      >
        <span className="block truncate text-[11px] leading-snug">
          {item.title}
        </span>
      </button>
      {/* T5.6c: "解析此段" action chip. The chip is its own accessible
          command tab stop (tabIndex=0) so keyboard users can reach and
          activate it independently of the parent row's roving tabindex.
          The parent row's roving tabindex (0 only on the focused row,
          -1 elsewhere) is preserved because the chip is a sibling button,
          not a child of the row button — Tab moves between the row's
          button and the chip without disturbing the row's tabindex.
          stopPropagation on click/keydown prevents the parent row's
          onClick (body scroll) and onKeyDown (ArrowDown/Escape roving)
          from firing when the chip is the target.
          T5.6c-P2: feedback state no longer replaces the action — it
          renders alongside a "重试" button so the user can retry without
          waiting for a snapshot refresh. The retry button keeps the same
          testid (`reader-record-outline-resolve-{nodeId}`) so the
          accessible action locator is stable across idle/feedback states.
          already_covered_or_inflight intentionally still routes through
          the same fetch path so the backend queued-recovery drain can be
          re-triggered by the user. */}
      {isLoading ? (
        <span
          data-testid={`reader-record-outline-resolve-loading-${item.nodeId}`}
          aria-live="polite"
          className="pointer-events-none absolute right-1.5 top-1/2 -translate-y-1/2 text-[9px] leading-tight text-muted-foreground/85"
        >
          正在解析…
        </span>
      ) : feedback ? (
        <div className="absolute right-1.5 top-1/2 flex -translate-y-1/2 items-center gap-1">
          <span
            data-testid={`reader-record-outline-resolve-feedback-${item.nodeId}`}
            aria-live="polite"
            className="max-w-[3.25rem] truncate text-[9px] leading-tight text-muted-foreground/85"
          >
            {feedback.message}
          </span>
          <button
            type="button"
            tabIndex={0}
            data-testid={`reader-record-outline-resolve-${item.nodeId}`}
            data-resolve-action="retry"
            aria-label={`重试：${item.title}`}
            onClick={(event) => {
              event.stopPropagation();
              onResolve();
            }}
            onKeyDown={(event) => {
              // Allow Enter / Space to activate the retry button; stop
              // propagation so the parent row's onKeyDown
              // (ArrowDown/Escape roving) does not fire and the event
              // does not bubble to the row's onClick path.
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                event.stopPropagation();
                onResolve();
              }
            }}
            className={cn(
              "rounded-sm px-1.5 py-0.5 text-[9px] leading-tight",
              "text-ink/55 hover:bg-ink/[0.06] hover:text-ink",
              "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-lens-blue/30",
              "transition-colors duration-100 ease-[var(--cl-ease-standard)]",
            )}
          >
            重试
          </button>
        </div>
      ) : (
        <button
          type="button"
          tabIndex={0}
          data-testid={`reader-record-outline-resolve-${item.nodeId}`}
          data-resolve-action="resolve"
          aria-label={`解析此段：${item.title}`}
          onClick={(event) => {
            event.stopPropagation();
            onResolve();
          }}
          onKeyDown={(event) => {
            // Allow Enter / Space to activate the chip; stop propagation so
            // the parent row's onKeyDown (ArrowDown/Escape roving) does not
            // fire and the event does not bubble to the row's onClick path.
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              event.stopPropagation();
              onResolve();
            }
          }}
          className={cn(
            "absolute right-1.5 top-1/2 -translate-y-1/2 rounded-sm px-1.5 py-0.5 text-[9px] leading-tight",
            "text-ink/55 hover:bg-ink/[0.06] hover:text-ink",
            "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-lens-blue/30",
            "transition-colors duration-100 ease-[var(--cl-ease-standard)]",
          )}
        >
          解析此段
        </button>
      )}
    </li>
  );
}
