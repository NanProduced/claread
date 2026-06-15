import { describe, expect, it } from "vitest";

import {
  buildSentenceAnalysisSegments,
  parseSentenceAnalysisContent,
} from "@/components/reader/reader-entry-utils";

describe("reader-entry-utils", () => {
  it("uses structured sentence analysis chunks when provided", () => {
    const parsed = parseSentenceAnalysisContent(
      "句子主干是 he said: \"...\"。",
      [
        { order: 2, label: "谓语", text: "said" },
        { order: 1, label: "主语", text: "he" },
      ],
    );

    expect(parsed.summary).toBe("句子主干是 he said: \"...\"。");
    expect(parsed.chunks).toEqual([
      { order: "1", label: "主语", text: "he" },
      { order: "2", label: "谓语", text: "said" },
    ]);
  });

  it("still parses legacy markdown sentence analysis chunks", () => {
    const parsed = parseSentenceAnalysisContent([
      "句子主干是 Institutional memory shapes policy choices.",
      "",
      "- **1. 主语**：`Institutional memory`",
      "- **2. 谓语**：`shapes`",
    ].join("\n"));

    expect(parsed.summary).toBe("句子主干是 Institutional memory shapes policy choices.");
    expect(parsed.chunks).toEqual([
      { order: "1", label: "主语", text: "Institutional memory" },
      { order: "2", label: "谓语", text: "shapes" },
    ]);
  });

  it("builds source text segments from structured chunks", () => {
    const segments = buildSentenceAnalysisSegments(
      "At the IPO, he said: \"Whoever you are watching this, SpaceX wants to be able to take you to the Moon.\"",
      [
        { label: "主句引述", text: "At the IPO, he said:" },
        { label: "引语主句主干", text: "SpaceX wants to be able to" },
      ],
    );

    expect(segments).toEqual([
      {
        start: 0,
        end: 19,
        label: "主句引述",
        index: 0,
      },
      {
        start: 53,
        end: 79,
        label: "引语主句主干",
        index: 1,
      },
    ]);
  });
});
