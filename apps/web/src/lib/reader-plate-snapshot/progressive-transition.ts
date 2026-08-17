/**
 * Progressive Transition UX — deterministic fixture / event
 * replay projection.
 *
 * Pure module (no React, no network, no LLM). Projects Reader snapshot +
 * event poll steps into progressive phases and applies live-update rules:
 *
 *   loading → first layer → partial/layer-ready → coverage_complete
 *
 * Interaction state (scroll, selection path, active anchor, expanded
 * panel / Quick Peek) is preserved across accepted snapshot reloads.
 * Stale snapshots (lower last_event_sequence) and cursor regression are
 * rejected so published layers cannot be rolled back by out-of-order
 * or duplicate events.
 *
 * Complements {@link decidePollingAction} polling cursor semantics;
 * does not replace the polling hook or backend orchestration contracts.
 */

import {
  decidePollingAction,
  type PollingDecision,
} from "@/lib/reader-plate-snapshot/polling";
import {
  READER_TEXT_RANGE_HASH_ALGORITHM,
  READER_TEXT_RANGE_OFFSET_UNIT,
  type ReaderEventPollResponseDto,
  type ReaderEventResponseDto,
  type ReaderEventType,
  type ReaderLayerType,
  type ReaderPlateSnapshotDto,
  type ReaderSnapshotLayerDto,
  type ReaderTextRangeAnchorDto,
} from "@/types/api/reader-plate";
import type { ReadingRecordReadinessState } from "@/types/api/reading-records";

// ---------------------------------------------------------------------------
// Progressive phases
// ---------------------------------------------------------------------------

/**
 * Client-visible progressive phase. Not a backend enum — derived from
 * readiness_state + published layer presence for UX assertions.
 */
export type ProgressivePhase =
  | "loading"
  | "article_ready_no_layers"
  | "first_layer"
  | "partial_ready"
  | "coverage_complete";

export const PROGRESSIVE_PHASE_ORDER: readonly ProgressivePhase[] = [
  "loading",
  "article_ready_no_layers",
  "first_layer",
  "partial_ready",
  "coverage_complete",
] as const;

// ---------------------------------------------------------------------------
// Interaction state preserved across live snapshot reloads
// ---------------------------------------------------------------------------

export type ExpandedPanelKind =
  | "quick_peek"
  | "grammar"
  | "dictionary"
  | "note"
  | "none";

/**
 * Client interaction state that MUST survive snapshot reloads.
 * These fields are domain/UI identity, not Plate path truth.
 */
export interface ProgressiveInteractionState {
  /** Scroll offset of the reader article scroller (px). */
  scrollTop: number;
  /**
   * Plate selection path pair, or null when no selection.
   * Restored only when both paths still exist in the new tree.
   */
  selection: {
    anchorPath: number[];
    focusPath: number[];
  } | null;
  /** Active domain anchor (anchor_segment_id), if any. */
  activeAnchorId: string | null;
  /** Expanded floating / side panel (Quick Peek, grammar, etc.). */
  expandedPanel: ExpandedPanelKind;
  /** Active grammar item id when grammar panel/callout is open. */
  activeGrammarItemId: string | null;
}

export const EMPTY_INTERACTION_STATE: ProgressiveInteractionState = {
  scrollTop: 0,
  selection: null,
  activeAnchorId: null,
  expandedPanel: "none",
  activeGrammarItemId: null,
};

// ---------------------------------------------------------------------------
// Progressive client state
// ---------------------------------------------------------------------------

export interface ProgressiveClientState {
  phase: ProgressivePhase;
  /** null while still in pure loading (no snapshot applied yet). */
  snapshot: ReaderPlateSnapshotDto | null;
  /** Monotonic poll cursor (= last applied snapshot.last_event_sequence). */
  cursor: number;
  readiness: ReadingRecordReadinessState | null;
  /** Published layer_type values currently visible (from snapshot). */
  visibleLayerTypes: readonly ReaderLayerType[];
  /** Stable keys `layer_type:layer_id` for published layers. */
  publishedLayerKeys: readonly string[];
  interaction: ProgressiveInteractionState;
  lastReloadReason: string | null;
  /** True when a poll/snapshot was rejected as stale (no state mutation). */
  lastRejected: boolean;
  rejectReason: string | null;
}

export function createInitialProgressiveState(
  interaction: ProgressiveInteractionState = EMPTY_INTERACTION_STATE,
): ProgressiveClientState {
  return {
    phase: "loading",
    snapshot: null,
    cursor: 0,
    readiness: null,
    visibleLayerTypes: [],
    publishedLayerKeys: [],
    interaction: { ...interaction },
    lastReloadReason: null,
    lastRejected: false,
    rejectReason: null,
  };
}

// ---------------------------------------------------------------------------
// Layer / phase projection
// ---------------------------------------------------------------------------

export function layerKey(layer: ReaderSnapshotLayerDto): string {
  return `${layer.layer_type}:${layer.layer_id}`;
}

/**
 * T5.4b: semantic_outline inventory layers are optional document enhancement.
 * They must not participate in progressive monotone key tracking or phase
 * typing — outline 有↔无 alone must not trigger layer_regression.
 */
function isProgressiveTrackedEnhancementLayer(
  layer: ReaderSnapshotLayerDto,
): boolean {
  return layer.layer_type !== "semantic_outline";
}

