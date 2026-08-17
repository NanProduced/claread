"use client";

import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";

import { cn } from "@/lib/cn";
import {
  buildOutlineScopeKey,
  projectReaderOutlineView,
  selectMostSpecificCoveringNode,
  type OutlineItem,
} from "@/lib/reader-plate/projection/reader-outline-view";
import type { ReaderRecordPlateDocument } from "@/lib/reader-plate/projection/reader-record-plate-document";
import type { ReaderPlateSnapshotDto } from "@/types/api/reader-plate";
import {
  isReaderRecordNavigableNode,
  READER_RECORD_ANCHOR_SEGMENT_ATTR as ANCHOR_SEGMENT_ATTR,
  READER_RECORD_NAVIGABLE_NODE_SELECTOR as NAVIGABLE_NODE_SELECTOR,
  READER_RECORD_PLATE_DOCUMENT_SELECTOR as PLATE_DOCUMENT_SELECTOR,
  READER_RECORD_UNIT_ID_ATTR as UNIT_ID_ATTR,
  READER_RECORD_UNIT_START_ATTR as UNIT_START_ATTR,
} from "@/lib/reader-plate/reader-record-dom-contract";

const TOPBAR_SAFE_HEIGHT = 56; // px, sticky topbar + small gap
const SCROLL_LOCK_MS = 700;
const ACTIVE_SAFE_OFFSET = 8;

// The DOM navigation contract (attribute names, selectors, predicates) lives in
// reader-record-dom-contract and is imported above — shared verbatim with the
// Plate node renderer and the agentic navigation adapter. The rail reads nodes
// through it and never branches on the node *value*.

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

  const nodes = body.querySelectorAll<HTMLElement>(NAVIGABLE_NODE_SELECTOR);

  let fallback: HTMLElement | null = null;
  for (const paragraph of nodes) {
    if (paragraph.getAttribute(UNIT_ID_ATTR) !== unitId) continue;
    if (paragraph.getAttribute(UNIT_START_ATTR) === "true") {
      return paragraph;
    }
    if (fallback === null) {
      fallback = paragraph;
    }
  }

  return fallback;
}

/**
 * Prefer the item's start anchor segment when it matches the start unit on the
 * DOM; otherwise the unit-start paragraph.
 */
function findOutlineNodeTarget(
  item: OutlineItem,
  plateRoot: HTMLElement | null = getPlateDocumentRoot(),
): HTMLElement | null {
  const body = plateRoot;
  if (!body) return null;

  if (item.target.anchorSegmentId) {
    const nodes = body.querySelectorAll<HTMLElement>(NAVIGABLE_NODE_SELECTOR);
    for (const paragraph of nodes) {
      if (
        paragraph.getAttribute(ANCHOR_SEGMENT_ATTR) ===
          item.target.anchorSegmentId &&
        paragraph.getAttribute(UNIT_ID_ATTR) === item.target.unitId
      ) {
        return paragraph;
      }
    }
  }

  return findUnitTarget(item.target.unitId, body);
}

/**
 * Cache entry is valid only when it is still a live paragraph for the item's
 * start unit under the current plate document root. Detached or remounted nodes
 * (common after Plate setValue) must not drive scroll spy or click positioning.
 */
function isValidCachedUnitTarget(
  unitId: string,
  el: HTMLElement,
  plateRoot: HTMLElement | null,
): boolean {
  if (!el.isConnected) return false;
  if (!plateRoot || !plateRoot.contains(el)) return false;
  if (!isReaderRecordNavigableNode(el)) return false;
  if (el.getAttribute(UNIT_ID_ATTR) !== unitId) return false;
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
  namespace = "",
): HTMLElement | null {
  const cacheKey = `${namespace}|u:${unitId}`;
  const cached = map.get(cacheKey);
  if (cached) {
    if (isValidCachedUnitTarget(unitId, cached, plateRoot)) {
      return cached;
    }
    map.delete(cacheKey);
  }

  const resolved = findUnitTarget(unitId, plateRoot);
  if (resolved && isValidCachedUnitTarget(unitId, resolved, plateRoot)) {
    map.set(cacheKey, resolved);
    return resolved;
  }
  return null;
}

