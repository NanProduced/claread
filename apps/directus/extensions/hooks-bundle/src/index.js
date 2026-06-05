import { InvalidPayloadError } from '@directus/errors';

const COLLECTION = 'eval_example_lab_entries';
const GENERATED_FRAGMENT_KEY = '__ai_rag_generated';

// output_fragment.type -> example_type mapping
const TYPE_MAP = {
  grammar: ['grammar_note'],
  sentence_analysis: ['sentence_analysis'],
  vocab: ['vocab_highlight', 'term_note', 'logic_note'],
  phrase: ['phrase_gloss'],
  context: ['context_gloss'],
  translation: ['translation', 'academic_translation'],
};

const CANONICAL_RAG_TYPE_MAP = {
  grammar: 'grammar_note',
  sentence_analysis: 'sentence_analysis',
};

const TARGET_NODE_MAP = {
  grammar: 'grammar',
  sentence_analysis: 'grammar',
  vocab: 'vocabulary',
  phrase: 'vocabulary',
  context: 'vocabulary',
  translation: 'translation',
};

// RAG-derived fields populated by the AI generator and stripped from output_fragment
// before persistence. `__ai_rag_generated` is the only transport key allowed inside
// output_fragment during create/update; the hook must extract it and clean it up
// before the value reaches storage.
const GENERATED_RAG_FIELD_KEYS = [
  'grammar_tags',
  'retrieval_text',
  'derived_by',
];

// Open-vocabulary normalization for grammar_tags. These rules are intentionally
// local to the hook so that the persisted tag is the canonical form even if the
// AI generator or a human curator types a variant.
//
// MUST stay aligned with `_TAG_ALIASES` in
// services/api/app/eval_adapter/example_lab.py — both layers must produce the
// same canonical form for the same input, otherwise tag overlap / rerank
// will diverge across the Directus write path and the API rebuild path.
const GRAMMAR_TAG_ALIASES = {
  defining_relative_clause: 'restrictive_relative_clause',
  limiting_relative_clause: 'restrictive_relative_clause',
  non_defining_relative_clause: 'nonrestrictive_relative_clause',
  'non-defining_relative_clause': 'nonrestrictive_relative_clause',
  participle_adverbial: 'past_participle_adverbial',
  participle_attribute: 'past_participle_attribute',
  fronting: 'subject_clause_fronting',
};

const GRAMMAR_TAG_FORBIDDEN_TOKENS = new Set([
  'general',
  'complex',
  'other',
  'misc',
]);

/**
 * Parse a field that might be a JSON string or already-parsed object/array.
 */
function parseJsonField(value) {
  if (value === null || value === undefined) return value;
  if (typeof value === 'string') {
    try {
      return JSON.parse(value);
    } catch {
      return value;
    }
  }
  return value;
}

function normalizeJsonFields(payload) {
  if (!payload || typeof payload !== 'object') return;

  const fragment = parseJsonField(payload.output_fragment);
  if (fragment && typeof fragment === 'object' && !Array.isArray(fragment)) {
    payload.output_fragment = fragment;
  }

  const grammarTags = parseJsonField(payload.grammar_tags);
  if (Array.isArray(grammarTags)) {
    payload.grammar_tags = grammarTags;
  }

  const tags = parseJsonField(payload.tags_json);
  if (Array.isArray(tags)) {
    payload.tags_json = tags;
  }
}

function normalizeCanonicalFragmentType(payload) {
  if (!payload || typeof payload !== 'object') return;

  const et = payload.example_type;
  const canonicalType = CANONICAL_RAG_TYPE_MAP[et];
  if (!canonicalType) return;

  const fragment = parseJsonField(payload.output_fragment);
  if (!fragment || typeof fragment !== 'object' || Array.isArray(fragment)) return;
  if (fragment.type) return;

  payload.output_fragment = { ...fragment, type: canonicalType };
}