export function listPublishedLayerKeys(
  snapshot: ReaderPlateSnapshotDto,
): string[] {
  const layers = snapshot.enhancement_layers ?? [];
  return layers
    .filter(
      (layer) =>
        layer.status === "published" && isProgressiveTrackedEnhancementLayer(layer),
    )
    .map(layerKey)
    .sort();
}

export function listVisibleLayerTypes(
  snapshot: ReaderPlateSnapshotDto,
): ReaderLayerType[] {
  const types = new Set<ReaderLayerType>();
  for (const layer of snapshot.enhancement_layers ?? []) {
    if (
      layer.status === "published" &&
      isProgressiveTrackedEnhancementLayer(layer)
    ) {
      types.add(layer.layer_type);
    }
  }
  // Also surface translation groups present only in the Plate value tree
  // (some fixtures publish translation via value without enhancement_layers).
  for (const unit of snapshot.value ?? []) {
    for (const child of unit.children ?? []) {
      if (
        child.type === "reader_translation" ||
        child.type === "reader_translation_group"
      ) {
        types.add("translation");
      }
    }
  }
  return [...types].sort();
}

/**
 * Derive progressive phase from a loaded snapshot.
 *
 * - readiness coverage_complete → coverage_complete
 * - no published layers → article_ready_no_layers (or loading if not ready)
 * - first published layer type only → first_layer
 * - 2+ layer types or initial_enhancement_ready → partial_ready
 */
export function classifyProgressivePhase(
  snapshot: ReaderPlateSnapshotDto | null,
): ProgressivePhase {
  if (!snapshot) {
    return "loading";
  }

  const readiness = snapshot.record.readiness_state;
  if (readiness === "coverage_complete") {
    return "coverage_complete";
  }

  const visible = listVisibleLayerTypes(snapshot);
  if (visible.length === 0) {
    if (
      readiness === "article_ready" ||
      readiness === "initial_enhancement_ready"
    ) {
      return "article_ready_no_layers";
    }
    // submitted / candidate_base_ready still count as loading for PUX.
    return "loading";
  }

  if (visible.length === 1) {
    return "first_layer";
  }

  // 2+ layer types present → partial progressive ready (not final coverage).
  return "partial_ready";
}

export function phaseIndex(phase: ProgressivePhase): number {
  return PROGRESSIVE_PHASE_ORDER.indexOf(phase);
}

/** Human-readable Chinese labels for published layer types (status strip). */
export const LAYER_TYPE_LABEL_ZH: Readonly<Record<ReaderLayerType, string>> = {
  translation: "译文",
  vocabulary: "词汇",
  grammar_note: "语法",
  sentence_analysis: "句法",
  // Not used in progressive strip (outline excluded from tracked types).
  semantic_outline: "大纲",
};

/**
 * Minimal, non-blocking progressive status copy for the Reader Record page.
 * Empty string when no strip should be shown (pure loading without snapshot).
 */
export function formatProgressiveStatusLine(
  phase: ProgressivePhase,
  visibleLayerTypes: readonly ReaderLayerType[] = [],
): string {
  const arrived = visibleLayerTypes
    .map((t) => LAYER_TYPE_LABEL_ZH[t] ?? t)
    .join("、");

  switch (phase) {
    case "loading":
      return "正在加载阅读内容…";
    case "article_ready_no_layers":
      return "正文已可读，批注生成中";
    case "first_layer":
      return arrived
        ? `已到达：${arrived} · 更多批注生成中`
        : "首层增强已到达 · 更多批注生成中";
    case "partial_ready":
      return arrived
        ? `已到达：${arrived} · 解析继续完善中`
        : "部分增强已就绪 · 解析继续完善中";
    case "coverage_complete":
      return arrived
        ? `完整解析完成 · ${arrived}`
        : "完整解析完成";
    default:
      return "";
  }
}

// ---------------------------------------------------------------------------
// Snapshot apply (monotonic + interaction preserve)
// ---------------------------------------------------------------------------

export type SnapshotApplyResult =
  | { ok: true; state: ProgressiveClientState }
  | { ok: false; reason: string; state: ProgressiveClientState };

/**
 * Apply a fresh snapshot reload while preserving interaction state.
 *
 * Rejects stale snapshots whose `last_event_sequence` is lower than the
 * current cursor (out-of-order / old snapshot must not roll back layers
 * or readiness). Equal sequence is allowed only when no snapshot is loaded
 * yet, or when the snapshot_id matches (idempotent re-apply).
 */
