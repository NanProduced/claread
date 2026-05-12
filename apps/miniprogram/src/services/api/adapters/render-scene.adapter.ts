/**
 * RenderScene Adapter
 *
 * 唯一转换点: AnalyzeResponseDto (snake_case) -> RenderSceneVm (camelCase)
 *
 * 约束:
 * - 只做字段映射 + 轻量结构适配
 * - 禁止在此文件引入业务逻辑
 * - 禁止在其他任何位置做字段转换
 */

import type {
  AnalyzeResponseDto,
  AcademicAnalyzeResponseDto,
  AnyAnalyzeResponseDto,
  InlineMark as DtoInlineMark,
  AcademicInlineMark as DtoAcademicInlineMark,
  TextAnchor as DtoTextAnchor,
  MultiTextAnchor as DtoMultiTextAnchor,
  SpanRefPart,
  ArticleSentence,
  ArticleParagraph,
  ArticleStructure,
  TranslationItem,
  SentenceEntry,
  AcademicSentenceEntry,
  Warning,
  AnalyzeRequestMeta,
  ContentSummaryDto,
  ACADEMIC_SCHEMA_VERSION,
} from '@/types/api/analyze-response.dto'

import { ACADEMIC_SCHEMA_VERSION as ACADEMIC_VERSION } from '@/types/api/analyze-response.dto'

import type {
  RenderSceneVmBase,
  AcademicRenderSceneVm,
  AnyRenderSceneVm,
  InlineMarkModel,
  AcademicInlineMarkModel,
  AcademicInlineGlossary as VmAcademicInlineGlossary,
  TextAnchor as VmTextAnchor,
  MultiTextAnchor as VmMultiTextAnchor,
  SpanRef,
  SentenceModel,
  ParagraphModel,
  ArticleModel,
  TranslationModel,
  SentenceEntryModel,
  AcademicSentenceEntryModel,
  ContentSummaryModel,
  WarningModel,
  RequestMeta,
} from '@/types/view/render-scene.vm'

// ============ 共享转换函数 ============

function transformAnchor(dtoAnchor: DtoTextAnchor | DtoMultiTextAnchor): VmTextAnchor | VmMultiTextAnchor {
  if (dtoAnchor.kind === 'text') {
    const a = dtoAnchor as DtoTextAnchor
    return {
      kind: 'text',
      sentenceId: a.sentence_id,
      anchorText: a.anchor_text,
      occurrence: a.occurrence,
    }
  } else {
    const a = dtoAnchor as DtoMultiTextAnchor
    return {
      kind: 'multi_text',
      sentenceId: a.sentence_id,
      parts: a.parts.map((p: SpanRefPart) => ({
        anchorText: p.anchor_text,
        occurrence: p.occurrence,
        role: p.role,
      })),
    }
  }
}

function transformTranslation(item: TranslationItem): TranslationModel {
  return {
    sentenceId: item.sentence_id,
    translationZh: item.translation_zh,
  }
}

function transformSentence(sentence: ArticleSentence): SentenceModel {
  return {
    sentenceId: sentence.sentence_id,
    paragraphId: sentence.paragraph_id,
    text: sentence.text,
  }
}

function transformParagraph(paragraph: ArticleParagraph): ParagraphModel {
  return {
    paragraphId: paragraph.paragraph_id,
    sentenceIds: paragraph.sentence_ids,
  }
}

function transformArticle(article: ArticleStructure): ArticleModel {
  return {
    paragraphs: (article.paragraphs ?? []).map(transformParagraph),
    sentences: (article.sentences ?? []).map(transformSentence),
  }
}

function transformWarning(warning: Warning): WarningModel {
  return {
    code: warning.code,
    level: warning.level,
    message: warning.message,
    sentenceId: warning.sentence_id,
    annotationId: warning.annotation_id,
  }
}

function transformRequestMeta(meta: AnalyzeRequestMeta): RequestMeta {
  return {
    requestId: meta.request_id,
    sourceType: meta.source_type as 'user_input',
    readingGoal: meta.reading_goal,
    readingVariant: meta.reading_variant,
    profileId: meta.profile_id,
  }
}

// ============ Learning 模式转换 ============

function transformInlineMark(mark: DtoInlineMark): InlineMarkModel {
  return {
    id: mark.id,
    annotationType: mark.annotation_type,
    anchor: transformAnchor(mark.anchor),
    renderType: mark.render_type,
    visualTone: mark.visual_tone,
    clickable: mark.clickable,
    lookupText: mark.lookup_text,
    lookupKind: mark.lookup_kind,
        glossary: mark.glossary
      ? {
          zh: mark.glossary.zh,
          gloss: mark.glossary.gloss,
          reason: mark.glossary.reason,
          phraseType: mark.glossary.phrase_type,
        }
      : undefined,
  }
}

function transformSentenceEntry(entry: SentenceEntry): SentenceEntryModel {
  return {
    id: entry.id,
    sentenceId: entry.sentence_id,
    entryType: entry.entry_type,
    label: entry.label,
    title: entry.title,
    content: entry.content,
  }
}

