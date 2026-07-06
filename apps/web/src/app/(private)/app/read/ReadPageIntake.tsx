"use client";

import { useMemo } from "react";
import { AnalyzeSubmitForm } from "./AnalyzeSubmitForm";
import { normalizeReaderRecordReadingDefaults, type ReadingDefaultState } from "@/lib/reading-defaults";

export function ReadPageIntake(props: ReadingDefaultState) {
  const recordDefaults = useMemo(
    () => normalizeReaderRecordReadingDefaults({ readingGoal: props.readingGoal, readingVariant: props.readingVariant }),
    [props.readingGoal, props.readingVariant],
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4">
      <AnalyzeSubmitForm
        readingGoal={recordDefaults.readingGoal}
        readingVariant={recordDefaults.readingVariant}
      />
    </div>
  );
}
