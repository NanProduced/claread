/**
 * Progressive transition fixture / event replay.
 *
 * No real LLM. Pure projection + polling decision contract only.
 */

import { describe, expect, it } from "vitest";

import {
  applyEventPoll,
  applySnapshotReload,
  advanceCursorAfterSuccessfulReload,
  buildCanonicalProgressiveReplaySteps,
  classifyProgressivePhase,
  createInitialProgressiveState,
  EMPTY_INTERACTION_STATE,
  formatProgressiveStatusLine,
  listPublishedLayerKeys,
  listVisibleLayerTypes,
  makePuxEvent,
  makePuxPollResponse,
  makePuxSnapshot,
  pathExistsInPlateChildren,
  preserveInteractionAcrossValueSwap,
  replayProgressiveSteps,
  resolvePreservedSelection,
  type ProgressiveInteractionState,
} from "@/lib/reader-plate-snapshot/progressive-transition";

// ---------------------------------------------------------------------------
// Phase classification
// ---------------------------------------------------------------------------

describe("classifyProgressivePhase", () => {
  it("returns loading when snapshot is null", () => {
    expect(classifyProgressivePhase(null)).toBe("loading");
  });

  it("returns article_ready_no_layers when ready but no published layers", () => {
    const snap = makePuxSnapshot({
      snapshotId: "s1",
      lastEventSequence: 1,
      readiness: "article_ready",
      layers: [],
    });
    expect(classifyProgressivePhase(snap)).toBe("article_ready_no_layers");
    expect(listVisibleLayerTypes(snap)).toEqual([]);
  });

  it("returns first_layer when only translation is published", () => {
    const snap = makePuxSnapshot({
      snapshotId: "s2",
      lastEventSequence: 2,
      readiness: "article_ready",
      layers: ["translation"],
    });
    expect(classifyProgressivePhase(snap)).toBe("first_layer");
    expect(listVisibleLayerTypes(snap)).toEqual(["translation"]);
  });

  it("returns partial_ready when 2+ layer types are published", () => {
    const snap = makePuxSnapshot({
      snapshotId: "s3",
      lastEventSequence: 3,
      readiness: "initial_enhancement_ready",
      layers: ["translation", "vocabulary"],
    });
    expect(classifyProgressivePhase(snap)).toBe("partial_ready");
  });

  it("returns coverage_complete when readiness is coverage_complete", () => {
    const snap = makePuxSnapshot({
      snapshotId: "s5",
      lastEventSequence: 5,
      readiness: "coverage_complete",
      layers: ["translation", "vocabulary", "grammar_note"],
    });
    expect(classifyProgressivePhase(snap)).toBe("coverage_complete");
  });
});

describe("formatProgressiveStatusLine", () => {
  it("describes article_ready_no_layers without layer list", () => {
    expect(formatProgressiveStatusLine("article_ready_no_layers")).toContain(
      "批注生成中",
    );
  });

  it("lists arrived layers for first_layer and partial_ready", () => {
    expect(
      formatProgressiveStatusLine("first_layer", ["translation"]),
    ).toContain("译文");
    expect(
      formatProgressiveStatusLine("partial_ready", [
        "translation",
        "vocabulary",
      ]),
    ).toMatch(/译文|词汇/);
  });

  it("marks coverage_complete", () => {
    expect(
      formatProgressiveStatusLine("coverage_complete", ["translation"]),
    ).toContain("完整解析完成");
  });
});

// ---------------------------------------------------------------------------
// Canonical progressive replay (happy path)
// ---------------------------------------------------------------------------

describe("canonical progressive transition replay", () => {
  it("walks loading → first layer → partial → coverage_complete with stable interaction", () => {
    const result = replayProgressiveSteps(buildCanonicalProgressiveReplaySteps());

    expect(result.failures).toEqual([]);
    expect(result.state.phase).toBe("coverage_complete");
    expect(result.state.readiness).toBe("coverage_complete");
    expect(result.state.cursor).toBe(5);
    expect(result.state.interaction.scrollTop).toBe(420);
    expect(result.state.interaction.expandedPanel).toBe("quick_peek");
    expect(result.state.interaction.activeAnchorId).toBe("seg_1");
    expect(result.state.lastRejected).toBe(false);

    // Phase trace must be monotonically non-decreasing (ignoring loading start).
    expect(result.phaseTrace).toContain("article_ready_no_layers");
    expect(result.phaseTrace).toContain("first_layer");
    expect(result.phaseTrace).toContain("partial_ready");
    expect(result.phaseTrace[result.phaseTrace.length - 1]).toBe(
      "coverage_complete",
    );

    // Reload decisions for each layer_published / readiness step.
    expect(result.decisionTrace.filter((d) => d === "reload").length).toBe(4);
  });

  it("documents expected readiness / layers / cursor per progressive step", () => {
    const steps = buildCanonicalProgressiveReplaySteps();
    // Step 0: initial load
    expect(steps[0]).toMatchObject({
      kind: "load_snapshot",
      expectPhase: "article_ready_no_layers",
      expectReadiness: "article_ready",
    });
    // Step 1: first layer translation
    expect(steps[1]).toMatchObject({
      kind: "poll",
      expectDecision: "reload",
      expectPhaseAfter: "first_layer",
      expectCursor: 2,
    });
    // Final assert: coverage_complete
    const last = steps[steps.length - 1];
    expect(last).toMatchObject({
      kind: "assert",
      phase: "coverage_complete",
      readiness: "coverage_complete",
      cursor: 5,
      scrollTop: 420,
      expandedPanel: "quick_peek",
    });
  });
});