function normalizeGrammarTag(rawTag) {
  if (typeof rawTag !== 'string') return null;
  const collapsed = rawTag
    .trim()
    .toLowerCase()
    .replace(/\s+/g, '_')
    .replace(/-+/g, '_')
    .replace(/_+/g, '_')
    .replace(/^_+|_+$/g, '');
  if (!collapsed) return null;
  return GRAMMAR_TAG_ALIASES[collapsed] || collapsed;
}

function normalizeGrammarTagsField(payload) {
  if (!Object.prototype.hasOwnProperty.call(payload || {}, 'grammar_tags')) return;
  const parsed = parseJsonField(payload.grammar_tags);
  if (parsed === null || parsed === undefined) {
    payload.grammar_tags = [];
    return;
  }
  if (!Array.isArray(parsed)) {
    payload.grammar_tags = [];
    return;
  }

  const seen = new Set();
  const normalized = [];
  for (const item of parsed) {
    const tag = normalizeGrammarTag(item);
    if (!tag) continue;
    if (GRAMMAR_TAG_FORBIDDEN_TOKENS.has(tag)) continue;
    if (seen.has(tag)) continue;
    seen.add(tag);
    normalized.push(tag);
  }
  payload.grammar_tags = normalized;
}

function extractGeneratedRagFields(payload) {
  if (!payload || typeof payload !== 'object') return;

  const fragment = parseJsonField(payload.output_fragment);
  if (!fragment || typeof fragment !== 'object' || Array.isArray(fragment)) return;

  const generated = fragment[GENERATED_FRAGMENT_KEY];
  if (generated && typeof generated === 'object' && !Array.isArray(generated)) {
    for (const field of GENERATED_RAG_FIELD_KEYS) {
      if (!Object.prototype.hasOwnProperty.call(generated, field)) continue;
      const generatedValue = generated[field];
      const currentValue = payload[field];
      const currentMissing = (
        currentValue === undefined
        || currentValue === null
        || currentValue === ''
        || (Array.isArray(currentValue) && currentValue.length === 0)
      );
      if (currentMissing && generatedValue !== undefined) {
        payload[field] = generatedValue;
      }
    }
    // The hook is the only writer to the database; this is the canonical place
    // to stamp when RAG-derived fields were last rebuilt.
    payload.derived_at = new Date().toISOString();
  }

  // Always strip the transport key so the stored output_fragment is the clean
  // few-shot JSON, never a mix of editor payload and AI scratch space.
  const nextFragment = { ...fragment };
  delete nextFragment[GENERATED_FRAGMENT_KEY];
  payload.output_fragment = nextFragment;
}

function validateFragmentShape(et, fragment, errors) {
  if (et === 'grammar') {
    if (fragment.type !== 'grammar_note') {
      errors.push('grammar 条目的 output_fragment.type 必须固定为 grammar_note');
    }
    if (!fragment.label || typeof fragment.label !== 'string' || !fragment.label.trim()) {
      errors.push('grammar 条目必须有非空的 output_fragment.label');
    }
    if (!fragment.note_zh || typeof fragment.note_zh !== 'string' || !fragment.note_zh.trim()) {
      errors.push('grammar 条目必须有非空的 output_fragment.note_zh');
    }
    if (fragment.spans !== undefined && fragment.spans !== null) {
      if (!Array.isArray(fragment.spans)) {
        errors.push('output_fragment.spans 必须是数组（可为空）');
      } else {
        fragment.spans.forEach((span, index) => {
          if (!span || typeof span !== 'object' || Array.isArray(span)) {
            errors.push(`output_fragment.spans[${index}] 必须是对象`);
            return;
          }
          if (typeof span.text !== 'string' || !span.text.trim()) {
            errors.push(`output_fragment.spans[${index}].text 必须为非空字符串`);
          }
        });
      }
    }
    return;
  }

  if (et === 'sentence_analysis') {
    if (fragment.type !== 'sentence_analysis') {
      errors.push('sentence_analysis 条目的 output_fragment.type 必须固定为 sentence_analysis');
    }
    if (!fragment.label || typeof fragment.label !== 'string' || !fragment.label.trim()) {
      errors.push('sentence_analysis 条目必须有非空的 output_fragment.label');
    }
    if (
      !fragment.analysis_zh
      || typeof fragment.analysis_zh !== 'string'
      || !fragment.analysis_zh.trim()
    ) {
      errors.push('sentence_analysis 条目必须有非空的 output_fragment.analysis_zh');
    }
    if (fragment.chunks !== undefined && fragment.chunks !== null) {
      if (!Array.isArray(fragment.chunks)) {
        errors.push('output_fragment.chunks 必须是数组（可为空）');
      } else {
        fragment.chunks.forEach((chunk, index) => {
          if (!chunk || typeof chunk !== 'object' || Array.isArray(chunk)) {
            errors.push(`output_fragment.chunks[${index}] 必须是对象`);
            return;
          }
          if (!Number.isInteger(chunk.order) || chunk.order <= 0) {
            errors.push(`output_fragment.chunks[${index}].order 必须是正整数`);
          }
          if (typeof chunk.label !== 'string' || !chunk.label.trim()) {
            errors.push(`output_fragment.chunks[${index}].label 必须为非空字符串`);
          }
          if (typeof chunk.text !== 'string' || !chunk.text.trim()) {
            errors.push(`output_fragment.chunks[${index}].text 必须为非空字符串`);
          }
        });
      }
    }
  }
}

