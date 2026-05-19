import type { ReactNode } from "react";
import type { WebDictCandidate, WebDictDisambiguationResult, WebDictEntry, WebDictResult } from "@/types/api/dict";
import type {
  DictionaryAIViewState,
  DictAISourceDto,
  WebDictAIErrorResult,
  WebDictAIRequest,
  WebDictAIResult,
} from "@/types/api/dict-ai";
import type { InlineGlossary } from "@/types/view/ReaderMockVm";
import type { DictionaryLookupSnapshot } from "./contracts";
import { firstMeaning } from "./contracts";

export type DictionaryContentTab = "meanings" | "examples" | "phrases" | "forms";

export type DictionarySenseExample = {
  key: string;
  example: string;
  exampleTranslation?: string;
};

export type DictionarySenseItem = {
  key: string;
  number: number;
  partOfSpeech: string;
  meaning: string;
  examples: DictionarySenseExample[];
};

export type DictionaryExampleGroup = {
  key: string;
  number?: number;
  partOfSpeech?: string;
  meaning: string;
  examples: DictionarySenseExample[];
  supplemental?: boolean;
};

export type DictionaryCandidateGroup = {
  key: string;
  label: string;
  hint: string;
  candidates: WebDictCandidate[];
};

export type DictionaryRenderableEntry = Pick<
  WebDictEntry,
  "word" | "baseWord" | "phonetic" | "meanings" | "examples" | "phrases" | "entryKind" | "exchange" | "tags"
> & {
  id?: number;
  homographNo?: number;
};

const phraseTypeLabel: Record<NonNullable<InlineGlossary["phraseType"]>, string> = {
  collocation: "固定搭配",
  phrasal_verb: "动词短语",
  idiom: "习语",
  proper_noun: "专名",
  compound: "复合表达",
};

const dictionaryAIConfidenceLabelMap: Record<NonNullable<WebDictAIResult["confidence"]>, string> = {
  high: "高置信",
  medium: "中置信",
  low: "低置信",
};

const dictionaryAIClassificationLabelMap = {
  valid_word: "可识别词条",
  slang_or_informal: "俚语/口语",
  proper_noun: "专有名词",
  domain_term: "领域术语",
  variant_or_inflection: "词形/变体",
  possible_typo_or_ocr: "拼写或 OCR 偏差",
  unrecognized_noise: "噪声串",
} satisfies Record<Extract<WebDictAIResult, { mode: "missing_fallback" }>["classification"], string>;

const dictionaryTagLabelMap: Record<string, string> = {
  cet: "大学四六级",
  cet4: "大学四级",
  cet6: "大学六级",
  gaokao: "高考",
  gmat: "GMAT",
  gre: "GRE",
  ielts: "雅思",
  ielts_toefl: "雅思托福",
  kaoyan: "考研",
  sat: "SAT",
  tem: "专业英语",
  tem4: "专业英语",
  tem8: "专业英语",
  toefl: "托福",
};

export function contextualGlossaryTitle(lookup: DictionaryLookupSnapshot) {
  if (lookup.annotationType === "phrase_gloss" || lookup.lookupType === "phrase") {
    return lookup.glossary?.phraseType ? phraseTypeLabel[lookup.glossary.phraseType] : "短语含义";
  }
  if (lookup.annotationType === "context_gloss") {
    return "本文含义";
  }
  return null;
}

export function contextualGlossaryText(glossary?: InlineGlossary) {
  return glossary?.zh ?? glossary?.gloss ?? "";
}

export function normalizeDictionaryText(value?: string) {
  return (value ?? "").replace(/\s+/g, " ").trim().toLowerCase();
}

export function dictionaryAITranslationVisible(translation: string | undefined, primaryMeaning: string) {
  if (!translation) {
    return false;
  }

  return normalizeDictionaryText(translation) !== normalizeDictionaryText(primaryMeaning);
}

export function dictionaryResolvedQuery(lookup: DictionaryLookupSnapshot) {
  if (lookup.state.kind === "ready") {
    return lookup.state.result.query.trim() || lookup.query;
  }
  return lookup.query;
}