// ---------------------------------------------------------------------------
// Stale / duplicate / out-of-order events must not regress
// ---------------------------------------------------------------------------

describe("stale / duplicate / out-of-order event safety", () => {
  function loadedAtTranslation() {
    const firstSnapshot = makePuxSnapshot({
      snapshotId: "snap_a",
      lastEventSequence: 1,
      readiness: "article_ready",
      layers: [],
    });
    const secondSnapshot = makePuxSnapshot({
      snapshotId: "snap_b",
      lastEventSequence: 2,
      readiness: "article_ready",
      layers: ["translation"],
    });
    return replayProgressiveSteps([
      {
        kind: "load_snapshot",
        snapshot: firstSnapshot,
        expectPhase: "article_ready_no_layers",
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
        snapshotOnReload: secondSnapshot,
        expectCursor: 2,
      },
      {
        kind: "set_interaction",
        interaction: {
          scrollTop: 300,
          expandedPanel: "quick_peek",
          activeAnchorId: "seg_1",
        },
      },
    ]);
  }

  it("rejects stale snapshot with lower last_event_sequence (no layer rollback)", () => {
    const base = loadedAtTranslation();
    expect(base.failures).toEqual([]);
    const published = listPublishedLayerKeys(base.state.snapshot!);
    expect(published.length).toBe(1);

    const stale = makePuxSnapshot({
      snapshotId: "snap_stale",
      lastEventSequence: 1, // older than cursor=2
      readiness: "article_ready",
      layers: [], // would wipe translation if accepted
    });

    const applied = applySnapshotReload(base.state, stale);
    expect(applied.ok).toBe(false);
    if (!applied.ok) {
      expect(applied.reason).toBe("stale_snapshot_sequence");
    }
    // Original layers preserved.
    expect(base.state.visibleLayerTypes).toEqual(["translation"]);
    expect(base.state.interaction.scrollTop).toBe(300);
    expect(base.state.interaction.expandedPanel).toBe("quick_peek");
  });

  it("rejects same-generation layer regression on sequence advance", () => {
    const base = loadedAtTranslation();
    const regressing = makePuxSnapshot({
      snapshotId: "snap_regress",
      lastEventSequence: 3,
      readiness: "article_ready",
      layers: [], // drops translation while advancing sequence
    });
    const applied = applySnapshotReload(base.state, regressing);
    expect(applied.ok).toBe(false);
    if (!applied.ok) {
      expect(applied.reason).toBe("layer_regression");
    }
  });

  it("duplicate poll of already-consumed layer_published advances/caught_up without rollback", () => {
    const base = loadedAtTranslation();
    // Cursor already 2; replaying the same events with after=2 and empty
    // or non-reload events should be caught_up / advance, not reload-regress.
    const poll = applyEventPoll(
      base.state,
      makePuxPollResponse({
        afterSequence: 2,
        nextAfterSequence: 2,
        lastEventSequence: 2,
        events: [],
      }),
    );
    expect(poll.decision.kind).toBe("caught_up");
    expect(poll.requiresSnapshotReload).toBe(false);
    expect(poll.nextCursor).toBe(2);
  });

  it("cursor ahead of server forces reload decision (does not silently skip)", () => {
    const state = createInitialProgressiveState();
    const loaded = applySnapshotReload(
      state,
      makePuxSnapshot({
        snapshotId: "snap_c",
        lastEventSequence: 5,
        readiness: "article_ready",
        layers: ["translation"],
      }),
    );
    expect(loaded.ok).toBe(true);
    if (!loaded.ok) return;

    const poll = applyEventPoll(
      loaded.state,
      makePuxPollResponse({
        afterSequence: 5,
        nextAfterSequence: 3,
        lastEventSequence: 3,
        events: [],
      }),
    );
    // afterSequence(5) > last_event_sequence(3) → cursor_ahead_of_server reload
    expect(poll.decision.kind).toBe("reload");
    expect(poll.requiresSnapshotReload).toBe(true);
    // Cursor held until successful snapshot apply.
    expect(poll.nextCursor).toBe(5);
  });

  it("does not advance cursor on failed snapshot reload (hold)", () => {
    const base = loadedAtTranslation();
    const poll = applyEventPoll(
      base.state,
      makePuxPollResponse({
        afterSequence: 2,
        nextAfterSequence: 9,
        lastEventSequence: 9,
        events: [makePuxEvent(3, "layer_published", { layer_type: "vocabulary" })],
      }),
    );
    expect(poll.requiresSnapshotReload).toBe(true);
    // Simulate failed/skipped reload: do NOT call applySnapshotReload / advance.
    expect(poll.nextCursor).toBe(base.state.cursor);
    expect(base.state.cursor).toBe(2);
  });

  it("advanceCursorAfterSuccessfulReload refuses regression", () => {
    const base = loadedAtTranslation();
    const next = advanceCursorAfterSuccessfulReload(base.state, 1);
    expect(next.lastRejected).toBe(true);
    expect(next.rejectReason).toBe("cursor_regression");
    expect(next.cursor).toBe(2);
  });
});