export function applySnapshotReload(
  state: ProgressiveClientState,
  snapshot: ReaderPlateSnapshotDto,
  options?: {
    reloadReason?: string | null;
    /** Override interaction after apply (tests). Default: keep previous. */
    interaction?: ProgressiveInteractionState;
  },
): SnapshotApplyResult {
  if (
    state.snapshot !== null &&
    snapshot.last_event_sequence < state.cursor
  ) {
    return {
      ok: false,
      reason: "stale_snapshot_sequence",
      state: {
        ...state,
        lastRejected: true,
        rejectReason: "stale_snapshot_sequence",
      },
    };
  }

  // Generation regression (base rebuild) is a hard reset — accept but
  // clear interaction that is generation-scoped.
  const prevGen = state.snapshot?.record.generation;
  const nextGen = snapshot.record.generation;
  const generationChanged =
    prevGen !== undefined && prevGen !== null && prevGen !== nextGen;

  const interaction = options?.interaction
    ? { ...options.interaction }
    : generationChanged
      ? { ...EMPTY_INTERACTION_STATE, scrollTop: state.interaction.scrollTop }
      : { ...state.interaction };

  // Monotone published layers within the same generation: a legitimate
  // progressive snapshot may only grow published layer keys. If the server
  // returns a same-generation snapshot that DROPS previously published
  // keys while advancing sequence, treat as contract violation and reject
  // so the UI does not flash layers away.
  if (state.snapshot !== null && !generationChanged) {
    const prevKeys = new Set(state.publishedLayerKeys);
    const nextKeys = new Set(listPublishedLayerKeys(snapshot));
    for (const key of prevKeys) {
      if (!nextKeys.has(key) && snapshot.last_event_sequence >= state.cursor) {
        // Allow equal-sequence idempotent re-apply of same snapshot_id.
        if (
          snapshot.snapshot_id === state.snapshot.snapshot_id &&
          snapshot.last_event_sequence === state.cursor
        ) {
          break;
        }
        // Layer drop on sequence advance within same generation is rejected.
        if (snapshot.last_event_sequence > state.cursor) {
          return {
            ok: false,
            reason: "layer_regression",
            state: {
              ...state,
              lastRejected: true,
              rejectReason: `layer_regression:${key}`,
            },
          };
        }
      }
    }
  }

  const next: ProgressiveClientState = {
    phase: classifyProgressivePhase(snapshot),
    snapshot,
    cursor: snapshot.last_event_sequence,
    readiness: snapshot.record.readiness_state,
    visibleLayerTypes: listVisibleLayerTypes(snapshot),
    publishedLayerKeys: listPublishedLayerKeys(snapshot),
    interaction,
    lastReloadReason: options?.reloadReason ?? state.lastReloadReason,
    lastRejected: false,
    rejectReason: null,
  };

  return { ok: true, state: next };
}

// ---------------------------------------------------------------------------
// Event poll apply (cursor + reload decision)
// ---------------------------------------------------------------------------

export type EventPollApplyResult = {
  decision: PollingDecision;
  /**
   * Cursor after the poll decision when no snapshot reload is required.
   * On `reload`, cursor stays put until {@link applySnapshotReload} succeeds
   * (T2.1 contract).
   */
  nextCursor: number;
  /** True when the caller must fetch + apply a snapshot. */
  requiresSnapshotReload: boolean;
  reloadReason: string | null;
};

/**
 * Apply a poll response against the progressive client cursor.
 *
 * Pure wrapper around {@link decidePollingAction} that also exposes the
 * T2.1 cursor-hold-on-reload contract for fixture replay. The snapshot fence
 * (generation/base_id of the currently accepted snapshot) is extracted from
 * the state so the payload-aware classifier can detect stale-base
 * representation events (T4.2a-O4-R2-D).
 */
export function applyEventPoll(
  state: ProgressiveClientState,
  response: ReaderEventPollResponseDto,
): EventPollApplyResult {
  const snapshotFence =
    state.snapshot !== null
      ? {
          generation: state.snapshot.record.generation,
          baseId: state.snapshot.base.base_id,
        }
      : null;

  const decision = decidePollingAction({
    afterSequence: state.cursor,
    response,
    snapshotFence,
  });

  if (decision.kind === "reload") {
    return {
      decision,
      nextCursor: state.cursor, // hold until snapshot applied
      requiresSnapshotReload: true,
      reloadReason: decision.reason,
    };
  }

  return {
    decision,
    nextCursor: decision.cursor,
    requiresSnapshotReload: false,
    reloadReason: null,
  };
}

/**
 * After a successful snapshot reload that was triggered by a poll, advance
 * the cursor to the poll's `next_after_sequence` (T2.1 success path).
 * Rejects advancement that would go backwards.
 */
export function advanceCursorAfterSuccessfulReload(
  state: ProgressiveClientState,
  nextAfterSequence: number,
): ProgressiveClientState {
  if (nextAfterSequence < state.cursor) {
    return {
      ...state,
      lastRejected: true,
      rejectReason: "cursor_regression",
    };
  }
  return {
    ...state,
    cursor: nextAfterSequence,
    lastRejected: false,
    rejectReason: null,
  };
}

// ---------------------------------------------------------------------------
// Interaction helpers (pure; mirror T2.1 Plate surface restore rules)
// ---------------------------------------------------------------------------

export type PlateDescendantLike =
  | { children?: PlateDescendantLike[] }
  | PlateDescendantLike[]
  | unknown;

/**
 * Return true if a Plate selection path still resolves in the children tree.
 *
 * Slate/Plate path semantics: path `[0, 1, 2]` means
 * `children[0].children[1].children[2]`. Each index steps into the
 * previous node's `children` array.
 */
export function pathExistsInPlateChildren(
  children: PlateDescendantLike[],
  path: number[],
): boolean {
  if (!Array.isArray(path) || path.length === 0) {
    return false;
  }
  // Synthetic root so the same descent rule applies at every level.
  let node: { children?: PlateDescendantLike[] } | PlateDescendantLike = {
    children,
  };
  for (const index of path) {
    if (
      !node ||
      typeof node !== "object" ||
      !("children" in node) ||
      !Array.isArray(node.children)
    ) {
      return false;
    }
    if (typeof index !== "number" || index < 0 || index >= node.children.length) {
      return false;
    }
    node = node.children[index] as PlateDescendantLike;
  }
  return node !== undefined && node !== null;
}

/**
 * Decide whether a saved selection can be restored after a value swap.
 * Scroll is always restorable; selection only when both paths exist.
 */
