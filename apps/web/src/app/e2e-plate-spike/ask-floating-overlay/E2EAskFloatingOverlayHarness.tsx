"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import { AiWorkspacePanel } from "@/components/reader/AiWorkspacePanel";
import type { AiWorkspaceSurface } from "@/components/reader/AiWorkspacePanel";
import type { ReaderAskPageIdentity } from "@/lib/reader-plate/bridges/ask/types";
import type { SpikeAskFloatingOverlayApi, SpikeSseScriptEvent } from "./types";

/**
 * ASK-UX-MOBILE-R3 — E2E harness for the floating-overlay Ask panel.
 *
 * Mounts a REAL AiWorkspacePanel with the props that the R0–R2 spec
 * identified as the actual user-facing mobile configuration:
 *   - layout="overlay"
 *   - surface="floating"
 *   - onChangeSurface (wired to a React state setter)
 *   - hasSidecarCapacity (toggleable at runtime via __spikeAskFloatingOverlay)
 *
 * Unlike the R2.5 ask-activity harness, this harness ALWAYS renders a
 * SCROLLABLE BACKGROUND (a tall <ol> with many list items) so the spec
 * can verify the body-scroll lock with a real overflow condition. The
 * previous harness's body had no overflow, so `scrollTop === 0` could
 * not prove the lock worked.
 *
 * The harness exposes `window.__spikeAskFloatingOverlay` to drive a
 * gated fetch interceptor for `/messages/stream`:
 *   - `setScript(events)` configures the next stream response
 *   - `releaseNext()` / `releaseAll()` unblock held events mid-stream
 *   - `reset()` remounts the panel
 *   - `setCapacity(bool)` toggles hasSidecarCapacity
 *   - `openPanel()` / `closePanel()` drive panel visibility
 *
 * Gate: ONLY rendered when CLAREAD_ENABLE_E2E_SPIKE === "1".
 */

export type { SpikeAskFloatingOverlayApi, SpikeSseScriptEvent };

const RECORD_ID = "test-record-r3-floating-overlay";

const PAGE_IDENTITY: ReaderAskPageIdentity = {
  recordId: RECORD_ID,
  recordTitle: "测试文章 - R3 Floating Overlay 验收",
  surface: "reader",
  source: "reader_2_0",
  availableContextCapabilities: [],
};

type StreamController = {
  script: SpikeSseScriptEvent[];
  emitted: number;
  waiting: boolean;
  finished: boolean;
  releaseResolvers: Array<() => void>;
  waitForRelease: () => Promise<void>;
  signalRelease: () => void;
  signalReleaseAll: () => void;
};

function createStreamController(script: SpikeSseScriptEvent[]): StreamController {
  const releaseResolvers: Array<() => void> = [];
  const controller: StreamController = {
    script,
    emitted: 0,
    waiting: false,
    finished: false,
    releaseResolvers,
    waitForRelease() {
      controller.waiting = true;
      return new Promise<void>((resolve) => {
        releaseResolvers.push(() => {
          controller.waiting = false;
          resolve();
        });
      });
    },
    signalRelease() {
      const next = releaseResolvers.shift();
      if (next) next();
    },
    signalReleaseAll() {
      while (releaseResolvers.length > 0) {
        const next = releaseResolvers.shift();
        next?.();
      }
    },
  };
  return controller;
}

function encodeSse(event: string, data: Record<string, unknown>): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

/**
 * Scrollable background — produces real document.body overflow. The list
 * is long enough that even at 1440x900 the body must scroll to see the
 * bottom. Without this, the body-lock assertion cannot distinguish a
 * real lock from a "page too short to scroll" false positive.
 */
function ScrollableBackground() {
  const items = Array.from({ length: 60 }, (_, index) => index);
  return (
    <ol
      data-testid="ask-floating-overlay-background"
      className="mx-auto max-w-2xl px-6 py-8 text-[15px] leading-7 text-ink/80"
    >
      {items.map((index) => (
        <li key={index} className="border-b border-hairline/40 py-3">
          背景段落 #{index + 1} — 这是一段用于制造可滚动背景的占位文本。Institutional
          memory shapes policy choices in subtle ways, and this paragraph exists
          purely so the document body has real overflow during the floating
          overlay body-lock verification.
        </li>
      ))}
    </ol>
  );
}

