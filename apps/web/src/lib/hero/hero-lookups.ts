/**
 * Hero Reader Stage — pre-baked dictionary lookups.
 *
 * When the user clicks a clickable mark inside the hero mock, the
 * surface fires `onLookupIntent` with the mark's `glossary` data.
 * We turn that intent into a populated `DictionaryLookupSnapshot`
 * so the local hero quick peek can render without any dictionary
 * API call.
 *
 * For the hero, every clickable mark resolves into a static
 * "ready" entry result built from its inline glossary. This is the
 * same pattern the product uses for offline / preview states.
 */

import type { ReaderLookupIntent } from "@/lib/reader-plate/bridges/dictionary";
import type { DictionaryLookupSnapshot } from "@/components/reader/dictionary/contracts";
import type { WebDictEntry, WebDictEntryResult } from "@/types/api/dict";
import type {
  AnnotationType,
  InlineGlossary,
  InlineMarkModel,
  ReaderMockVm,
} from "@/types/view/ReaderMockVm";

const HERO_LOOKUP_LABELS: Partial<Record<AnnotationType, string>> = {
  vocab_highlight: "词典",
  phrase_gloss: "短语",
  context_gloss: "语境",
  term_note: "术语",
  logic_note: "逻辑",
};

const HERO_LOOKUP_PARTS_OF_SPEECH: Record<string, string> = {
  nationally: "adv.",
  deplored: "v.",
  chronically: "adv.",
  "miss out on": "phr.",
  "secretary of education": "n.",
  "excused or unexcused": "phr.",
  "academic achievement": "n.",
};

/**
 * Build a fake `WebDictEntry` from an `InlineGlossary`. The hero
 * only ever shows the short preview pane, so we only need the
 * primary meaning + an example + 1-2 phrases.
 */
function buildEntryFromGlossary(
  query: string,
  lookupKind: "word" | "phrase",
  glossary: InlineGlossary,
): WebDictEntry {
  const meaning = glossary.zh ?? glossary.gloss ?? "";
  const example = glossary.reason
    ? `${query} is used here to mean ${meaning.toLowerCase()}.`
    : `${query}在这里表示${meaning}。`;
  const exampleTranslation = glossary.reason
    ? `这里 ${query} 表示 ${meaning.toLowerCase()}。`
    : undefined;

  return {
    id: 0,
    word: query,
    baseWord: lookupKind === "word" ? query : undefined,
    homographNo: undefined,
    phonetic: undefined,
    meanings: [
      {
        partOfSpeech: HERO_LOOKUP_PARTS_OF_SPEECH[query.toLowerCase()] ?? (lookupKind === "word" ? "n." : "phr."),
        definitions: [
          {
            meaning,
            example,
            exampleTranslation,
          },
        ],
      },
    ],
    examples: [],
    phrases: [],
    entryKind: "entry",
    exchange: [],
    tags: [],
  };
}

function buildEntryResult(
  query: string,
  entry: WebDictEntry,
): WebDictEntryResult {
  return {
    kind: "entry",
    query,
    provider: "hero-preview",
    cached: true,
    entry,
  };
}

function getMarkSentenceId(mark: InlineMarkModel) {
  return mark.anchor.sentenceId;
}

function getMarkAnchorText(mark: InlineMarkModel) {
  if (mark.anchor.kind === "text") {
    return mark.anchor.anchorText;
  }

  return mark.anchor.parts.map((part) => part.anchorText).join(" ");
}

function getMarkOccurrence(mark: InlineMarkModel) {
  return mark.anchor.kind === "text" ? mark.anchor.occurrence : undefined;
}

function buildHeroLookupIntentFromMark(
  mark: InlineMarkModel,
  scene: ReaderMockVm,
): ReaderLookupIntent | null {
  if (!mark.clickable || !mark.glossary) {
    return null;
  }

  const sentenceId = getMarkSentenceId(mark);
  const contextSentence =
    scene.article.sentences.find((sentence) => sentence.sentenceId === sentenceId)?.text ?? "";
  const anchorText = getMarkAnchorText(mark);
  const query = mark.lookupText ?? anchorText;

  if (!query || !contextSentence) {
    return null;
  }

  return {
    kind: "lexical_lookup",
    query,
    lookupType: mark.lookupKind ?? (/\s/.test(query) ? "phrase" : "word"),
    sentenceId,
    contextSentence,
    anchorText,
    occurrence: getMarkOccurrence(mark),
    title: query,
    label: HERO_LOOKUP_LABELS[mark.annotationType],
    annotationType: mark.annotationType,
    visualTone: mark.visualTone,
    glossary: mark.glossary,
  };
}

/**
 * Build a `DictionaryLookupSnapshot` from a `ReaderLookupIntent`
 * fired by the surface when the user clicks a clickable mark.
 *
 * Returns `null` when the intent has no `glossary` (e.g. raw token
 * lookup without an inline mark) — the caller should then either
 * ignore or build a different fallback.
 */
export function buildHeroLookupFromIntent(
  intent: ReaderLookupIntent,
): DictionaryLookupSnapshot | null {
  const glossary = intent.glossary;
  if (!glossary) {
    return null;
  }

  const lookupType = intent.lookupType;
  const entry = buildEntryFromGlossary(intent.query, lookupType, glossary);
  const result = buildEntryResult(intent.query, entry);

  return {
    query: intent.query,
    lookupType,
    contextSentence: intent.contextSentence,
    sourceContext: intent.sourceContext,
    recordId: "__hero_preview__",
    sentenceId: intent.sentenceId,
    anchorText: intent.anchorText,
    anchorOffsets: intent.anchorOffsets,
    title: intent.title,
    label: intent.label,
    annotationType: intent.annotationType,
    visualTone: intent.visualTone,
    glossary,
    state: { kind: "ready", result },
  };
}

export function buildHeroLookupFromMarkId(
  markId: string,
  scene: ReaderMockVm,
): DictionaryLookupSnapshot | null {
  const mark = scene.inlineMarks.find((candidate) => candidate.id === markId);
  if (!mark) {
    return null;
  }

  const intent = buildHeroLookupIntentFromMark(mark, scene);
  return intent ? buildHeroLookupFromIntent(intent) : null;
}
