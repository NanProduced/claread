import { memo } from 'react'
import { ScrollView, Text, View } from '@tarojs/components'
import LucideIcon from '../LucideIcon'
import type { ReaderNoteDto } from '../../services/api/reader-notes.client'
import './index.scss'

interface ReaderNoteSheetProps {
  visible: boolean
  sentenceText?: string
  notes: ReaderNoteDto[]
  activeNoteId?: string | null
  onClose: () => void
  onSelectNote: (note: ReaderNoteDto) => void
}

function shouldHideQuote(note: ReaderNoteDto): boolean {
  return note.quote_mode === 'sentence' && note.sentence_id === note.anchor_sentence_id
}

const ReaderNoteSheet = memo(function ReaderNoteSheet({
  visible,
  sentenceText,
  notes,
  activeNoteId,
  onClose,
  onSelectNote,
}: ReaderNoteSheetProps) {
  if (!visible) return null

  return (
    <View className='reader-note-sheet__overlay' onClick={onClose}>
      <View className='reader-note-sheet' onClick={e => e.stopPropagation()}>
        <View className='reader-note-sheet__handle' />

        <View className='reader-note-sheet__header'>
          <View>
            <Text className='reader-note-sheet__title'>用户笔记</Text>
            {!!sentenceText && (
              <Text className='reader-note-sheet__subtitle' numberOfLines={2}>
                {sentenceText}
              </Text>
            )}
          </View>
          <View className='reader-note-sheet__count'>
            <LucideIcon name='sticky-note' size={16} color='currentColor' />
            <Text>{notes.length}</Text>
          </View>
        </View>

        <ScrollView className='reader-note-sheet__list' scrollY enhanced showScrollbar={false}>
          {notes.map((note) => {
            const isActive = note.id === activeNoteId
            return (
              <View
                key={note.id}
                className={`reader-note-card ${isActive ? 'is-active' : ''}`}
                onClick={() => onSelectNote(note)}
              >
                {!shouldHideQuote(note) && (
                  <View className='reader-note-card__quote'>
                    <Text className='reader-note-card__quote-text' numberOfLines={3}>
                      {note.selected_text}
                    </Text>
                  </View>
                )}
                <Text className='reader-note-card__body'>{note.note_text}</Text>
              </View>
            )
          })}

          {notes.length === 0 && (
            <View className='reader-note-sheet__empty'>
              <Text className='reader-note-sheet__empty-text'>当前句子还没有用户笔记</Text>
            </View>
          )}
        </ScrollView>
      </View>
    </View>
  )
})

export default ReaderNoteSheet