export function resolvePreservedSelection(
  children: PlateDescendantLike[],
  selection: ProgressiveInteractionState["selection"],
): ProgressiveInteractionState["selection"] {
  if (!selection) {
    return null;
  }
  const anchorOk = pathExistsInPlateChildren(children, selection.anchorPath);
  const focusOk = pathExistsInPlateChildren(children, selection.focusPath);
  return anchorOk && focusOk ? selection : null;
}

/**
 * Apply a live value swap: preserve scroll always; selection only when
 * paths still resolve. Expanded panel / active anchor are identity-based
 * and preserved unless generation changed (handled in applySnapshotReload).
 */
export function preserveInteractionAcrossValueSwap(input: {
  previous: ProgressiveInteractionState;
  nextChildren: PlateDescendantLike[];
}): ProgressiveInteractionState {
  return {
    ...input.previous,
    scrollTop: input.previous.scrollTop,
    selection: resolvePreservedSelection(
      input.nextChildren,
      input.previous.selection,
    ),
  };
}

// ---------------------------------------------------------------------------
// Fixture step types + replay
// ---------------------------------------------------------------------------

export type ProgressiveReplayStep =
  | {
      kind: "load_snapshot";
      snapshot: ReaderPlateSnapshotDto;
      /** Expected phase after apply. */
      expectPhase: ProgressivePhase;
      expectReadiness?: ReadingRecordReadinessState;
      expectVisibleLayers?: readonly ReaderLayerType[];
      interaction?: ProgressiveInteractionState;
    }
  | {
      kind: "poll";
      response: ReaderEventPollResponseDto;
      /** Expected decision kind. */
      expectDecision: PollingDecision["kind"];
      expectReloadReason?: string;
      /**
       * When decision is reload, provide the snapshot that would be fetched.
       * Replay applies it immediately (deterministic fixture).
       */
      snapshotOnReload?: ReaderPlateSnapshotDto;
      expectPhaseAfter?: ProgressivePhase;
      expectCursor?: number;
      expectRejected?: boolean;
    }
  | {
      kind: "set_interaction";
      interaction: Partial<ProgressiveInteractionState>;
    }
  | {
      kind: "assert";
      phase?: ProgressivePhase;
      readiness?: ReadingRecordReadinessState | null;
      cursor?: number;
      visibleLayers?: readonly ReaderLayerType[];
      scrollTop?: number;
      expandedPanel?: ExpandedPanelKind;
      activeAnchorId?: string | null;
      activeGrammarItemId?: string | null;
      publishedLayerKeys?: readonly string[];
      lastRejected?: boolean;
    };

export interface ProgressiveReplayResult {
  state: ProgressiveClientState;
  /** Phase at each successful snapshot apply, in order. */
  phaseTrace: ProgressivePhase[];
  /** Poll decisions observed, in order. */
  decisionTrace: PollingDecision["kind"][];
  /** Assertion failures collected (empty when all pass). */
  failures: string[];
}

/**
 * Deterministic event/snapshot replay for progressive UX fixtures.
 * Mutates no globals; returns final state + traces + soft assertion failures.
 */
