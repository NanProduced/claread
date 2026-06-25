import type { ReadingGoalDto, ReadingVariantDto } from "@/types/api/tasks";

export type ReadPageSubmitMode = "reading-record";

export const READ_PAGE_SUBMIT_MODE: ReadPageSubmitMode = "reading-record";

export interface ReadPageSubmitPayloadInput {
  text: string;
  readingGoal: ReadingGoalDto;
  readingVariant: ReadingVariantDto;
}

export function readPageSubmitEndpoint(
  mode: ReadPageSubmitMode = READ_PAGE_SUBMIT_MODE,
) {
  void mode;
  return "/api/web/reading-record/submit";
}

export function readPageSubmitRequestBody(
  input: ReadPageSubmitPayloadInput,
  mode: ReadPageSubmitMode = READ_PAGE_SUBMIT_MODE,
) {
  void mode;
  void input.readingGoal;
  void input.readingVariant;
  return { plainText: input.text };
}
