import type { ReadingGoalDto, ReadingVariantDto } from "@/types/api/tasks";

export type ReadPageSubmitMode = "legacy" | "reading-record";

export const READ_PAGE_SUBMIT_MODE: ReadPageSubmitMode = "reading-record";

export interface ReadPageSubmitPayloadInput {
  text: string;
  readingGoal: ReadingGoalDto;
  readingVariant: ReadingVariantDto;
}

export function readPageSubmitEndpoint(
  mode: ReadPageSubmitMode = READ_PAGE_SUBMIT_MODE,
) {
  return mode === "reading-record"
    ? "/api/web/reading-record/submit"
    : "/api/web/analysis/submit";
}

export function readPageSubmitRequestBody(
  input: ReadPageSubmitPayloadInput,
  mode: ReadPageSubmitMode = READ_PAGE_SUBMIT_MODE,
) {
  if (mode === "reading-record") {
    return { plainText: input.text };
  }

  return {
    text: input.text,
    readingGoal: input.readingGoal,
    readingVariant: input.readingVariant,
  };
}