export function replayProgressiveSteps(
  steps: readonly ProgressiveReplayStep[],
  initial: ProgressiveClientState = createInitialProgressiveState(),
): ProgressiveReplayResult {
  let state = initial;
  const phaseTrace: ProgressivePhase[] = [state.phase];
  const decisionTrace: PollingDecision["kind"][] = [];
  const failures: string[] = [];

  const fail = (message: string) => {
    failures.push(message);
  };

  for (let i = 0; i < steps.length; i += 1) {
    const step = steps[i];
    const tag = `step[${i}] ${step.kind}`;

    if (step.kind === "set_interaction") {
      state = {
        ...state,
        interaction: { ...state.interaction, ...step.interaction },
      };
      continue;
    }

    if (step.kind === "load_snapshot") {
      const result = applySnapshotReload(state, step.snapshot, {
        reloadReason: "initial_load",
        interaction: step.interaction,
      });
      if (!result.ok) {
        fail(`${tag}: load rejected: ${result.reason}`);
        state = result.state;
        continue;
      }
      state = result.state;
      phaseTrace.push(state.phase);
      if (state.phase !== step.expectPhase) {
        fail(
          `${tag}: phase expected ${step.expectPhase}, got ${state.phase}`,
        );
      }
      if (
        step.expectReadiness !== undefined &&
        state.readiness !== step.expectReadiness
      ) {
        fail(
          `${tag}: readiness expected ${step.expectReadiness}, got ${state.readiness}`,
        );
      }
      if (step.expectVisibleLayers) {
        const got = [...state.visibleLayerTypes].sort().join(",");
        const exp = [...step.expectVisibleLayers].sort().join(",");
        if (got !== exp) {
          fail(`${tag}: visible layers expected [${exp}], got [${got}]`);
        }
      }
      continue;
    }

    if (step.kind === "poll") {
      const pollResult = applyEventPoll(state, step.response);
      decisionTrace.push(pollResult.decision.kind);

      if (pollResult.decision.kind !== step.expectDecision) {
        fail(
          `${tag}: decision expected ${step.expectDecision}, got ${pollResult.decision.kind}`,
        );
      }

      if (
        step.expectReloadReason !== undefined &&
        pollResult.reloadReason !== step.expectReloadReason
      ) {
        fail(
          `${tag}: reloadReason expected ${step.expectReloadReason}, got ${pollResult.reloadReason}`,
        );
      }

      if (pollResult.requiresSnapshotReload) {
        if (!step.snapshotOnReload) {
          // Reload required but fixture did not supply snapshot — hold cursor.
          state = {
            ...state,
            lastReloadReason: pollResult.reloadReason,
          };
        } else {
          const applied = applySnapshotReload(state, step.snapshotOnReload, {
            reloadReason: pollResult.reloadReason,
          });
          if (!applied.ok) {
            state = applied.state;
            if (step.expectRejected === true) {
              // expected rejection
            } else {
              fail(`${tag}: snapshotOnReload rejected: ${applied.reason}`);
            }
          } else {
            // T2.1: advance cursor only after successful apply.
            state = advanceCursorAfterSuccessfulReload(
              applied.state,
              step.response.next_after_sequence,
            );
            phaseTrace.push(state.phase);
          }
        }
      } else {
        // advance / caught_up — move cursor without snapshot.
        if (pollResult.nextCursor < state.cursor) {
          fail(
            `${tag}: cursor would regress ${state.cursor} → ${pollResult.nextCursor}`,
          );
        } else {
          state = {
            ...state,
            cursor: pollResult.nextCursor,
            lastRejected: false,
            rejectReason: null,
          };
        }
      }

      if (step.expectPhaseAfter !== undefined && state.phase !== step.expectPhaseAfter) {
        fail(
          `${tag}: phaseAfter expected ${step.expectPhaseAfter}, got ${state.phase}`,
        );
      }
      if (step.expectCursor !== undefined && state.cursor !== step.expectCursor) {
        fail(
          `${tag}: cursor expected ${step.expectCursor}, got ${state.cursor}`,
        );
      }
      if (
        step.expectRejected !== undefined &&
        state.lastRejected !== step.expectRejected
      ) {
        fail(
          `${tag}: lastRejected expected ${step.expectRejected}, got ${state.lastRejected}`,
        );
      }
      continue;
    }

    if (step.kind === "assert") {
      if (step.phase !== undefined && state.phase !== step.phase) {
        fail(`${tag}: phase expected ${step.phase}, got ${state.phase}`);
      }
      if (step.readiness !== undefined && state.readiness !== step.readiness) {
        fail(
          `${tag}: readiness expected ${step.readiness}, got ${state.readiness}`,
        );
      }
      if (step.cursor !== undefined && state.cursor !== step.cursor) {
        fail(`${tag}: cursor expected ${step.cursor}, got ${state.cursor}`);
      }
      if (step.visibleLayers) {
        const got = [...state.visibleLayerTypes].sort().join(",");
        const exp = [...step.visibleLayers].sort().join(",");
        if (got !== exp) {
          fail(`${tag}: visible layers expected [${exp}], got [${got}]`);
        }
      }
      if (
        step.scrollTop !== undefined &&
        state.interaction.scrollTop !== step.scrollTop
      ) {
        fail(
          `${tag}: scrollTop expected ${step.scrollTop}, got ${state.interaction.scrollTop}`,
        );
      }
      if (
        step.expandedPanel !== undefined &&
        state.interaction.expandedPanel !== step.expandedPanel
      ) {
        fail(
          `${tag}: expandedPanel expected ${step.expandedPanel}, got ${state.interaction.expandedPanel}`,
        );
      }
      if (
        step.activeAnchorId !== undefined &&
        state.interaction.activeAnchorId !== step.activeAnchorId
      ) {
        fail(
          `${tag}: activeAnchorId expected ${step.activeAnchorId}, got ${state.interaction.activeAnchorId}`,
        );
      }
      if (
        step.activeGrammarItemId !== undefined &&
        state.interaction.activeGrammarItemId !== step.activeGrammarItemId
      ) {
        fail(
          `${tag}: activeGrammarItemId expected ${step.activeGrammarItemId}, got ${state.interaction.activeGrammarItemId}`,
        );
      }
      if (step.publishedLayerKeys) {
        const got = [...state.publishedLayerKeys].sort().join(",");
        const exp = [...step.publishedLayerKeys].sort().join(",");
        if (got !== exp) {
          fail(`${tag}: publishedLayerKeys expected [${exp}], got [${got}]`);
        }
      }
      if (
        step.lastRejected !== undefined &&
        state.lastRejected !== step.lastRejected
      ) {
        fail(
          `${tag}: lastRejected expected ${step.lastRejected}, got ${state.lastRejected}`,
        );
      }
    }
  }

  return { state, phaseTrace, decisionTrace, failures };
}

// ---------------------------------------------------------------------------
// Fixture builders
// ---------------------------------------------------------------------------

const SOURCE_TEXT = "Institutional memory shapes policy choices.";
const TRANSLATION_TEXT = "制度记忆会塑造政策选择。";

function baseRecord(
  overrides: Partial<ReaderPlateSnapshotDto["record"]> = {},
): ReaderPlateSnapshotDto["record"] {
  return {
    title: "PUX Progressive Fixture",
    display_title_zh: null,
    title_generation_status: "pending",
    title_generation_error_code: null,
    title_generation_error_message: null,
    reading_goal: "daily_reading",
    reading_variant: "intensive_reading",
    created_at: "2026-07-13T00:00:00Z",
    source_type: "text",
    source_metadata: {},
    generation: 1,
    product_state: "readable_enhancing",
    readiness_state: "article_ready",
    ...overrides,
  };
}