function transformLearningDto(dto: AnalyzeResponseDto): RenderSceneVmBase {
  return {
    schemaVersion: dto.schema_version as RenderSceneVmBase['schemaVersion'],
    request: transformRequestMeta(dto.request),
    article: transformArticle(dto.article),
    userFacingState: dto.user_facing_state,
    translations: (dto.translations ?? []).map(transformTranslation),
    inlineMarks: (dto.inline_marks ?? []).map(transformInlineMark),
    sentenceEntries: (dto.sentence_entries ?? []).map(transformSentenceEntry),
    warnings: (dto.warnings ?? []).map(transformWarning),
  }
}

// ============ Academic 模式转换 ============

function transformAcademicInlineMark(mark: DtoAcademicInlineMark): AcademicInlineMarkModel {
  return {
    id: mark.id,
    annotationType: mark.annotation_type,
    anchor: transformAnchor(mark.anchor),
    renderType: mark.render_type,
    visualTone: mark.visual_tone,
    clickable: mark.clickable,
    lookupText: mark.lookup_text,
    glossary: mark.glossary
      ? {
          zh: mark.glossary.zh,
          zhUncertain: mark.glossary.zh_uncertain,
          contextDefinition: mark.glossary.context_definition,
          termCategory: mark.glossary.term_category,
          logicType: mark.glossary.logic_type,
          hedgingDetected: mark.glossary.hedging_detected,
          hedgingWords: mark.glossary.hedging_words,
        }
      : undefined,
  }
}

function transformAcademicSentenceEntry(entry: AcademicSentenceEntry): AcademicSentenceEntryModel {
  return {
    id: entry.id,
    sentenceId: entry.sentence_id,
    entryType: entry.entry_type,
    label: entry.label,
    title: entry.title,
    content: entry.content,
  }
}

function transformContentSummary(dto: ContentSummaryDto): ContentSummaryModel {
  return {
    completeness: dto.completeness,
    overview: dto.overview,
    researchQuestion: dto.research_question,
    methodology: dto.methodology,
    keyFindings: dto.key_findings,
    limitations: dto.limitations,
  }
}

function transformAcademicDto(dto: AcademicAnalyzeResponseDto): AcademicRenderSceneVm {
  return {
    schemaVersion: '3.0.0-academic',
    request: transformRequestMeta(dto.request),
    article: transformArticle(dto.article),
    userFacingState: dto.user_facing_state,
    translations: (dto.translations ?? []).map(transformTranslation),
    inlineMarks: (dto.inline_marks ?? []).map(transformAcademicInlineMark),
    sentenceEntries: (dto.sentence_entries ?? []).map(transformAcademicSentenceEntry),
    contentSummary: dto.content_summary ? transformContentSummary(dto.content_summary) : null,
    title: dto.title,
    warnings: (dto.warnings ?? []).map(transformWarning),
  }
}

// ============ 统一入口 ============

export function analyzeResponseDtoToVm(dto: AnyAnalyzeResponseDto): AnyRenderSceneVm {
  if (dto.schema_version === ACADEMIC_VERSION) {
    return transformAcademicDto(dto as AcademicAnalyzeResponseDto)
  }
  return transformLearningDto(dto as AnalyzeResponseDto)
}

// ============ 反向转换：共享函数 ============

function reverseAnchor(vmAnchor: VmTextAnchor | VmMultiTextAnchor): DtoTextAnchor | DtoMultiTextAnchor {
  if (vmAnchor.kind === 'text') {
    const a = vmAnchor as VmTextAnchor
    return {
      kind: 'text',
      sentence_id: a.sentenceId,
      anchor_text: a.anchorText,
      occurrence: a.occurrence,
    }
  } else {
    const a = vmAnchor as VmMultiTextAnchor
    return {
      kind: 'multi_text',
      sentence_id: a.sentenceId,
      parts: a.parts.map((p: SpanRef) => ({
        anchor_text: p.anchorText,
        occurrence: p.occurrence,
        role: p.role,
      })),
    }
  }
}

function reverseTranslation(item: TranslationModel): TranslationItem {
  return {
    sentence_id: item.sentenceId,
    translation_zh: item.translationZh,
  }
}

function reverseSentence(sentence: SentenceModel): ArticleSentence {
  return {
    sentence_id: sentence.sentenceId,
    paragraph_id: sentence.paragraphId,
    text: sentence.text,
    sentence_span: { start: 0, end: 0 },
  }
}

function reverseParagraph(paragraph: ParagraphModel): ArticleParagraph {
  return {
    paragraph_id: paragraph.paragraphId,
    text: '',
    render_span: { start: 0, end: 0 },
    sentence_ids: paragraph.sentenceIds,
  }
}

function reverseWarning(warning: WarningModel): Warning {
  return {
    code: warning.code,
    level: warning.level,
    message: warning.message,
    sentence_id: warning.sentenceId,
    annotation_id: warning.annotationId,
  }
}

