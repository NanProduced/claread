import type {
  ReaderRecordReadingGoal,
  ReaderRecordReadingVariant,
} from "@/lib/reading-defaults";

export type ReadPageSubmitMode = "reading-record";

export const READ_PAGE_SUBMIT_MODE: ReadPageSubmitMode = "reading-record";

export interface ReadPageSubmitPayloadInput {
  text: string;
  readingGoal: ReaderRecordReadingGoal;
  readingVariant: ReaderRecordReadingVariant;
}

export interface ReadPageSubmitRequestBody {
  plainText: string;
  reading_goal: ReaderRecordReadingGoal;
  reading_variant: ReaderRecordReadingVariant;
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
): ReadPageSubmitRequestBody {
  void mode;
  return {
    plainText: input.text,
    reading_goal: input.readingGoal,
    reading_variant: input.readingVariant,
  };
}
