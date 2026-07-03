"use client";

import { useMemo, useState } from "react";
import { AnalyzeSubmitForm } from "./AnalyzeSubmitForm";
import { ArtifactIntakePanel } from "./ArtifactIntakePanel";
import { normalizeReaderRecordReadingDefaults, type ReadingDefaultState } from "@/lib/reading-defaults";

type Mode = "paste" | "file";

export function ReadPageIntake(props: ReadingDefaultState) {
  const [mode, setMode] = useState<Mode>("paste");
  const recordDefaults = useMemo(
    () => normalizeReaderRecordReadingDefaults({ readingGoal: props.readingGoal, readingVariant: props.readingVariant }),
    [props.readingGoal, props.readingVariant],
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4">
      <div
        role="tablist"
        aria-label="输入方式"
        className="flex w-full items-center gap-1 self-start rounded-[10px] border border-hairline/60 bg-surface/40 p-1 font-sans"
      >
        <button
          type="button"
          role="tab"
          aria-selected={mode === "paste"}
          onClick={() => setMode("paste")}
          className={`focus-ring min-h-9 rounded-[7px] px-3 text-[0.82rem] font-medium transition-colors ${
            mode === "paste"
              ? "bg-reader-paper text-ink shadow-[0_1px_2px_rgba(23,21,17,0.06)]"
              : "text-muted hover:text-ink"
          }`}
        >
          贴入文本
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={mode === "file"}
          onClick={() => setMode("file")}
          className={`focus-ring min-h-9 rounded-[7px] px-3 text-[0.82rem] font-medium transition-colors ${
            mode === "file"
              ? "bg-reader-paper text-ink shadow-[0_1px_2px_rgba(23,21,17,0.06)]"
              : "text-muted hover:text-ink"
          }`}
        >
          上传文件
        </button>
      </div>

      {mode === "paste" ? (
        <AnalyzeSubmitForm
          readingGoal={props.readingGoal}
          readingVariant={props.readingVariant}
        />
      ) : (
        <div className="flex min-h-0 flex-1 w-full">
          <div className="flex min-h-0 flex-1 w-full shrink-0 flex-col overflow-hidden rounded-[10px] bg-[linear-gradient(180deg,rgba(251,247,238,0.45),rgba(251,247,238,0.12)_48%,rgba(251,247,238,0)_100%)] ring-1 ring-hairline/35 lg:min-h-[31rem] 2xl:min-h-[34rem]">
            <div className="relative z-10 flex min-h-0 flex-1 flex-col px-6 py-6 sm:px-10 sm:py-8">
              <ArtifactIntakePanel
                readingGoal={recordDefaults.readingGoal}
                readingVariant={recordDefaults.readingVariant}
                onUseTextMode={() => setMode("paste")}
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