// ---------------------------------------------------------------------------
// Interaction preservation pure helpers (surface contract)
// ---------------------------------------------------------------------------

describe("interaction preservation across value swap", () => {
  it("preserves scrollTop always and selection when paths still exist", () => {
    const previous: ProgressiveInteractionState = {
      ...EMPTY_INTERACTION_STATE,
      scrollTop: 880,
      selection: { anchorPath: [0, 1], focusPath: [0, 1] },
      expandedPanel: "grammar",
      activeGrammarItemId: "grammar_1",
      activeAnchorId: "seg_1",
    };
    const children = [
      { children: [{ text: "a" }, { text: "b" }] },
      { children: [{ text: "c" }] },
    ];
    const next = preserveInteractionAcrossValueSwap({
      previous,
      nextChildren: children,
    });
    expect(next.scrollTop).toBe(880);
    expect(next.selection).toEqual({
      anchorPath: [0, 1],
      focusPath: [0, 1],
    });
    expect(next.expandedPanel).toBe("grammar");
    expect(next.activeGrammarItemId).toBe("grammar_1");
  });

  it("clears selection when path no longer resolves but keeps scroll/panels", () => {
    const previous: ProgressiveInteractionState = {
      ...EMPTY_INTERACTION_STATE,
      scrollTop: 120,
      selection: { anchorPath: [0, 9], focusPath: [0, 9] },
      expandedPanel: "quick_peek",
      activeAnchorId: "seg_1",
    };
    const children = [{ children: [{ text: "only" }] }];
    const next = preserveInteractionAcrossValueSwap({
      previous,
      nextChildren: children,
    });
    expect(next.scrollTop).toBe(120);
    expect(next.selection).toBeNull();
    expect(next.expandedPanel).toBe("quick_peek");
    expect(next.activeAnchorId).toBe("seg_1");
  });

  it("pathExistsInPlateChildren validates nested paths", () => {
    const tree = [{ children: [{ children: [{ text: "x" }] }] }];
    expect(pathExistsInPlateChildren(tree, [0, 0, 0])).toBe(true);
    expect(pathExistsInPlateChildren(tree, [0, 0, 1])).toBe(false);
    expect(pathExistsInPlateChildren(tree, [])).toBe(false);
    expect(resolvePreservedSelection(tree, null)).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Interaction survives multi-step snapshot reloads
// ---------------------------------------------------------------------------

describe("live update keeps interaction identity across progressive reloads", () => {
  it("keeps scroll / active anchor / quick peek across vocab+grammar arrivals", () => {
    const interaction: ProgressiveInteractionState = {
      scrollTop: 640,
      selection: { anchorPath: [0, 0], focusPath: [0, 0] },
      activeAnchorId: "seg_1",
      expandedPanel: "quick_peek",
      activeGrammarItemId: null,
    };
    const result = replayProgressiveSteps(
      buildCanonicalProgressiveReplaySteps(interaction),
    );
    expect(result.failures).toEqual([]);
    expect(result.state.interaction).toMatchObject({
      scrollTop: 640,
      activeAnchorId: "seg_1",
      expandedPanel: "quick_peek",
    });
    // Published layers grow monotonically.
    expect(result.state.publishedLayerKeys.length).toBeGreaterThanOrEqual(3);
    expect(
      result.state.publishedLayerKeys.every((key) =>
        result.state.publishedLayerKeys.includes(key),
      ),
    ).toBe(true);
  });

  it("initial client state starts as loading with empty layers", () => {
    const state = createInitialProgressiveState();
    expect(state.phase).toBe("loading");
    expect(state.cursor).toBe(0);
    expect(state.visibleLayerTypes).toEqual([]);
    expect(state.snapshot).toBeNull();
  });
});
