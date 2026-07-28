import { notFound } from "next/navigation";

import E2EInputSpikeHarness from "./E2EInputSpikeHarness";

/**
 * L1 input spike — 生产 MarkdownTextInput 的浏览器 harness。
 * 仅在 CLAREAD_ENABLE_E2E_SPIKE === "1" 时渲染，否则 404。
 */

const ENABLE_E2E_SPIKE = process.env.CLAREAD_ENABLE_E2E_SPIKE;

export default function E2EInputSpikePage() {
  if (ENABLE_E2E_SPIKE !== "1") {
    notFound();
  }

  return <E2EInputSpikeHarness />;
}
