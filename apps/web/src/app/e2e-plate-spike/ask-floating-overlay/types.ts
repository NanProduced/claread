/**
 * ASK-UX-MOBILE-R3 — Shared types for the floating-overlay E2E harness.
 *
 * Kept dependency-free so both the Next.js harness and the Playwright spec
 * can import the same shape without pulling in React or browser-only code.
 *
 * The harness mounts a REAL AiWorkspacePanel with:
 *   - layout="overlay"
 *   - surface="floating"
 *   - onChangeSurface (wired to a state setter so the spec can drive it)
 *   - hasSidecarCapacity (toggleable via __spikeAskFloatingOverlay.setCapacity)
 *   - a SCROLLABLE BACKGROUND (a tall <ol> behind the panel) so body-lock
 *     verification has a real overflow condition to test against.
 *
 * The harness exposes `window.__spikeAskFloatingOverlay` to drive a gated
 * SSE stream: `setScript`, `releaseNext`, `releaseAll`, `reset`,
 * `getStreamState`, `setCapacity`, `getCapacity`, `getSurface`,
 * `openPanel`, `closePanel`.
 */
export type SpikeSseScriptEvent = {
  event: string;
  data: Record<string, unknown>;
  /** When true, stream pauses after this event until releaseNext/releaseAll. */
  hold?: boolean;
  /** Optional delay (ms) before emitting this event. */
  delayMs?: number;
};

export type SpikeAskFloatingOverlayApi = {
  ready: boolean;
  setScript: (events: SpikeSseScriptEvent[]) => void;
  releaseNext: () => void;
  releaseAll: () => void;
  reset: () => void;
  getStreamState: () => {
    total: number;
    emitted: number;
    waiting: boolean;
    finished: boolean;
  };
  /** Toggle hasSidecarCapacity at runtime. */
  setCapacity: (capacity: boolean) => void;
  getCapacity: () => boolean;
  /** Read the current surface selection (floating | sidecar). */
  getSurface: () => "floating" | "sidecar";
  /** Programmatically open/close the panel. */
  openPanel: () => void;
  closePanel: () => void;
  isOpen: () => boolean;
};

declare global {
  interface Window {
    __spikeAskFloatingOverlay?: SpikeAskFloatingOverlayApi;
  }
}

export {};