export default function E2EAskFloatingOverlayHarness() {
  const [open, setOpen] = useState(true);
  const [resetKey, setResetKey] = useState(0);
  const [surface, setSurface] = useState<AiWorkspaceSurface>("floating");
  const [capacity, setCapacity] = useState<boolean>(false);
  const scriptRef = useRef<SpikeSseScriptEvent[]>([]);
  const activeControllerRef = useRef<StreamController | null>(null);
  const originalFetchRef = useRef<typeof window.fetch | null>(null);

  // Keep latest state in refs so the stream interceptor (installed once)
  // always reads the current values without re-installing.
  const openRef = useRef(open);
  const surfaceRef = useRef(surface);
  const capacityRef = useRef(capacity);
  openRef.current = open;
  surfaceRef.current = surface;
  capacityRef.current = capacity;

  const handleReset = useCallback(() => {
    activeControllerRef.current?.signalReleaseAll();
    activeControllerRef.current = null;
    scriptRef.current = [];
    setResetKey((k) => k + 1);
  }, []);

  const handleSetScript = useCallback((events: SpikeSseScriptEvent[]) => {
    scriptRef.current = events.map((item) => ({
      event: item.event,
      data: { ...item.data },
      hold: item.hold === true,
      delayMs: item.delayMs,
    }));
  }, []);

  const handleReleaseNext = useCallback(() => {
    activeControllerRef.current?.signalRelease();
  }, []);

  const handleReleaseAll = useCallback(() => {
    activeControllerRef.current?.signalReleaseAll();
  }, []);

  const getStreamState = useCallback(() => {
    const active = activeControllerRef.current;
    if (!active) {
      return {
        total: scriptRef.current.length,
        emitted: 0,
        waiting: false,
        finished: false,
      };
    }
    return {
      total: active.script.length,
      emitted: active.emitted,
      waiting: active.waiting,
      finished: active.finished,
    };
  }, []);

  // Install stream interceptor once; always read the latest script/controller refs.
  useEffect(() => {
    if (originalFetchRef.current == null) {
      originalFetchRef.current = window.fetch.bind(window);
    }
    const originalFetch = originalFetchRef.current;

    window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const url =
        typeof input === "string"
          ? input
          : input instanceof URL
            ? input.toString()
            : input.url;

      if (!url.includes("/messages/stream")) {
        return originalFetch(input, init);
      }

      const script = scriptRef.current.slice();
      const streamController = createStreamController(script);
      activeControllerRef.current = streamController;

      const encoder = new TextEncoder();
      const body = new ReadableStream<Uint8Array>({
        async start(controller) {
          try {
            for (const item of streamController.script) {
              if (typeof item.delayMs === "number" && item.delayMs > 0) {
                await new Promise((resolve) => setTimeout(resolve, item.delayMs));
              }
              controller.enqueue(encoder.encode(encodeSse(item.event, item.data)));
              streamController.emitted += 1;
              if (item.hold) {
                await streamController.waitForRelease();
              }
            }
          } finally {
            streamController.finished = true;
            controller.close();
          }
        },
      });

      return new Response(body, {
        status: 200,
        headers: {
          "Content-Type": "text/event-stream",
          "Cache-Control": "no-cache",
        },
      });
    };

    return () => {
      if (originalFetchRef.current) {
        window.fetch = originalFetchRef.current;
      }
    };
  }, []);

  useEffect(() => {
    window.__spikeAskFloatingOverlay = {
      ready: true,
      setScript: handleSetScript,
      releaseNext: handleReleaseNext,
      releaseAll: handleReleaseAll,
      reset: handleReset,
      getStreamState,
      setCapacity: (next: boolean) => {
        setCapacity(next);
      },
      getCapacity: () => capacityRef.current,
      getSurface: () => surfaceRef.current,
      openPanel: () => {
        setOpen(true);
      },
      closePanel: () => {
        setOpen(false);
      },
      isOpen: () => openRef.current,
    };
    return () => {
      delete window.__spikeAskFloatingOverlay;
    };
  }, [
    getStreamState,
    handleReleaseAll,
    handleReleaseNext,
    handleReset,
    handleSetScript,
  ]);

  return (
    <div
      data-testid="ask-floating-overlay-host"
      className="relative min-h-screen w-full bg-background"
    >
      <div className="border-b border-border bg-surface/40 px-4 py-2 text-xs text-muted-foreground">
        R3 E2E Harness — Floating Overlay (layout=overlay, surface=floating,
        onChangeSurface wired, hasSidecarCapacity toggleable, scrollable
        background)
      </div>
      <ScrollableBackground />
      <AiWorkspacePanel
        key={resetKey}
        open={open}
        onToggle={() => setOpen((o) => !o)}
        onChangeSurface={(next) => setSurface(next)}
        surface={surface}
        hasSidecarCapacity={capacity}
        layout="overlay"
        presentation="intensive"
        pageIdentity={PAGE_IDENTITY}
        recordId={RECORD_ID}
        recordScope="reading_record"
        recordTitle={PAGE_IDENTITY.recordTitle}
        attachments={[]}
        onRemoveAttachment={() => undefined}
        onClearAttachments={() => undefined}
      />
    </div>
  );
}
