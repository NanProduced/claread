import type {
  ReaderRecordReadingGoal,
  ReaderRecordReadingVariant,
} from "@/lib/reading-defaults";
import type { ReaderInputAdapterSourceTypeDto } from "@/types/api/reader-plate";

export type ReadPageSubmitMode = "reader-plate-input";

export const READ_PAGE_SUBMIT_MODE: ReadPageSubmitMode = "reader-plate-input";

export interface ReadPageUnifiedSubmitPayloadInput {
  text: string;
  sourceType?: ReaderInputAdapterSourceTypeDto;
  markdownMode?: boolean;
  filename?: string;
  readingGoal: ReaderRecordReadingGoal;
  readingVariant: ReaderRecordReadingVariant;
}

export interface ReadPageSubmitRequestBody {
  text: string;
  sourceType: ReaderInputAdapterSourceTypeDto;
  filename: string | null;
  reading_goal: ReaderRecordReadingGoal;
  reading_variant: ReaderRecordReadingVariant;
}

export function readPageSubmitEndpoint(
  _mode: ReadPageSubmitMode = READ_PAGE_SUBMIT_MODE,
): string {
  return "/api/web/reader-plate/input";
}

export function readPageSubmitRequestBody(
  input: ReadPageUnifiedSubmitPayloadInput,
  _mode: ReadPageSubmitMode = READ_PAGE_SUBMIT_MODE,
): ReadPageSubmitRequestBody {
  const sourceType: ReaderInputAdapterSourceTypeDto = input.markdownMode
    ? "markdown_file"
    : input.sourceType ?? "pasted_text";

  return {
    text: input.text,
    sourceType,
    filename: input.filename ?? null,
    reading_goal: input.readingGoal,
    reading_variant: input.readingVariant,
  };
}