function baseValue(options?: {
  withTranslation?: boolean;
}): ReaderPlateSnapshotDto["value"] {
  const children: ReaderPlateSnapshotDto["value"][number]["children"] = [
    {
      type: "reader_source_block",
      owner: "stable",
      base_id: "base_pux_1",
      unit_id: "unit_1",
      base_start_utf16: 0,
      base_end_utf16: SOURCE_TEXT.length,
      children: [
        {
          type: "reader_anchor_segment",
          owner: "stable",
          base_id: "base_pux_1",
          unit_id: "unit_1",
          anchor_segment_id: "seg_1",
          sentence_id: "sent_1",
          segment_type: "sentence",
          boundary_quality: "normal",
          base_start_utf16: 0,
          base_end_utf16: SOURCE_TEXT.length,
          unit_start_utf16: 0,
          unit_end_utf16: SOURCE_TEXT.length,
          text_hash: "hash_seg_1",
          hash_algorithm: "fnv1a32-utf16",
          children: [
            {
              text: SOURCE_TEXT,
              owner: "stable",
              lock_source: true,
              source_role: "segment_text",
              base_start_utf16: 0,
              base_end_utf16: SOURCE_TEXT.length,
              anchor_segment_id: "seg_1",
              segment_start_utf16: 0,
              segment_end_utf16: SOURCE_TEXT.length,
            },
          ],
        },
      ],
    },
  ];

  if (options?.withTranslation) {
    children.push({
      type: "reader_translation_group",
      owner: "system_ai",
      layer_id: "layer_translation_1",
      layer_version: 1,
      base_id: "base_pux_1",
      unit_id: "unit_1",
      target_scope: "unit",
      target_key: "unit_1",
      group_id: "unit_1_g1_1",
      covered_anchor_segment_ids: ["seg_1"],
      source_text_hash: "hash_unit_1",
      children: [{ text: TRANSLATION_TEXT }],
    });
  }

  return [
    {
      type: "reader_unit",
      owner: "stable",
      base_id: "base_pux_1",
      unit_id: "unit_1",
      order_index: 1,
      unit_type: "body",
      boundary_quality: "normal",
      base_start_utf16: 0,
      base_end_utf16: SOURCE_TEXT.length,
      text_hash: "hash_unit_1",
      hash_algorithm: "fnv1a32-utf16",
      children,
    },
  ];
}

function textRangeAnchor(): ReaderTextRangeAnchorDto {
  return {
    anchor_type: "text_range",
    base_id: "base_pux_1",
    unit_id: "unit_1",
    anchor_segment_id: "seg_1",
    sentence_id: "sent_1",
    segment_type: "sentence",
    offset_unit: READER_TEXT_RANGE_OFFSET_UNIT,
    start_offset: 0,
    end_offset: SOURCE_TEXT.length,
    selected_text: SOURCE_TEXT,
    text_hash: "hash_seg_1",
    hash_algorithm: READER_TEXT_RANGE_HASH_ALGORITHM,
  };
}

function publishedLayer(
  layerType: ReaderLayerType,
  layerId: string,
): ReaderSnapshotLayerDto {
  const base = {
    layer_id: layerId,
    owner: "system_ai" as const,
    base_id: "base_pux_1",
    target_scope: "unit" as const,
    target_key: "unit_1",
    status: "published" as const,
    schema_version: 1,
    published_at: "2026-07-13T00:00:00Z",
  };

  if (layerType === "translation") {
    return {
      ...base,
      layer_type: "translation",
      output: {
        groups: [
          {
            group_id: "unit_1_g1_1",
            anchor_segment_ids: ["seg_1"],
            source_text_hash: "hash_unit_1",
            translated_text: TRANSLATION_TEXT,
          },
        ],
      },
    };
  }
  if (layerType === "vocabulary") {
    return {
      ...base,
      layer_type: "vocabulary",
      output: {
        schema_version: 1,
        items: [
          {
            item_type: "vocab_highlight",
            anchor: {
              ...textRangeAnchor(),
              start_offset: 14,
              end_offset: 20,
              selected_text: "memory",
              text_hash: "hash_memory",
            },
            headword: "memory",
            brief_explanation: "记忆",
            reason: "key concept",
          },
        ],
      },
    };
  }
  if (layerType === "grammar_note") {
    return {
      ...base,
      layer_type: "grammar_note",
      output: {
        schema_version: 1,
        items: [
          {
            item_type: "grammar_note",
            spans: [textRangeAnchor()],
            grammar_point: "名词短语主语",
            pattern: "adjective + noun",
            note: "Institutional memory 是主语。",
          },
        ],
      },
    };
  }
  if (layerType === "semantic_outline") {
    return {
      layer_id: layerId,
      owner: "system_ai" as const,
      base_id: "base_pux_1",
      target_scope: "record" as const,
      target_key: "document",
      status: "published" as const,
      schema_version: 1,
      published_at: "2026-07-13T00:00:00Z",
      layer_type: "semantic_outline" as const,
      output: {
        schema_kind: "reader_semantic_outline",
        schema_version: 1,
        status: "ready",
        source_identity: { base_id: "base_pux_1", generation: 1 },
        publication: {
          outline_revision: "olrev_fixture",
          layer_id: layerId,
          published_at: "2026-07-13T00:00:00Z",
        },
        provenance: { kind: "llm", builder: "fixture", model: "test" },
        nodes: [],
        diagnostics: { drops: [], skipped_node_count: 0 },
      },
    };
  }
  return {
    ...base,
    layer_type: "sentence_analysis",
    output: {
      schema_version: 1,
      items: [
        {
          item_type: "sentence_analysis",
          anchor: textRangeAnchor(),
          label: "主谓结构",
          analysis: "主语 + 谓语",
          chunks: [
            { order: 1, label: "主语", text: "Institutional memory" },
            { order: 2, label: "谓语", text: "shapes policy choices" },
          ],
        },
      ],
    },
  };
}