/**
 * Validate an eval_example_lab_entries payload.
 */
function validatePayload(payload) {
  const errors = [];
  const et = payload.example_type;

  const fragment = parseJsonField(payload.output_fragment);
  if (
    Object.prototype.hasOwnProperty.call(payload, 'output_fragment')
    && fragment !== null
    && fragment !== undefined
    && (typeof fragment !== 'object' || Array.isArray(fragment))
  ) {
    errors.push('output_fragment 必须是 JSON object');
  }

  if (fragment && typeof fragment === 'object' && fragment.type && et && TYPE_MAP[et]) {
    if (!TYPE_MAP[et].includes(fragment.type)) {
      if (et === 'grammar' && fragment.type === 'sentence_analysis') {
        errors.push('grammar 条目固定使用 grammar_note；sentence_analysis 条目请改用 example_type=sentence_analysis');
      } else {
        errors.push(
          `output_fragment.type "${fragment.type}" 与 example_type "${et}" 不匹配，允许值: ${TYPE_MAP[et].join(', ')}`
        );
      }
    }
  }

  if (
    (et === 'grammar' || et === 'sentence_analysis')
    && fragment
    && typeof fragment === 'object'
  ) {
    validateFragmentShape(et, fragment, errors);
  }

  return errors;
}

function syncLabelFromFragment(payload) {
  const et = payload.example_type;
  if (et !== 'grammar' && et !== 'sentence_analysis') return;

  const fragment = parseJsonField(payload.output_fragment);
  if (fragment && typeof fragment === 'object' && fragment.label && fragment.label.trim()) {
    payload.label = fragment.label.trim();
  }
}

function syncTargetNodeFromExampleType(payload) {
  const targetNode = TARGET_NODE_MAP[payload.example_type];
  if (targetNode) {
    payload.target_node = targetNode;
  }
}

export default ({ filter }) => {
  filter(`${COLLECTION}.items.create`, async (payload) => {
    normalizeJsonFields(payload);
    extractGeneratedRagFields(payload);
    normalizeCanonicalFragmentType(payload);
    normalizeGrammarTagsField(payload);
    syncLabelFromFragment(payload);
    syncTargetNodeFromExampleType(payload);

    const errors = validatePayload(payload);
    if (errors.length) {
      throw new InvalidPayloadError({ reason: errors.join('; ') });
    }

    return payload;
  });

  filter(`${COLLECTION}.items.update`, async (payload) => {
    normalizeJsonFields(payload);
    extractGeneratedRagFields(payload);
    normalizeCanonicalFragmentType(payload);
    normalizeGrammarTagsField(payload);
    syncLabelFromFragment(payload);
    syncTargetNodeFromExampleType(payload);

    const errors = validatePayload(payload);
    if (errors.length) {
      throw new InvalidPayloadError({ reason: errors.join('; ') });
    }

    return payload;
  });
};
