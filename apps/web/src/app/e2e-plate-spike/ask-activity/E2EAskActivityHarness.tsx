"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import { AiWorkspacePanel } from "@/components/reader/AiWorkspacePanel";
import type { ReaderAskPageIdentity } from "@/lib/reader-plate/bridges/ask/types";
import type { SpikeAskActivityApi, SpikeSseScriptEvent } from "./types";

/**
 * R2.5 — E2E harness for Agentic Ask Activity browser acceptance.
 *
 * Mounts a REAL AiWorkspacePanel. Synthetic SSE is driven by a gated
 * fetch interceptor exposed on `window.__spikeAskActivity`:
 * - `setScript(events)` configures the next /messages/stream response
 * - `releaseNext()` / `releaseAll()` unblocks held events mid-stream
 * - `waitForPhase(phase)` resolves when activity row shows that phase
 * - `reset()` remounts the panel
 *
 * Gate: ONLY rendered when CLAREAD_ENABLE_E2E_SPIKE === "1".
 */

export type { SpikeAskActivityApi, SpikeSseScriptEvent };

const RECORD_ID = "test-record-r2-activity";

const PAGE_IDENTITY: ReaderAskPageIdentity = {
  recordId: RECORD_ID,
  recordTitle: "测试文章 - R2.5 Activity 验收",
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

export default function E2EAskActivityHarness() {
  const [open, setOpen] = useState(true);
  const [resetKey, setResetKey] = useState(0);
  const scriptRef = useRef<SpikeSseScriptEvent[]>([]);
  const activeControllerRef = useRef<StreamController | null>(null);
  const originalFetchRef = useRef<typeof window.fetch | null>(null);

  const handleReset = useCallback(() => {
    activeControllerRef.current?.signalReleaseAll();
    activeControllerRef.current = null;
    scriptRef.current = [];
    setResetKey((k) => k + 1);
  }, []);

  const handleSetScript = useCallback((events: SpikeSseScriptEvent[]) => {
    scriptRef.current = events.map((item) => ({
      event: item.event,
      data: { ...(item.data ?? {}) },
      hold: item.hold === true,
      delayMs: item.delayMs,
      raw: typeof item.raw === "string" ? item.raw : undefined,
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
              if (typeof item.raw === "string" && item.raw.length > 0) {
                controller.enqueue(encoder.encode(item.raw));
              } else {
                controller.enqueue(
                  encoder.encode(encodeSse(item.event, item.data)),
                );
              }
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
    window.__spikeAskActivity = {
      ready: true,
      setScript: handleSetScript,
      releaseNext: handleReleaseNext,
      releaseAll: handleReleaseAll,
      reset: handleReset,
      getStreamState,
    };
    return () => {
      delete window.__spikeAskActivity;
    };
  }, [
    getStreamState,
    handleReleaseAll,
    handleReleaseNext,
    handleReset,
    handleSetScript,
  ]);

  return (
    <div className="flex h-screen w-full flex-col bg-background">
      <div className="border-b border-border px-4 py-2 text-xs text-muted-foreground">
        R2.5 E2E Harness — Agentic Ask Activity
      </div>
      <div className="flex-1 overflow-hidden">
        <AiWorkspacePanel
          key={resetKey}
          open={open}
          onToggle={() => setOpen((o) => !o)}
          pageIdentity={PAGE_IDENTITY}
          recordId={RECORD_ID}
          recordScope="reading_record"
          recordTitle={PAGE_IDENTITY.recordTitle}
          attachments={[]}
          onRemoveAttachment={() => undefined}
          onClearAttachments={() => undefined}
          layout="docked"
          presentation="intensive"
        />
      </div>
    </div>
  );
}
