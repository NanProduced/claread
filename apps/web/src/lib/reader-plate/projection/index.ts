export { renderSceneToPlateDocument } from "./render-scene-to-plate-document";
export {
  anchorDraftForSelectionSegment,
  anchorDraftsForSelection,
  type ReaderRecordAnchorDraft,
  type ReaderRecordAnchorScope,
} from "./reader-record-anchor-draft";
export {
  READER_RECORD_PLATE_DOCUMENT_SCHEMA_VERSION,
  projectReaderPlateSnapshotToReaderRecordPlateDocument,
  type ReaderRecordPlateBlock,
  type ReaderRecordPlateBlockquoteBlock,
  type ReaderRecordPlateCalloutBlock,
  type ReaderRecordPlateCalloutVariant,
  type ReaderRecordPlateDocument,
  type ReaderRecordPlateDocumentSchemaVersion,
  type ReaderRecordPlateGrammarMark,
  type ReaderRecordPlateMark,
  type ReaderRecordPlateParagraphBlock,
  type ReaderRecordPlateProgress,
  type ReaderRecordPlateProgressLayer,
  type ReaderRecordPlateRange,
  type ReaderRecordPlateTextAnchor,
  type ReaderRecordPlateTextLeaf,
  type ReaderRecordPlateTranslationTextLeaf,
  type ReaderRecordPlateUserHighlightMark,
  type ReaderRecordPlateUserNoteMark,
  type ReaderRecordPlateVocabularyMark,
} from "./reader-record-plate-document";
export {
  userEditorialAssetAnchorDraftForActiveAnchor,
  type ReaderRecordActiveAnchorInput,
  type ReaderRecordActiveAnchorSource,
  type UserEditorialAssetAnchorDraft,
} from "./reader-record-active-anchor";