export function dictionaryContextExplainQuery(
  result: Extract<WebDictResult, { kind: "entry" }>,
  fallbackQuery: string,
) {
  return result.entry.baseWord?.trim() || result.query.trim() || result.entry.word.trim() || fallbackQuery;
}

export function dictionaryIsManualLookup(lookup: DictionaryLookupSnapshot | null) {
  if (!lookup) {
    return false;
  }

  return lookup.sentenceId === "__manual__" || lookup.label === "手动查词";
}

export function dictionaryAILookupSource(lookup: DictionaryLookupSnapshot): DictAISourceDto {
  if (dictionaryIsManualLookup(lookup)) {
    return "manual_search";
  }
  if (lookup.label === "选区查词" || lookup.title === "选区查词") {
    return "selection";
  }
  return "reader_click";
}

export function dictionaryAIRequestForLookup(
  lookup: DictionaryLookupSnapshot | null,
  mode: WebDictAIRequest["mode"],
): WebDictAIRequest | null {
  if (!lookup || !lookup.contextSentence.trim() || dictionaryIsManualLookup(lookup)) {
    return null;
  }

  const resolvedQuery = dictionaryResolvedQuery(lookup);

  if (mode === "context_explain") {
    const result = lookup.state.kind === "ready" ? lookup.state.result : null;
    if (!result || result.kind !== "entry") {
      return null;
    }

    return {
      mode,
      query: dictionaryContextExplainQuery(result, resolvedQuery),
      queryType: lookup.lookupType,
      contextSentence: lookup.contextSentence,
      occurrence: lookup.occurrence,
      recordId: lookup.recordId,
      sentenceId: lookup.sentenceId,
      source: dictionaryAILookupSource(lookup),
      entryId: result.entry.id,
    };
  }

  return {
    mode,
    query: resolvedQuery,
    queryType: lookup.lookupType,
    contextSentence: lookup.contextSentence,
    occurrence: lookup.occurrence,
    recordId: lookup.recordId,
    sentenceId: lookup.sentenceId,
    source: dictionaryAILookupSource(lookup),
  };
}

export function dictionaryAIRequestKey(request: WebDictAIRequest) {
  const entryIdPart = request.mode === "context_explain" ? String(request.entryId) : "missing";
  return [
    request.mode,
    request.query.toLowerCase(),
    request.queryType,
    request.contextSentence.trim().toLowerCase(),
    entryIdPart,
  ].join("::");
}

export function dictionaryAIContextKey(lookup: DictionaryLookupSnapshot | null) {
  if (!lookup) {
    return null;
  }

  const base = [
    lookup.query.toLowerCase(),
    lookup.lookupType,
    lookup.contextSentence.trim().toLowerCase(),
    lookup.sentenceId,
    lookup.anchorText.toLowerCase(),
    lookup.occurrence ?? "",
  ].join("::");

  if (lookup.state.kind !== "ready") {
    return `${base}::${lookup.state.kind}`;
  }

  if (lookup.state.result.kind === "entry") {
    return `${base}::entry::${lookup.state.result.entry.id}`;
  }

  return `${base}::${lookup.state.result.kind}`;
}

export function dictionaryLookupBase(lookup: DictionaryLookupSnapshot) {
  const { state: _state, ...base } = lookup;
  return base;
}

export function isDictionaryAIErrorResult(value: unknown): value is WebDictAIErrorResult {
  if (!value || typeof value !== "object") {
    return false;
  }

  const payload = value as Record<string, unknown>;
  return (
    payload.kind === "error" &&
    typeof payload.query === "string" &&
    typeof payload.status === "number" &&
    typeof payload.code === "string" &&
    typeof payload.message === "string"
  );
}