function reverseRequestMeta(meta: RequestMeta): AnalyzeRequestMeta {
  return {
    request_id: meta.requestId,
    source_type: meta.sourceType,
    reading_goal: meta.readingGoal as AnalyzeRequestMeta['reading_goal'],
    reading_variant: meta.readingVariant as AnalyzeRequestMeta['reading_variant'],
    profile_id: meta.profileId,
  }
}

// ============ 反向转换：Learning 模式 ============

function reverseInlineMark(mark: InlineMarkModel): DtoInlineMark {
  return {
    id: mark.id,
    annotation_type: mark.annotationType,
    anchor: reverseAnchor(mark.anchor),
    render_type: mark.renderType,
    visual_tone: mark.visualTone,
    clickable: mark.clickable,
    lookup_text: mark.lookupText,
    lookup_kind: mark.lookupKind as 'word' | 'phrase' | undefined,
    glossary: mark.glossary
      ? {
          zh: mark.glossary.zh,
          gloss: mark.glossary.gloss,
          reason: mark.glossary.reason,
          phrase_type: mark.glossary.phraseType,
        }
      : undefined,
  }
}

function reverseSentenceEntry(entry: SentenceEntryModel): SentenceEntry {
  return {
    id: entry.id,
    sentence_id: entry.sentenceId,
    entry_type: entry.entryType,
    label: entry.label,
    title: entry.title,
    content: entry.content,
  }
}

function reverseLearningVm(vm: RenderSceneVmBase): AnalyzeResponseDto {
  return {
    schema_version: vm.schemaVersion,
    request: reverseRequestMeta(vm.request),
    article: {
      source_type: vm.request.sourceType,
      source_text: '',
      render_text: '',
      paragraphs: (vm.article.paragraphs ?? []).map(reverseParagraph),
      sentences: (vm.article.sentences ?? []).map(reverseSentence),
    },
    user_facing_state: vm.userFacingState,
    translations: (vm.translations ?? []).map(reverseTranslation),
    inline_marks: (vm.inlineMarks ?? []).map(reverseInlineMark),
    sentence_entries: (vm.sentenceEntries ?? []).map(reverseSentenceEntry),
    warnings: (vm.warnings ?? []).map(reverseWarning),
  }
}

// ============ 反向转换：Academic 模式 ============

function reverseAcademicInlineMark(mark: AcademicInlineMarkModel): DtoAcademicInlineMark {
  return {
    id: mark.id,
    annotation_type: mark.annotationType,
    anchor: reverseAnchor(mark.anchor),
    render_type: mark.renderType,
    visual_tone: mark.visualTone,
    clickable: mark.clickable,
    lookup_text: mark.lookupText,
    glossary: mark.glossary
      ? {
          zh: mark.glossary.zh,
          zh_uncertain: mark.glossary.zhUncertain,
          context_definition: mark.glossary.contextDefinition,
          term_category: mark.glossary.termCategory,
          logic_type: mark.glossary.logicType,
          hedging_detected: mark.glossary.hedgingDetected,
          hedging_words: mark.glossary.hedgingWords,
        }
      : undefined,
  }
}

function reverseAcademicSentenceEntry(entry: AcademicSentenceEntryModel): AcademicSentenceEntry {
  return {
    id: entry.id,
    sentence_id: entry.sentenceId,
    entry_type: entry.entryType,
    label: entry.label,
    title: entry.title,
    content: entry.content,
  }
}

function reverseContentSummary(model: ContentSummaryModel): ContentSummaryDto {
  return {
    completeness: model.completeness,
    overview: model.overview,
    research_question: model.researchQuestion,
    methodology: model.methodology,
    key_findings: model.keyFindings,
    limitations: model.limitations,
  }
}

function reverseAcademicVm(vm: AcademicRenderSceneVm): AcademicAnalyzeResponseDto {
  return {
    schema_version: '3.0.0-academic',
    request: reverseRequestMeta(vm.request),
    article: {
      source_type: vm.request.sourceType,
      source_text: '',
      render_text: '',
      paragraphs: (vm.article.paragraphs ?? []).map(reverseParagraph),
      sentences: (vm.article.sentences ?? []).map(reverseSentence),
    },
    user_facing_state: vm.userFacingState,
    translations: (vm.translations ?? []).map(reverseTranslation),
    inline_marks: (vm.inlineMarks ?? []).map(reverseAcademicInlineMark),
    sentence_entries: (vm.sentenceEntries ?? []).map(reverseAcademicSentenceEntry),
    content_summary: vm.contentSummary ? reverseContentSummary(vm.contentSummary) : null,
    title: vm.title,
    warnings: (vm.warnings ?? []).map(reverseWarning),
  }
}

// ============ 反向转换：统一入口 ============

export function vmToAnalyzeResponseDto(vm: AnyRenderSceneVm): AnyAnalyzeResponseDto {
  if (vm.schemaVersion === '3.0.0-academic') {
    return reverseAcademicVm(vm as AcademicRenderSceneVm)
  }
  return reverseLearningVm(vm as RenderSceneVmBase)
}
