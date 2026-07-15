# Ask Claread Reader Workspace v2

Status: accepted design specification, 2026-07-10.

## Scope

This specification replaces the initial launcher-only integration for the Reader Record Plate. It defines the first delivery phase for the Ask Claread workspace: a real docked sidecar on sufficiently wide Reader layouts, a right-bottom floating workspace elsewhere, and an outline that remains usable in both forms.

The article remains the Reader's primary stage. Ask Claread is a secondary workspace that may reserve space beside the article, but never turns the article into an obscured background for chat.

## Product decisions

- The lower-right Claread launcher remains the default entry point when Ask is closed. It reuses `ClareadAiMark`; it is not a generic sparkle control.
- Entry point, context, and workspace form are independent. Every entry only opens Ask and supplies context; no entry may choose a surface.
- The initial requested surface for every Reader page visit is `sidecar`. Explicit user switches remain for the mounted page session, including close/reopen, and are not stored as a user preference.
- A requested `sidecar` automatically presents as `floating` when the current Reader workspace cannot safely hold the reading area, outline gutter, and Ask column. It automatically returns to sidecar when space is restored.
- An explicitly requested `floating` remains floating at every width.
- In floating mode, the outline mini rail remains visible behind the Ask window. Its hover and keyboard-focus expansion is intentionally layered above the floating Ask window.
- Phase 1 does not ship user-resizable sidecar width. Width resize, pointer drag, and keyboard resize are Phase 2 work, not an implicit partial feature.

## Non-goals

- No backend, thread, SSE, attachment, stable-document, or anchor-contract changes.
- No permanent user setting or record setting for surface or width.
- No global App Shell reflow and no changes to non-Plate Ask callers.
- No resize separator in Phase 1.

## State model

`requestedSurface` is the page-session preference. `effectiveSurface` is a derived presentation decision and must not overwrite the request.

```text
open               boolean; existing visibility state
requestedSurface   sidecar | floating; useState, initial sidecar
effectiveSurface   sidecar | floating; derived from requestedSurface + capacity
context            page / selection / note attachment; existing contract
```

```text
if requestedSurface === floating:
  effectiveSurface = floating
else if Reader workspace has safe sidecar capacity:
  effectiveSurface = sidecar
else:
  effectiveSurface = floating
```

Safe capacity is measured from the actual Reader workspace element with `ResizeObserver`, not from `window.innerWidth`. The implementation reserves:

```text
minimum reading area: 48rem
outline gutter:      2.5rem
Ask column:          clamp(24rem, 29vw, 37.5rem)
```

The article itself retains its existing `70ch` maximum and is centered inside the remaining reading area. The capacity values are Phase 1 design tokens and must be centralized in Reader CSS; no repeated literals in React components.

When a requested sidecar is automatically presented as floating, a polite live status announces that the narrower workspace is using floating Ask. This is a presentation fallback, not an error and not a change to the user preference.

## Layout contract

### Effective sidecar

On a safe-width Reader workspace, the Plate owns a three-column layout:

```text
[ reading available area ][ outline gutter ][ Ask docked column ]
```

- The current article/header/document tree belongs in the first column.
- The outline belongs in the second column. Its compact rail is always available; expanded navigation opens leftward into the reading area.
- Ask belongs in the third column and fills the Reader work height. It is `relative`, not `fixed`, and must not use viewport positioning.
- The document's `70ch` column is centered within the first column, not within the full viewport.
- The existing rule that hides `.reader-record-outline-slot` while Ask is open is removed.

### Effective floating

Floating does not change Reader canvas columns or article centering.

- Ask is a viewport overlay anchored to the desktop bottom-right, with bounded width and height. It must not use the former desktop centered/top placement.
- The outline compact rail remains at the right edge below the Ask window.
- A hovered or keyboard-focused outline panel renders above the Ask window and expands leftward. It must remain reachable without hover.
- The z-index roles are semantic and ordered: outline rail < floating Ask < expanded outline panel. Values are defined in Reader CSS rather than chosen ad hoc in component classes.

## Component boundaries

### `ReaderRecordPlateSurface`

Owns page-session `requestedSurface`, the workspace measurement ref, and the derived `effectiveSurface`. It composes the reading stage, outline, and Ask into the sidecar grid only for this Plate. Other Reader surfaces do not inherit this layout by accident.

### `useReaderAskPresentation`

Introduce a small, independently tested hook responsible only for observing workspace capacity and deriving `effectiveSurface`. It exposes no Ask API or attachment concerns. Update React state only when the derived boolean changes.

### `AiWorkspacePanel`

Retains threads, composer, streaming, attachments, and current surface switch. Add a backward-compatible layout prop:

```ts
layout?: "overlay" | "docked";
```

Existing callers default to `overlay`. The Plate alone passes `docked` when `effectiveSurface === "sidecar"`. In docked mode the panel has normal layout classes; in overlay mode it uses floating placement. `surface` remains the user-visible presentation state and must never silently mutate the requested state.

The workspace is a labelled complementary region, not a modal dialog: it must not trap focus. On an explicit surface switch, move focus to the Ask heading or composer and announce the resulting form through an `aria-live="polite"` status.

### `ReaderRecordNavigationRail`

Keeps its current navigation semantics, keyboard activation, and hover/focus expansion. It must be able to render in the reserved sidecar gutter, and its expanded state must be given the documented floating-mode z-index. It does not need Ask API knowledge.

## Interaction rules

- Opening Ask from launcher, selection, vocabulary, note, or quick action preserves `requestedSurface`.
- Closing and reopening preserves `requestedSurface` during the mounted page session.
- Choosing floating is explicit and sticks for the session.
- Choosing sidecar at insufficient capacity records a sidecar request but continues to present floating with a concise capacity notice. It recovers to sidecar when capacity returns.
- Switching forms never clears thread, pending request, attachment, or composer draft state.
- Phase 1 uses a labelled compact surface control, not an icon-only switch.

## Validation contract

Unit and component tests must cover:

1. `useReaderAskPresentation`: requested floating always floats; requested sidecar docks only at capacity; a capacity change automatically falls back and recovers without changing the request.
2. Plate sidecar: grid/docked Ask is not fixed; Reader canvas reflows; outline is present.
3. Plate floating: panel is bottom-right, canvas does not gain sidecar reflow, and outline remains present.
4. Contextual Ask: selection and note entry preserve the current request and existing serialized attachments.
5. Accessibility: labelled surface control, live status, focus movement, and outline keyboard expansion.

Browser verification must inspect at least 1280px, 1536px, 1920px, and 2100px viewport widths. The evaluation checks article centering, outline availability, actual floating position, and no clipping after opening, switching, closing, and reopening Ask. Include browser zoom or enlarged text coverage before declaring the responsive work complete.

Required commands for the implementation slice:

```text
pnpm --filter=@claread/web test -- AiWorkspacePanel.test.tsx ReaderRecordPlateSurface.test.tsx ReaderRecordNavigationRail.test.tsx
pnpm --filter=@claread/web typecheck
git diff --check
```

## Phase 2

After Phase 1 is visually accepted, add a session-only `sidecarWidth` and an accessible resize separator. It must support pointer, touch, and keyboard adjustment, expose separator ARIA values, clamp against the same safe-capacity rule, and update an element CSS variable during drag rather than forcing a full Reader re-render.