export function dictionaryAIActionLabel(
  mode: WebDictAIRequest["mode"],
  state: DictionaryAIViewState,
  panelOpen: boolean,
) {
  const baseLabel = mode === "context_explain" ? "AI 语境解读" : "词典未收录，试试 AI";

  if (state.kind === "loading" && state.mode === mode) {
    return mode === "context_explain" ? "AI 解读中..." : "AI 生成中...";
  }
  if (state.kind === "ready" && state.mode === mode) {
    return panelOpen
      ? mode === "context_explain"
        ? "收起 AI 语境解读"
        : "收起 AI 结果"
      : baseLabel;
  }
  if (state.kind === "error" && state.mode === mode) {
    return mode === "context_explain" ? "重试 AI 语境解读" : "重试 AI 补充";
  }

  return baseLabel;
}

function splitDictionaryField(value?: string) {
  return (value ?? "")
    .split(/[；;]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function dictionaryRenderableEntryKey(entry: DictionaryRenderableEntry) {
  return entry.id ?? `${entry.word.toLowerCase()}-${entry.baseWord?.toLowerCase() ?? "entry"}`;
}

export function dictionaryAIConfidenceLabel(confidence?: WebDictAIResult["confidence"]) {
  return confidence ? dictionaryAIConfidenceLabelMap[confidence] : null;
}

export function dictionaryAIClassificationLabel(
  classification?: Extract<WebDictAIResult, { mode: "missing_fallback" }>["classification"],
) {
  return classification ? dictionaryAIClassificationLabelMap[classification] : null;
}

export function dictionarySenseItems(entry: DictionaryRenderableEntry): DictionarySenseItem[] {
  let senseNumber = 0;
  const entryKey = dictionaryRenderableEntryKey(entry);

  return entry.meanings.flatMap((meaning, meaningIndex) =>
    meaning.definitions.map((definition, definitionIndex) => {
      senseNumber += 1;
      const examples = splitDictionaryField(definition.example);
      const translations = splitDictionaryField(definition.exampleTranslation);
      const seenExamples = new Set<string>();
      const definitionExamples = examples.flatMap((example, exampleIndex) => {
        const normalized = example.trim();
        if (!normalized) {
          return [];
        }
        const dedupeKey = normalized.toLowerCase();
        if (seenExamples.has(dedupeKey)) {
          return [];
        }
        seenExamples.add(dedupeKey);
        return [
          {
            key: `${entryKey}-${meaningIndex}-${definitionIndex}-example-${exampleIndex}`,
            example: normalized,
            exampleTranslation: translations[exampleIndex]?.trim() || undefined,
          },
        ];
      });

      return {
        key: `${entryKey}-${meaningIndex}-${definitionIndex}-${definition.meaning}`,
        number: senseNumber,
        partOfSpeech: meaning.partOfSpeech,
        meaning: definition.meaning,
        examples: definitionExamples,
      };
    }),
  );
}

export function dictionaryExampleGroups(
  entry: DictionaryRenderableEntry,
  senseItems: DictionarySenseItem[],
): DictionaryExampleGroup[] {
  const seenExamples = new Set(
    senseItems.flatMap((sense) => sense.examples.map((example) => example.example.trim().toLowerCase())),
  );
  const entryKey = dictionaryRenderableEntryKey(entry);

  const groups: DictionaryExampleGroup[] = senseItems
    .filter((sense) => sense.examples.length > 0)
    .map((sense) => ({
      key: sense.key,
      number: sense.number,
      partOfSpeech: sense.partOfSpeech,
      meaning: sense.meaning,
      examples: sense.examples,
    }));

  const supplementalExamples = entry.examples.flatMap((example, index) => {
    const normalized = example.example.trim();
    if (!normalized) {
      return [];
    }
    const dedupeKey = normalized.toLowerCase();
    if (seenExamples.has(dedupeKey)) {
      return [];
    }
    seenExamples.add(dedupeKey);
    return [
      {
        key: `${entryKey}-supplemental-example-${index}`,
        example: normalized,
        exampleTranslation: example.exampleTranslation?.trim() || undefined,
      },
    ];
  });

  if (supplementalExamples.length > 0) {
    groups.push({
      key: `${entryKey}-supplemental`,
      meaning: "补充例句",
      examples: supplementalExamples,
      supplemental: true,
    });
  }

  return groups;
}

export function dictionaryDisplayTags(tags: string[], readingGoal: string) {
  if (readingGoal !== "exam") {
    return [];
  }

  const values = tags
    .map((tag) => tag.trim())
    .filter(Boolean)
    .map((tag) => dictionaryTagLabelMap[tag.toLowerCase()] ?? tag.toUpperCase());

  return Array.from(new Set(values));
}

function disambiguationGroupMeta(
  candidate: WebDictCandidate,
  ambiguityKind: WebDictDisambiguationResult["ambiguityKind"],
) {
  if (candidate.candidateKind === "phrase") {
    return { key: "phrase", label: "完整短语", hint: "优先按固定表达区分" };
  }
  if (candidate.candidateKind === "proper_noun") {
    return { key: "proper_noun", label: "专有名词", hint: "区分专名与普通词义" };
  }
  if (candidate.candidateKind === "fragment" || candidate.entryKind === "fragment") {
    return { key: "fragment", label: "片段释义", hint: "更像局部搭配或片段命中" };
  }
  if (candidate.candidateKind === "variant") {
    return { key: "variant", label: "词形 / 变体", hint: "同源词形或变体词条" };
  }
  if (ambiguityKind === "proper_vs_common") {
    return { key: "common_word", label: "普通词", hint: "和专名相对的普通义项" };
  }

  return { key: "common_word", label: "普通词", hint: "按常见词义查看" };
}

export function groupDisambiguationCandidates(result: WebDictDisambiguationResult) {
  const groups = new Map<string, DictionaryCandidateGroup>();

  result.candidates.forEach((candidate) => {
    const meta = disambiguationGroupMeta(candidate, result.ambiguityKind);
    const current = groups.get(meta.key);
    if (current) {
      current.candidates.push(candidate);
      return;
    }

    groups.set(meta.key, {
      ...meta,
      candidates: [candidate],
    });
  });

  const order = ["proper_noun", "common_word", "phrase", "fragment", "variant"];
  return Array.from(groups.values()).sort((left, right) => {
    const leftIndex = order.indexOf(left.key);
    const rightIndex = order.indexOf(right.key);
    const safeLeft = leftIndex === -1 ? order.length : leftIndex;
    const safeRight = rightIndex === -1 ? order.length : rightIndex;
    return safeLeft - safeRight;
  });
}

export function dictionaryLookupHistoryKey(lookup: DictionaryLookupSnapshot) {
  return `${lookup.query}-${lookup.sentenceId}-${lookup.anchorText}`;
}

export function dictionaryEntrySummary(
  result: Extract<WebDictResult, { kind: "entry" }>,
  lookup?: DictionaryLookupSnapshot | null,
) {
  return contextualGlossaryText(lookup?.glossary) || firstMeaning(result) || "";
}

export function dictionaryLookupHistorySummary(lookup: DictionaryLookupSnapshot) {
  if (lookup.state.kind === "loading") {
    return "查询中";
  }
  if (lookup.state.kind !== "ready") {
    return "词典暂不可用";
  }

  const result = lookup.state.result;
  if (result.kind === "entry") {
    return dictionaryEntrySummary(result, lookup) || "已打开词条";
  }
  if (result.kind === "disambiguation") {
    return result.candidates[0]?.preview || result.candidates[0]?.label || "有多个候选词义";
  }
  if (result.kind === "not_found") {
    return "词典暂未收录";
  }

  return result.message || "词典暂不可用";
}

export function structuredInspectLabel(
  annotationType: string,
  phraseType?: InlineGlossary["phraseType"],
): string {
  if (annotationType === "phrase_gloss" && phraseType) {
    return phraseTypeLabel[phraseType];
  }
  if (annotationType === "context_gloss") {
    return "语境义";
  }
  if (annotationType === "term_note") {
    return "术语说明";
  }
  if (annotationType === "logic_note") {
    return "逻辑提示";
  }
  return "结构化标注";
}

export function structuredInspectSummary(glossary?: InlineGlossary): ReactNode {
  const text = contextualGlossaryText(glossary);
  if (!text) {
    return null;
  }

  return text;
}
