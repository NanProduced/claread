/**
 * Ask thread / model-option summary projections (pure).
 *
 * The panel keeps thread list and model-option state; these helpers are the
 * wire→view mappings shared by init, load, send, and reset flows.
 */
import type {
  ReaderAskModelOptionSummaryDto,
  ReaderAskSelectedModelDto,
  ReaderAskThreadDetailDto,
  ReaderAskThreadSummaryDto,
} from "@/types/api/reader-ask";

export function toThreadSummary(
  detail: ReaderAskThreadDetailDto,
): ReaderAskThreadSummaryDto {
  return {
    id: detail.id,
    record_id: detail.record_id,
    title: detail.title,
    is_default: detail.is_default,
    selected_model: detail.selected_model ?? null,
    archived_at: detail.archived_at ?? null,
    created_at: detail.created_at,
    updated_at: detail.updated_at,
    last_message_at: detail.last_message_at,
  };
}

export function replaceThreadSummary(
  threads: ReaderAskThreadSummaryDto[],
  nextThread: ReaderAskThreadSummaryDto,
): ReaderAskThreadSummaryDto[] {
  const index = threads.findIndex((thread) => thread.id === nextThread.id);
  if (index < 0) {
    return [nextThread, ...threads];
  }
  return threads.map((thread) => (thread.id === nextThread.id ? nextThread : thread));
}

export function isKnownModelOptionKey(
  items: ReaderAskModelOptionSummaryDto[],
  key: string | null | undefined,
): key is string {
  return Boolean(key && items.some((item) => item.key === key));
}

export function findModelOptionSummary(
  items: ReaderAskModelOptionSummaryDto[],
  key: string | null | undefined,
): ReaderAskModelOptionSummaryDto | null {
  if (!key) {
    return null;
  }
  return items.find((item) => item.key === key) ?? null;
}

export function toSelectedModelSummary(
  option: ReaderAskModelOptionSummaryDto | null | undefined,
): ReaderAskSelectedModelDto | null {
  if (!option) {
    return null;
  }
  return {
    key: option.key,
    label: option.label,
    description: option.description ?? null,
    model_name: option.model_name ?? null,
    replan_model_name: option.replan_model_name ?? null,
    price_multiplier: option.price_multiplier,
  };
}