export function makePuxSnapshot(options: {
  snapshotId: string;
  lastEventSequence: number;
  readiness: ReadingRecordReadinessState;
  productState?: ReaderPlateSnapshotDto["record"]["product_state"];
  layers?: ReaderLayerType[];
  withTranslationInValue?: boolean;
  generation?: number;
}): ReaderPlateSnapshotDto {
  const layerTypes = options.layers ?? [];
  const enhancement_layers = layerTypes.map((lt, i) =>
    publishedLayer(lt, `layer_${lt}_${i + 1}`),
  );

  return {
    schema_kind: "reader_plate_snapshot",
    snapshot_id: options.snapshotId,
    snapshot_taken_at: "2026-07-13T00:00:00Z",
    last_event_sequence: options.lastEventSequence,
    record_id: "rec_pux_1",
    record: baseRecord({
      readiness_state: options.readiness,
      product_state: options.productState ?? "readable_enhancing",
      generation: options.generation ?? 1,
    }),
    base: {
      base_id: "base_pux_1",
      content_sha256: "sha256_pux_1",
      canonicalizer_version: "canonicalizer_test",
      builder_version: "builder_test",
      segmenter_version: "segmenter_test",
      hash_algorithm: "fnv1a32-utf16",
      text_length_utf16: SOURCE_TEXT.length,
    },
    navigation: {
      units: [
        {
          unit_id: "unit_1",
          order_index: 1,
          unit_type: "body",
          boundary_quality: "normal",
          base_start_utf16: 0,
          base_end_utf16: SOURCE_TEXT.length,
          text_hash: "hash_unit_1",
          hash_algorithm: "fnv1a32-utf16",
        },
      ],
    },
    anchor_segments: [
      {
        anchor_segment_id: "seg_1",
        sentence_id: "sent_1",
        paragraph_id: "unit_1",
        unit_id: "unit_1",
        order_index: 1,
        unit_order_index: 1,
        segment_type: "sentence",
        boundary_quality: "normal",
        base_start_utf16: 0,
        base_end_utf16: SOURCE_TEXT.length,
        unit_start_utf16: 0,
        unit_end_utf16: SOURCE_TEXT.length,
        text_hash: "hash_seg_1",
        hash_algorithm: "fnv1a32-utf16",
      },
    ],
    enhancement_layers,
    analysis_progress: {
      mode: "automatic",
      plan_version: "reader_analysis_sections_v1",
      overall_status: "queued",
      active_phase: null,
      translation_status: "not_started",
      completed_section_count: 0,
      total_section_count: 0,
      active_section_id: null,
      needs_user_action: false,
      last_progress_at: null,
      sections: [],
    },
    enhancement_progress: {
      overall_status:
        options.readiness === "coverage_complete"
          ? "ready"
          : layerTypes.length === 0
            ? "readable_enhancing"
            : "readable_enhancing",
      layers: layerTypes.map((lt, i) => ({
        capability:
          lt === "grammar_note" || lt === "sentence_analysis"
            ? "grammar"
            : lt === "vocabulary"
              ? "vocabulary"
              : "translation",
        layer_type: lt,
        status: "succeeded",
        job_status: "succeeded",
        job_type:
          lt === "translation"
            ? "translate_article"
            : lt === "vocabulary"
              ? "build_vocabulary_layer_article"
              : "build_grammar_bundle",
        layer_id: `layer_${lt}_${i + 1}`,
        job_id: `job_${lt}_${i + 1}`,
        target_type: "unit",
        target_scope: "unit",
        target_key: "unit_1",
      })),
    },
    ask_supplements: [],
    user_assets: [],
    parsed_decisions: [],
    value: baseValue({
      withTranslation:
        options.withTranslationInValue === true ||
        layerTypes.includes("translation"),
    }),
  };
}

export function makePuxEvent(
  sequence: number,
  eventType: ReaderEventType,
  payload: Record<string, unknown> = {},
): ReaderEventResponseDto {
  return {
    id: `evt_pux_${sequence}`,
    reading_record_id: "rec_pux_1",
    sequence,
    event_type: eventType,
    payload,
    created_at: "2026-07-13T00:00:00Z",
  };
}

export function makePuxPollResponse(options: {
  afterSequence: number;
  nextAfterSequence: number;
  lastEventSequence: number;
  events?: ReaderEventResponseDto[];
  reloadRequired?: boolean;
  reloadReason?: string | null;
  hasMore?: boolean;
}): ReaderEventPollResponseDto {
  return {
    reading_record_id: "rec_pux_1",
    after_sequence: options.afterSequence,
    next_after_sequence: options.nextAfterSequence,
    last_event_sequence: options.lastEventSequence,
    has_more: options.hasMore ?? false,
    truncated: false,
    reload_required: options.reloadRequired ?? false,
    reload_reason: options.reloadReason ?? null,
    events: options.events ?? [],
  };
}

/**
 * Canonical five-step progressive fixture used by PUX-R1 acceptance tests.
 *
 * Steps:
 *  1. article_ready, no layers (cursor=1)
 *  2. translation first layer via layer_published (cursor→2)
 *  3. vocabulary arrives (partial_ready, cursor→3)
 *  4. grammar arrives (still partial_ready, cursor→4)
 *  5. coverage_complete via record_state_changed + reload (cursor→5)
 *
 * Interaction (scroll / selection / quick peek / active anchor) is set
 * after step 2 and must survive steps 3–5.
 */
