import { describe, expect, it } from 'vitest';
import {
  readingRecordStatusKey,
  readingRecordStatusLabel,
  shouldShowStatusLine,
} from './reader-record-status';

describe('readingRecordStatusKey', () => {
  it('readable_enhancing + coverage_complete → completed', () => {
    expect(readingRecordStatusKey('readable_enhancing', 'coverage_complete')).toBe('completed');
  });
  it('readable_enhancing + others → ready_to_read', () => {
    expect(readingRecordStatusKey('readable_enhancing', 'article_ready')).toBe('ready_to_read');
    expect(readingRecordStatusKey('readable_enhancing', 'initial_enhancement_ready')).toBe('ready_to_read');
    expect(readingRecordStatusKey('readable_enhancing', 'submitted')).toBe('ready_to_read');
  });
  it('processing / needs_confirmation / action_required / failed 各自映射', () => {
    expect(readingRecordStatusKey('processing', 'submitted')).toBe('processing');
    expect(readingRecordStatusKey('needs_confirmation', 'candidate_base_ready')).toBe('needs_confirmation');
    expect(readingRecordStatusKey('action_required', 'article_ready')).toBe('awaiting_continue');
    expect(readingRecordStatusKey('failed', 'article_ready')).toBe('failed');
  });
  it('deleted → failed', () => {
    expect(readingRecordStatusKey('deleted', 'article_ready')).toBe('failed');
  });
});

describe('readingRecordStatusLabel', () => {
  it.each([
    ['processing', '解析中'],
    ['needs_confirmation', '需要确认'],
    ['ready_to_read', '可以开始阅读'],
    ['awaiting_continue', '等待继续'],
    ['failed', '解析遇到问题'],
    ['completed', '解析完成'],
  ] as const)('%s → %s', (key, expected) => {
    expect(readingRecordStatusLabel(key)).toBe(expected);
  });
});

describe('shouldShowStatusLine', () => {
  it('only priority states show a status line', () => {
    expect(shouldShowStatusLine('processing')).toBe(true);
    expect(shouldShowStatusLine('needs_confirmation')).toBe(true);
    expect(shouldShowStatusLine('awaiting_continue')).toBe(true);
    expect(shouldShowStatusLine('failed')).toBe(true);
    expect(shouldShowStatusLine('ready_to_read')).toBe(false);
    expect(shouldShowStatusLine('completed')).toBe(false);
    expect(shouldShowStatusLine('reading_enhancing')).toBe(false);
  });
});
