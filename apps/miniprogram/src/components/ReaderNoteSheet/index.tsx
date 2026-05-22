import { memo } from 'react'
import { ScrollView, Text, Textarea, View } from '@tarojs/components'
import LucideIcon from '../LucideIcon'
import type { ReaderNoteDto } from '../../services/api/reader-notes.client'
import './index.scss'

interface ReaderNoteSheetProps {
  visible: boolean
  notes: ReaderNoteDto[]
  mode: 'preview' | 'compose'
  activeNote?: ReaderNoteDto | null
  activeNoteId?: string | null
  draftText: string
  submitLabel: string
  onClose: () => void
  onSelectNote: (note: ReaderNoteDto) => void
  onDraftChange: (value: string) => void
  onSubmitDraft: () => void
  onOpenActions?: () => void
}

const ReaderNoteSheet = memo(function ReaderNoteSheet({
  visible,
  notes,
  mode,
  activeNote,
  activeNoteId,
  draftText,
  submitLabel,
  onClose,
  onSelectNote,
  onDraftChange,
  onSubmitDraft,
  onOpenActions,
}: ReaderNoteSheetProps) {
  if (!visible) return null

  return (
    <View className='reader-note-sheet__overlay' onClick={onClose}>
      <View className='reader-note-sheet' onClick={e => e.stopPropagation()}>
        <View className='reader-note-sheet__handle' />

        <View className='reader-note-sheet__header'>
          <Text className='reader-note-sheet__title'>用户笔记</Text>
          {mode === 'preview' && activeNote && onOpenActions ? (
            <View className='reader-note-sheet__header-action' onClick={onOpenActions}>
              <Text className='reader-note-sheet__header-action-text'>操作</Text>
            </View>
          ) : null}
        </View>

        <ScrollView className='reader-note-sheet__list' scrollY enhanced showScrollbar={false}>
          {mode === 'compose' ? (
            <View className='reader-note-sheet__composer'>
              <Textarea
                className='reader-note-sheet__composer-input'
                value={draftText}
                maxlength={500}
                placeholder='写下你的想法'
                cursorSpacing={120}
                onInput={event => onDraftChange(event.detail.value)}
              />
              <View className='reader-note-sheet__composer-footer'>
                <Text className='reader-note-sheet__composer-count'>{draftText.trim().length}/500</Text>
                <View className='reader-note-sheet__composer-submit' onClick={onSubmitDraft}>
                  <Text>{submitLabel}</Text>
                </View>
              </View>
            </View>
          ) : activeNote ? (
            <View className='reader-note-sheet__preview'>
              <View className='reader-note-sheet__preview-meta'>
                <LucideIcon name='sticky-note' size={16} color='currentColor' />
                <Text>{notes.length > 1 ? `共 ${notes.length} 条笔记` : '当前笔记'}</Text>
              </View>
              <Text className='reader-note-sheet__preview-body'>{activeNote.note_text}</Text>
            </View>
          ) : null}

          {mode === 'preview' && notes.length > 1 ? (
            <View className='reader-note-sheet__stack'>
              {notes.map((note) => {
                const isActive = note.id === activeNoteId
                return (
                  <View
                    key={note.id}
                    className={`reader-note-card ${isActive ? 'is-active' : ''}`}
                    onClick={() => onSelectNote(note)}
                  >
                    <Text className='reader-note-card__body'>{note.note_text}</Text>
                  </View>
                )
              })}
            </View>
          ) : null}

          {mode === 'preview' && !activeNote ? (
            <View className='reader-note-sheet__empty'>
              <Text className='reader-note-sheet__empty-text'>当前还没有可预览的笔记</Text>
            </View>
          ) : null}
        </ScrollView>
      </View>
    </View>
  )
})

export default ReaderNoteSheet