export function buildCanonicalProgressiveReplaySteps(
  interactionAfterFirstLayer: ProgressiveInteractionState = {
    scrollTop: 420,
    selection: { anchorPath: [0, 0, 0], focusPath: [0, 0, 0] },
    activeAnchorId: "seg_1",
    expandedPanel: "quick_peek",
    activeGrammarItemId: null,
  },
): ProgressiveReplayStep[] {
  const s1 = makePuxSnapshot({
    snapshotId: "snap_pux_loading",
    lastEventSequence: 1,
    readiness: "article_ready",
    layers: [],
  });
  const s2 = makePuxSnapshot({
    snapshotId: "snap_pux_translation",
    lastEventSequence: 2,
    readiness: "article_ready",
    layers: ["translation"],
    withTranslationInValue: true,
  });
  const s3 = makePuxSnapshot({
    snapshotId: "snap_pux_vocab",
    lastEventSequence: 3,
    readiness: "initial_enhancement_ready",
    layers: ["translation", "vocabulary"],
    withTranslationInValue: true,
  });
  const s4 = makePuxSnapshot({
    snapshotId: "snap_pux_grammar",
    lastEventSequence: 4,
    readiness: "initial_enhancement_ready",
    layers: ["translation", "vocabulary", "grammar_note"],
    withTranslationInValue: true,
  });
  const s5 = makePuxSnapshot({
    snapshotId: "snap_pux_complete",
    lastEventSequence: 5,
    readiness: "coverage_complete",
    productState: "readable_enhancing",
    layers: ["translation", "vocabulary", "grammar_note", "sentence_analysis"],
    withTranslationInValue: true,
  });

  return [
    {
      kind: "load_snapshot",
      snapshot: s1,
      expectPhase: "article_ready_no_layers",
      expectReadiness: "article_ready",
      expectVisibleLayers: [],
    },
    {
      kind: "poll",
      response: makePuxPollResponse({
        afterSequence: 1,
        nextAfterSequence: 2,
        lastEventSequence: 2,
        events: [
          makePuxEvent(2, "layer_published", { layer_type: "translation" }),
        ],
      }),
      expectDecision: "reload",
      expectReloadReason: "layer_published",
      snapshotOnReload: s2,
      expectPhaseAfter: "first_layer",
      expectCursor: 2,
    },
    {
      kind: "set_interaction",
      interaction: interactionAfterFirstLayer,
    },
    {
      kind: "assert",
      phase: "first_layer",
      scrollTop: interactionAfterFirstLayer.scrollTop,
      expandedPanel: interactionAfterFirstLayer.expandedPanel,
      activeAnchorId: interactionAfterFirstLayer.activeAnchorId,
    },
    {
      kind: "poll",
      response: makePuxPollResponse({
        afterSequence: 2,
        nextAfterSequence: 3,
        lastEventSequence: 3,
        events: [
          makePuxEvent(3, "layer_published", { layer_type: "vocabulary" }),
        ],
      }),
      expectDecision: "reload",
      expectReloadReason: "layer_published",
      snapshotOnReload: s3,
      expectPhaseAfter: "partial_ready",
      expectCursor: 3,
    },
    {
      kind: "assert",
      phase: "partial_ready",
      scrollTop: interactionAfterFirstLayer.scrollTop,
      expandedPanel: interactionAfterFirstLayer.expandedPanel,
      activeAnchorId: interactionAfterFirstLayer.activeAnchorId,
      visibleLayers: ["translation", "vocabulary"],
    },
    {
      kind: "poll",
      response: makePuxPollResponse({
        afterSequence: 3,
        nextAfterSequence: 4,
        lastEventSequence: 4,
        events: [
          makePuxEvent(4, "layer_published", { layer_type: "grammar_note" }),
        ],
      }),
      expectDecision: "reload",
      snapshotOnReload: s4,
      expectPhaseAfter: "partial_ready",
      expectCursor: 4,
    },
    {
      kind: "assert",
      phase: "partial_ready",
      scrollTop: interactionAfterFirstLayer.scrollTop,
      expandedPanel: "quick_peek",
      visibleLayers: ["grammar_note", "translation", "vocabulary"],
    },
    {
      kind: "poll",
      response: makePuxPollResponse({
        afterSequence: 4,
        nextAfterSequence: 5,
        lastEventSequence: 5,
        events: [
          makePuxEvent(5, "record_state_changed", {
            field: "readiness_state",
            next_value: "coverage_complete",
          }),
          // record_state_changed is lightweight; force reload via flag so
          // the client fetches the final coverage snapshot.
        ],
        reloadRequired: true,
        reloadReason: "readiness_coverage_complete",
      }),
      expectDecision: "reload",
      expectReloadReason: "readiness_coverage_complete",
      snapshotOnReload: s5,
      expectPhaseAfter: "coverage_complete",
      expectCursor: 5,
    },
    {
      kind: "assert",
      phase: "coverage_complete",
      readiness: "coverage_complete",
      cursor: 5,
      scrollTop: interactionAfterFirstLayer.scrollTop,
      expandedPanel: "quick_peek",
      activeAnchorId: "seg_1",
      visibleLayers: [
        "grammar_note",
        "sentence_analysis",
        "translation",
        "vocabulary",
      ],
      lastRejected: false,
    },
  ];
}
