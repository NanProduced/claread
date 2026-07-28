"use client";

/**
 * L1 input spike harness — 在真实浏览器里挂载生产 MarkdownTextInput，
 * 验证 L1 插件接入 + clipboard sanitize + 不静默丢失合同的端到端行为。
 *
 * 暴露：
 * - `window.__inputSpikeReady`
 * - `window.__inputSpike.handle` — MarkdownTextInputHandle
 *   （getSubmitText / getMarkdown / setValue / clear / focus）
 * - `window.__inputSpike.lastChange` — 最近一次 onChange 的 markdown
 * - `window.__inputSpike.lastDegraded` — 最近一次 onDegraded status
 *
 * Boundary: 测试专用 route（CLAREAD_ENABLE_E2E_SPIKE gate），不改生产页面。
 */

import { useEffect, useRef } from "react";

import {
  MarkdownTextInput,
  type MarkdownTextInputHandle,
} from "@/app/(private)/app/read/MarkdownTextInput";

declare global {
  interface Window {
    __inputSpikeReady?: boolean;
    __inputSpike?: {
      handle: MarkdownTextInputHandle | null;
      lastChange: string;
      lastDegraded: string | null;
      lastLint: string | null;
    };
  }
}

export default function E2EInputSpikeHarness() {
  const handleRef = useRef<MarkdownTextInputHandle>(null);
  // 可变 spike 状态用 ref 持有（react-hooks/immutability：useState 值不可直接改）。
  const spikeRef = useRef({
    handle: null as MarkdownTextInputHandle | null,
    lastChange: "",
    lastDegraded: null as string | null,
    lastLint: null as string | null,
  });

  useEffect(() => {
    spikeRef.current.handle = handleRef.current;
    window.__inputSpike = spikeRef.current;
    window.__inputSpikeReady = true;
  }, []);

  return (
    <main className="min-h-screen bg-background px-8 py-8">
      <h1 className="mb-4 text-lg font-semibold text-ink">
        E2E Input Spike (L1) — production MarkdownTextInput
      </h1>
      <div className="mx-auto max-w-[72ch]">
        <MarkdownTextInput
          ref={handleRef}
          initialValue=""
          onChange={(md) => {
            spikeRef.current.lastChange = md;
          }}
          onSubmit={() => {
            /* no-op: e2e 直接读 handle */
          }}
          onDegraded={(result) => {
            spikeRef.current.lastDegraded = result.status;
          }}
          onLintResult={(result) => {
            spikeRef.current.lastLint =
              result.warnings.length === 0 ? "ok" : "warnings";
          }}
        />
      </div>
    </main>
  );
}
