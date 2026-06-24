export { renderSceneToPlateDocument } from "./render-scene-to-plate-document";
export {
  adaptReaderPlateSnapshotToPlateDocument,
  adaptReaderPlateSnapshotToReaderVm,
} from "./snapshot-to-reader-workbench";
export {
  anchorDraftForSelectionSegment,
  anchorDraftsForSelection,
  type ReaderRecordAnchorDraft,
  type ReaderRecordAnchorScope,
} from "./reader-record-anchor-draft";
export {
  READER_RECORD_PLATE_DOCUMENT_SCHEMA_VERSION,
  projectReaderPlateSnapshotToReaderRecordPlateDocument,
  type ReaderRecordPlateAnchorSegmentNode,
  type ReaderRecordPlateCue,
  type ReaderRecordPlateDocument,
  type ReaderRecordPlateDocumentSchemaVersion,
  type ReaderRecordPlateGrammarCue,
  type ReaderRecordPlateGrammarMark,
  type ReaderRecordPlateMark,
  type ReaderRecordPlateProgress,
  type ReaderRecordPlateProgressLayer,
  type ReaderRecordPlateSentenceAnalysisCue,
  type ReaderRecordPlateSourceBlockNode,
  type ReaderRecordPlateTextAnchor,
  type ReaderRecordPlateTextLeaf,
  type ReaderRecordPlateTranslationBlockNode,
  type ReaderRecordPlateUnitNode,
  type ReaderRecordPlateVocabularyMark,
} from "./reader-record-plate-document";
export {
  userEditorialAssetAnchorDraftForActiveAnchor,
  type ReaderRecordActiveAnchorInput,
  type ReaderRecordActiveAnchorSource,
  type UserEditorialAssetAnchorDraft,
} from "./reader-record-active-anchor";
