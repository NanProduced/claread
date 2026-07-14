import type {
  ReadingRecordProductState,
  ReadingRecordReadinessState,
} from '@/types/api/reading-records';

export type ReadingRecordStatusKey =
  | 'processing'
  | 'needs_confirmation'
  | 'ready_to_read'
  | 'reading_enhancing'
  | 'awaiting_continue'
  | 'failed'
  | 'completed';

const _READY_TO_READ_STATUSES: ReadonlySet<ReadingRecordReadinessState> = new Set([
  'article_ready',
  'initial_enhancement_ready',
  'submitted',
]);

export function readingRecordStatusKey(
  productState: ReadingRecordProductState,
  readinessState: ReadingRecordReadinessState,
): ReadingRecordStatusKey {
  if (productState === 'readable_enhancing') {
    if (readinessState === 'coverage_complete') return 'completed';
    if (_READY_TO_READ_STATUSES.has(readinessState)) return 'ready_to_read';
  }
  switch (productState) {
    case 'processing':
      return 'processing';
    case 'needs_confirmation':
      return 'needs_confirmation';
    case 'readable_enhancing':
      return 'ready_to_read';
    case 'action_required':
      return 'awaiting_continue';
    case 'failed':
      return 'failed';
    case 'deleted':
      return 'failed';
    default:
      return 'ready_to_read';
  }
}

const _LABEL_BY_KEY: Record<ReadingRecordStatusKey, string> = {
  processing: '解析中',
  needs_confirmation: '需要确认',
  ready_to_read: '可以开始阅读',
  reading_enhancing: '解析中',
  awaiting_continue: '等待继续',
  failed: '解析遇到问题',
  completed: '解析完成',
};

export function readingRecordStatusLabel(key: ReadingRecordStatusKey): string {
  return _LABEL_BY_KEY[key];
}

const _SHOW_STATUS_LINE: ReadonlySet<ReadingRecordStatusKey> = new Set([
  'processing',
  'needs_confirmation',
  'awaiting_continue',
  'failed',
]);

export function shouldShowStatusLine(key: ReadingRecordStatusKey): boolean {
  return _SHOW_STATUS_LINE.has(key);
}
