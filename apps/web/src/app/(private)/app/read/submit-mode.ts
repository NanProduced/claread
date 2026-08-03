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
  return "/api/web/reader/records/input";
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

/**
 * Heuristic Markdown marker detection (M2 minimal increment).
 *
 * Used ONLY to surface a user-facing hint ("将作为 Markdown 解析") on the
 * paste textarea. This does NOT change the submit `sourceType` by itself —
 * the BFF still receives `pasted_text` unless the user explicitly uploads a
 * `.md` file or toggles markdown mode. The real parsing decision is owned
 * by the backend parser adapter (M1) per the G0 Structured Source Contract.
 *
 * Detection rules (any one match → true):
 *   - Line starting with 1–6 `#` followed by whitespace (ATX heading)
 *   - Fenced code block delimiter line (``` or ~~~)
 *   - GFM table row (line containing `|` with at least one cell separator)
 *   - Line starting with `-`, `*`, or `+` followed by whitespace (unordered list item)
 *   - Line starting with a number followed by `.` or `)` and whitespace (ordered list item)
 *   - Line starting with `>` (blockquote)
 *   - Markdown link syntax `[text](href)` anywhere in the text
 *
 * False positives (e.g. plain `-` dashes in prose) are acceptable because
 * the hint is non-blocking and only suggests Markdown parsing.
 */
export function detectMarkdownMarkers(text: string): boolean {
  if (typeof text !== "string" || text.length === 0) {
    return false;
  }

  const lines = text.split(/\r?\n/);
  for (const rawLine of lines) {
    const line = rawLine.trimStart();
    if (line.length === 0) continue;

    // ATX heading: 1–6 `#` followed by whitespace or end of line
    if (/^#{1,6}(?:\s|$)/.test(line)) {
      return true;
    }

    // Fenced code block delimiter: ``` or ~~~ (3+ same chars)
    if (/^(`{3,}|~{3,})/.test(line)) {
      return true;
    }

    // GFM table row: line containing `|` with at least one non-pipe cell char
    // and a `|` separator. Avoid matching a lone `|` in prose by requiring
    // either `|...|` or `| ` at start.
    if (/^\|.*\|/.test(line) || /^[^|\n]+\|[^|\n]+/.test(line)) {
      // Refine: only count as table if the line has at least 2 pipe chars OR
      // matches the strict GFM row shape `| cell | cell |`.
      if ((line.match(/\|/g)?.length ?? 0) >= 2) {
        return true;
      }
    }

    // Unordered list item: `-`, `*`, or `+` followed by whitespace
    if (/^[-*+]\s/.test(line)) {
      return true;
    }

    // Ordered list item: `1.` or `1)` followed by whitespace
    if (/^\d+[.)]\s/.test(line)) {
      return true;
    }

    // Blockquote: `>` followed by whitespace or end of line
    if (/^>(?:\s|$)/.test(line)) {
      return true;
    }
  }

  // Markdown link syntax anywhere in text
  if (/\[[^\]\n]+\]\([^)\s]+\)/.test(text)) {
    return true;
  }

  return false;
}