function isValidCachedOutlineTarget(
  item: OutlineItem,
  el: HTMLElement,
  plateRoot: HTMLElement | null,
): boolean {
  if (!el.isConnected) return false;
  if (!plateRoot || !plateRoot.contains(el)) return false;
  if (!isReaderRecordNavigableNode(el)) return false;
  if (el.getAttribute(UNIT_ID_ATTR) !== item.target.unitId) return false;
  if (item.target.anchorSegmentId) {
    if (
      el.getAttribute(ANCHOR_SEGMENT_ATTR) !== item.target.anchorSegmentId
    ) {
      return false;
    }
  }
  return true;
}

function resolveValidatedOutlineTarget(
  item: OutlineItem,
  map: Map<string, HTMLElement>,
  plateRoot: HTMLElement | null = getPlateDocumentRoot(),
  namespace = "",
): HTMLElement | null {
  const cacheKey = `${namespace}|o:${item.key}`;
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
 * Active outline item for scroll spy:
 * - lead zone: every depth=1 root start is still below safeTop → null
 * - else: unit under safeTop, then most specific covering item
 */
function computeActiveOutlineItemId(
  panelItems: OutlineItem[],
  tickItems: OutlineItem[],
  orderedUnitIds: string[],
  unitOrderById: Map<string, number>,
  targetMap: Map<string, HTMLElement>,
  safeTop: number,
  namespace = "",
): string | null {
  const plateRoot = getPlateDocumentRoot();

  let anyRootAbove = false;
  for (const root of tickItems) {
    const target = resolveValidatedUnitTarget(
      root.target.unitId,
      targetMap,
      plateRoot,
      namespace,
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
    const target = resolveValidatedUnitTarget(
      unitId,
      targetMap,
      plateRoot,
      namespace,
    );
    if (!target) continue;
    if (target.getBoundingClientRect().top <= safeTop) {
      currentUnitId = unitId;
    } else if (currentUnitId !== null) {
      break;
    }
  }

  // A group parent has no independent landing point — never the active section.
  return selectMostSpecificCoveringNode(
    panelItems.filter((item) => item.role === "section"),
    unitOrderById,
    currentUnitId,
  );
}

function buildOutlineTriggerLabel(
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

  const body = document.querySelector<HTMLElement>(PLATE_DOCUMENT_SELECTOR);
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

function isDescendantOf(
  items: OutlineItem[],
  nodeId: string,
  ancestorId: string,
): boolean {
  let current = items.find((n) => n.key === nodeId);
  const seen = new Set<string>();
  while (current?.parentKey) {
    if (current.parentKey === ancestorId) return true;
    if (seen.has(current.parentKey)) return false;
    seen.add(current.parentKey);
    current = items.find((n) => n.key === current!.parentKey);
  }
  return false;
}

// ---------------------------------------------------------------------------
// Visual ticks — purely decorative, aria-hidden.
// ---------------------------------------------------------------------------

interface VisualTicksProps {
  tickKeys: string[];
  activeKey: string | null;
  onTickMouseEnter: () => void;
}

function VisualTicks({
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
          key={key}
          className="group relative flex min-h-[7px] w-10 flex-1 max-h-4 shrink items-center justify-end rounded-sm px-1"
          data-navigation-tick-key={key}
          data-outline-node-id={key}
          onMouseEnter={onTickMouseEnter}
        >
          <span
            className={cn(
              "block h-[1.5px] rounded-full transition-all duration-150 ease-[var(--cl-ease-standard)]",
              key === activeKey
                ? "w-5 bg-lens-blue"
                : "w-3.5 bg-ink/18 group-hover:bg-ink/40",
            )}
          />
        </span>
      ))}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Navigation panel
// ---------------------------------------------------------------------------

interface NavigationPanelProps {
  panelId: string;
  isPartial: boolean;
  items: OutlineItem[];
  activeKey: string | null;
  focusedKey: string | null;
  panelOpen: boolean;
  getRowRef: (key: string) => HTMLButtonElement | null;
  onItemClick: (key: string) => void;
  onItemKeyDown: (
    event: React.KeyboardEvent<HTMLButtonElement>,
    key: string,
  ) => void;
  onMouseEnter: () => void;
  onMouseLeave: (event: React.MouseEvent<HTMLElement>) => void;
  registerRowRef: (key: string, el: HTMLButtonElement | null) => void;
}

function NavigationPanel({
  panelId,
  isPartial,
  items,
  activeKey,
  focusedKey,
  panelOpen,
  getRowRef,
  onItemClick,
  onItemKeyDown,
  onMouseEnter,
  onMouseLeave,
  registerRowRef,
}: NavigationPanelProps) {
  const scrollAreaRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!panelOpen) return;
    const row =
      (focusedKey ? getRowRef(focusedKey) : null) ??
      (activeKey ? getRowRef(activeKey) : null);
    const scrollArea = scrollAreaRef.current;
    if (
      row &&
      scrollArea &&
      scrollArea.contains(row) &&
      typeof row.scrollIntoView === "function"
    ) {
      row.scrollIntoView({ block: "nearest", inline: "nearest" });
    }
  }, [panelOpen, activeKey, focusedKey, getRowRef]);

  return (
    <div
      id={panelId}
      data-testid="reader-record-navigation-panel"
      data-reader-record-navigation-panel="true"
      className={cn(
        "reader-record-navigation-panel motion-reduce:transition-none",
        "transition-[transform,opacity,visibility] duration-200 ease-[var(--cl-ease-standard)]",
        "absolute right-0 top-1/2 z-20 max-h-[min(72vh,42rem)] origin-right -translate-y-1/2",
        panelOpen
          ? "visible scale-100 opacity-100"
          : "invisible scale-95 opacity-0 pointer-events-none",
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
        {isPartial ? (
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
          <ol className="flex flex-col">
            {items.map((item) => (
              <OutlineRow
                key={item.key}
                item={item}
                active={item.key === activeKey}
                tabIndex={panelOpen && item.key === focusedKey ? 0 : -1}
                onClick={() => onItemClick(item.key)}
                onKeyDown={(event) => onItemKeyDown(event, item.key)}
                registerRef={(el) => registerRowRef(item.key, el)}
              />
            ))}
          </ol>
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
}

export function ReaderRecordNavigationRail({
  snapshot,
  plateDocument,
  askOpen = false,
  className,
  layout = "viewport",
}: ReaderRecordNavigationRailProps) {
  const panelDomId = useId();
  const panelId = `reader-record-nav-panel-${panelDomId.replace(/:/g, "")}`;

  // The single, source-agnostic outline the UI renders. When unavailable the
  // rail renders nothing (no unit-list fallback, no placeholder).
  const viewModel = useMemo(
    () => projectReaderOutlineView(snapshot, plateDocument),
    [snapshot, plateDocument],
  );

  const available = viewModel.available;
  const isPartial = viewModel.isPartial;
  const items = viewModel.panelItems;
  const ticks = viewModel.tickItems;
  const orderedUnitIds = viewModel.orderedUnitIds;
  const unitOrderById = viewModel.unitOrderById;
  const outlineRevision = viewModel.identity.revision;
  const sourceKind = viewModel.identity.sourceKind;
  // Full isolation identity (incl. sourceKind) — see buildOutlineScopeKey.
  const scopeKey = buildOutlineScopeKey(viewModel.identity);

  const [activeKey, setActiveKey] = useState<string | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const [focusedKey, setFocusedKey] = useState<string | null>(null);

  // Same-source revision refresh: keep only still-navigable active/focus and
  // the panel open. If a key that was a section became a group (or vanished),
  // drop it from active/focus, release the scroll lock so scroll-spy resumes,
  // and (while open) move focus to the first remaining section. These value
  // adjustments happen during render on the revision fence (adjust-state-when-
  // props-change); the outline-revision effect below only clears ref-side
  // state (scroll lock, stale close timer, target cache).
  const [prevOutlineRevision, setPrevOutlineRevision] = useState(outlineRevision);
  if (prevOutlineRevision !== outlineRevision) {
    setPrevOutlineRevision(outlineRevision);
    if (!available) {
      if (activeKey !== null) {
        setActiveKey(null);
      }
      if (focusedKey !== null) {
        setFocusedKey(null);
      }
    } else {
      const sectionKeys = new Set(
        items.filter((n) => n.role === "section").map((n) => n.key),
      );
      const firstSection =
        items.find((n) => n.role === "section")?.key ?? null;
      setActiveKey((prev) => (prev && sectionKeys.has(prev) ? prev : null));
      setFocusedKey((prev) => {
        if (prev && sectionKeys.has(prev)) return prev;
        return panelOpen ? firstSection : null;
      });
    }
  }

  const wrapperRef = useRef<HTMLElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const rowRefsRef = useRef<Map<string, HTMLButtonElement>>(new Map());
  const closeTimerRef = useRef<number | null>(null);
  const scrollLockTimerRef = useRef<number | null>(null);
  const lockedKeyRef = useRef<string | null>(null);
  const targetMapRef = useRef<Map<string, HTMLElement>>(new Map());
  const outlineScopeKeyRef = useRef(scopeKey);
  const outlineRevisionRef = useRef(outlineRevision);

  // The DOM target cache is scoped to the full outline identity (sourceKind +
  // sourceIdentityKey): it is dropped whenever that identity or the rendered
  // tree changes, so a semantic↔markdown switch can never reuse stale targets.
  useEffect(() => {
    targetMapRef.current = new Map();
  }, [items, outlineRevision, scopeKey]);

  // Full outline-identity reset: a change in sourceKind OR base_id:generation
  // (e.g. semantic → Markdown that happen to share a base/generation) clears all
  // rail state — active/focus/scroll-lock/target-cache — and closes the stale
  // panel. Same-source revision updates are handled separately below and keep
  // the panel open.
  useEffect(() => {
    if (outlineScopeKeyRef.current === scopeKey) {
      return;
    }
    outlineScopeKeyRef.current = scopeKey;
    setActiveKey(null);
    setFocusedKey(null);
    setPanelOpen(false);
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
  }, [scopeKey]);

  // Same-source revision refresh — ref-side cleanup only (active/focus value
  // pruning happens during render on the revision fence above).
  useEffect(() => {
    if (outlineRevisionRef.current === outlineRevision) {
      return;
    }
    outlineRevisionRef.current = outlineRevision;

    if (!available) {
      lockedKeyRef.current = null;
      if (scrollLockTimerRef.current !== null) {
        window.clearTimeout(scrollLockTimerRef.current);
        scrollLockTimerRef.current = null;
      }
      targetMapRef.current = new Map();
      return;
    }

    // Release a scroll lock stuck on a key that is no longer a section so the
    // scroll-spy can resume (it re-subscribes on `items` change regardless).
    const sectionKeys = new Set(
      items.filter((n) => n.role === "section").map((n) => n.key),
    );
    if (lockedKeyRef.current && !sectionKeys.has(lockedKeyRef.current)) {
      lockedKeyRef.current = null;
      if (scrollLockTimerRef.current !== null) {
        window.clearTimeout(scrollLockTimerRef.current);
        scrollLockTimerRef.current = null;
      }
    }

    targetMapRef.current = new Map();
  }, [outlineRevision, available, items, panelOpen]);

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

  // --- Scroll-based active item -------------------------------------------
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!available || items.length === 0) return;

    const fenceScopeKey = scopeKey;
    const scrollContainer = getScrollContainer() ?? window;
    let rafId: number | null = null;
    let pending = false;

    const updateActive = () => {
      if (pending) return;
      pending = true;
      rafId = window.requestAnimationFrame(() => {
        pending = false;
        if (outlineScopeKeyRef.current !== fenceScopeKey) {
          return;
        }
        if (lockedKeyRef.current) return;

        const safeTop = TOPBAR_SAFE_HEIGHT + ACTIVE_SAFE_OFFSET;
        const next = computeActiveOutlineItemId(
          items,
          ticks,
          orderedUnitIds,
          unitOrderById,
          targetMapRef.current,
          safeTop,
          scopeKey,
        );
        setActiveKey(next);
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
    available,
    items,
    ticks,
    orderedUnitIds,
    unitOrderById,
    scopeKey,
  ]);

  // Initialize focused key when the panel opens; clear on close. Focus only ever
  // lands on sections — groups are skipped entirely. Adjusted during render
  // (adjust-state-when-props-change) because it is a pure sync to
  // panelOpen/items/activeKey — no async cascade, no stale one-frame flash.
  if (panelOpen) {
    if (focusedKey === null) {
      const firstSection =
        items.find((item) => item.role === "section")?.key ?? null;
      const activeSection =
        activeKey !== null &&
        items.some(
          (item) => item.key === activeKey && item.role === "section",
        )
          ? activeKey
          : null;
      const nextFocusedKey = activeSection ?? firstSection;
      if (nextFocusedKey !== null) {
        setFocusedKey(nextFocusedKey);
      }
    }
  } else if (focusedKey !== null) {
    setFocusedKey(null);
  }

  // Focus the row matching focusedKey when it changes (keyboard nav).
  useEffect(() => {
    if (!panelOpen || focusedKey === null) return;
    const row = rowRefsRef.current.get(focusedKey);
    row?.focus();
  }, [focusedKey, panelOpen]);

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

  // Click → resolve target and safe-scroll. Fail closed: no target means no
  // scroll and no activation. No network request is made (the per-row
  // "解析此段" action was removed; a row click only navigates).
  const handleItemClick = useCallback(
    (key: string) => {
      const item = items.find((n) => n.key === key);
      if (!item) return;
      const target = resolveValidatedOutlineTarget(
        item,
        targetMapRef.current,
        getPlateDocumentRoot(),
        scopeKey,
      );
      if (!target) {
        return;
      }
      scrollElementIntoSafeView(target);
      setActiveKey(key);
      setFocusedKey(key);
      lockActiveKey(key);
    },
    [lockActiveKey, items, scopeKey],
  );

  const handleItemKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLButtonElement>, key: string) => {
      // Roving tabindex moves only across sections; groups are skipped.
      const navigable = items.filter((item) => item.role === "section");
      const currentIndex = navigable.findIndex((item) => item.key === key);
      if (currentIndex === -1) return;

      switch (event.key) {
        case "Enter":
        case " ": {
          event.preventDefault();
          handleItemClick(key);
          break;
        }
        case "ArrowDown": {
          event.preventDefault();
          const nextIndex = Math.min(navigable.length - 1, currentIndex + 1);
          setFocusedKey(navigable[nextIndex]!.key);
          break;
        }
        case "ArrowUp": {
          event.preventDefault();
          const prevIndex = Math.max(0, currentIndex - 1);
          setFocusedKey(navigable[prevIndex]!.key);
          break;
        }
        case "Home": {
          event.preventDefault();
          setFocusedKey(navigable[0]!.key);
          break;
        }
        case "End": {
          event.preventDefault();
          setFocusedKey(navigable[navigable.length - 1]!.key);
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
    [items, handleItemClick, closePanel],
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

  // Hide rule: no usable outline → no rail, no ticks, no panel, no fallback.
  // Placed after all hooks to respect the rules of hooks.
  if (!available) {
    return null;
  }

  const isCanvas = layout === "canvas";

  const tickKeys = ticks.map((t) => t.key);

  // "当前第 N 项" counts navigable sections only — groups are not numbered.
  const sectionItems = items.filter((item) => item.role === "section");
  const activeItemIndex =
    activeKey === null
      ? -1
      : sectionItems.findIndex((item) => item.key === activeKey);
  const activeIndexForLabel =
    activeItemIndex >= 0 ? activeItemIndex + 1 : null;

  const triggerLabel = buildOutlineTriggerLabel(
    panelOpen,
    activeIndexForLabel,
  );

  // Highlight the root tick that is the active item or an ancestor of it.
  const activeTickKey = activeKey
    ? (ticks.find(
        (t) =>
          t.key === activeKey ||
          items.some(
            (n) =>
              n.key === activeKey &&
              (n.key === t.key || isDescendantOf(items, n.key, t.key)),
          ),
      )?.key ?? null)
    : null;

  return (
    <nav
      ref={wrapperRef}
      aria-label="内容大纲"
      data-testid="reader-record-navigation-rail"
      data-outline-source={sourceKind}
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
        isPartial={isPartial}
        items={items}
        activeKey={activeKey}
        focusedKey={focusedKey}
        panelOpen={panelOpen}
        getRowRef={getRowRef}
        onItemClick={handleItemClick}
        onItemKeyDown={handleItemKeyDown}
        onMouseEnter={keepOpenPanel}
        onMouseLeave={handleMouseLeave}
        registerRowRef={registerRowRef}
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
          tickKeys={tickKeys}
          activeKey={activeTickKey}
          onTickMouseEnter={handleTickMouseEnter}
        />
      </button>
    </nav>
  );
}

interface OutlineRowProps {
  item: OutlineItem;
  active: boolean;
  tabIndex?: number;
  onClick: () => void;
  onKeyDown: (event: React.KeyboardEvent<HTMLButtonElement>) => void;
  registerRef: (el: HTMLButtonElement | null) => void;
}

function OutlineRow({
  item,
  active,
  tabIndex = -1,
  onClick,
  onKeyDown,
  registerRef,
}: OutlineRowProps) {
  const depth = Math.min(Math.max(item.depth, 1), 3);
  const levelLabel =
    depth === 1 ? "一级" : depth === 2 ? "二级" : "三级";
  const indent = { paddingLeft: `${8 + (depth - 1) * 12}px` };

  // A `group` is a meaningful parent topic with no independent landing point:
  // rendered for hierarchy only — not a button, not focusable, excluded from the
  // roving tab order, no click/scroll handler, no pointer affordance. It carries
  // heading structure semantics (role="heading" + aria-level) at normal title
  // contrast so it never reads as a disabled item.
  if (item.role !== "section") {
    return (
      <li className="relative">
        <div
          role="heading"
          aria-level={depth}
          data-testid={`reader-record-outline-node-${item.key}`}
          data-outline-node-id={item.key}
          data-outline-depth={item.depth}
          data-outline-role="group"
          style={indent}
          className="w-full cursor-default py-1.5 pr-2.5 text-left font-medium text-ink/70"
        >
          <span className="block truncate text-[11px] leading-snug">
            {item.title}
          </span>
        </div>
      </li>
    );
  }

  return (
    <li className="relative">
      <button
        ref={registerRef}
        type="button"
        data-testid={`reader-record-outline-node-${item.key}`}
        data-outline-node-id={item.key}
        data-outline-depth={item.depth}
        data-outline-role="section"
        aria-label={`${levelLabel}，${item.title}`}
        aria-current={active ? "true" : undefined}
        tabIndex={tabIndex}
        onClick={onClick}
        onKeyDown={onKeyDown}
        style={indent}
        className={cn(
          "relative w-full py-1.5 pr-2.5 text-left transition-colors duration-150 ease-[var(--cl-ease-standard)]",
          "focus-visible:outline-none focus-visible:bg-ink/[0.035] focus-visible:ring-1 focus-visible:ring-lens-blue/30",
          active
            ? "font-medium text-lens-blue"
            : "text-ink/60 hover:bg-ink/[0.035] hover:text-ink",
        )}
      >
        <span className="block truncate text-[11px] leading-snug">
          {item.title}
        </span>
      </button>
    </li>
  );
}
