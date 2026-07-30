/**
 * Shared types for the R2.5 E2E Ask Activity harness.
 *
 * Kept in a dependency-free module so both the Next.js harness and the
 * Playwright specs can import the same shape without pulling in React or
 * browser-only code.
 */

export type SpikeSseScriptEvent = {
  event: string;
  data: Record<string, unknown>;
  /** When true, stream pauses after this event until releaseNext/releaseAll. */
  hold?: boolean;
  /** Optional delay (ms) before emitting this event. */
  delayMs?: number;
  /**
   * When set, emit this SSE text verbatim (skip JSON encode).
   * Used by R8.1 parse_error gates.
   */
  raw?: string;
};

export type SpikeAskActivityApi = {
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
};

declare global {
  interface Window {
    __spikeAskActivity?: SpikeAskActivityApi;
  }
}

export {};
