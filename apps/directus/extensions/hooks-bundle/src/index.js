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

// Allowed values for enum fields; keep in sync with Python example_lab.py VALID_*
const VALID_GRAMMAR_TAGS = [
  'general', 'nonfinite', 'inversion', 'parallelism', 'nested_clause',
  'object_clause', 'relative_clause', 'nonrestrictive_relative_clause',
  'participle_adverbial', 'participle_attribute', 'appositive_clause',
  'main_clause_interruption', 'passive_voice',
];

const VALID_STRUCTURE_SIGNALS = [
  'has_wh_clause', 'local_structure', 'has_inversion', 'has_that_clause',
  'has_comma_insertion', 'nested_structure', 'leading_vbn', 'leading_ving', 'long_sentence',
];

const VALID_TEACHING_GOALS = [
  'focused', 'balanced', 'structural', 'explicit_split', 'structural_logic',
  'explicit_exam', 'speed_support', 'rhetorical', 'info_extraction',
];

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

  const structureSignals = parseJsonField(payload.structure_signals);
  if (Array.isArray(structureSignals)) {
    payload.structure_signals = structureSignals;
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

function extractGeneratedRagFields(payload) {
  if (!payload || typeof payload !== 'object') return;

  const fragment = parseJsonField(payload.output_fragment);
  if (!fragment || typeof fragment !== 'object' || Array.isArray(fragment)) return;

  const generated = fragment[GENERATED_FRAGMENT_KEY];
  if (!generated || typeof generated !== 'object' || Array.isArray(generated)) return;

  for (const field of ['grammar_tags', 'structure_signals', 'retrieval_text', 'teaching_goal']) {
    const currentValue = payload[field];
    const generatedValue = generated[field];
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

  const nextFragment = { ...fragment };
  delete nextFragment[GENERATED_FRAGMENT_KEY];
  payload.output_fragment = nextFragment;
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

  const grammarTags = parseJsonField(payload.grammar_tags);
  if (Array.isArray(grammarTags)) {
    const invalid = grammarTags.filter((tag) => !VALID_GRAMMAR_TAGS.includes(tag));
    if (invalid.length) {
      errors.push(`grammar_tags 包含非法值: ${invalid.join(', ')}`);
    }
  }

  const structureSignals = parseJsonField(payload.structure_signals);
  if (Array.isArray(structureSignals)) {
    const invalid = structureSignals.filter((signal) => !VALID_STRUCTURE_SIGNALS.includes(signal));
    if (invalid.length) {
      errors.push(`structure_signals 包含非法值: ${invalid.join(', ')}`);
    }
  }

  if (payload.teaching_goal && !VALID_TEACHING_GOALS.includes(payload.teaching_goal)) {
    errors.push(`teaching_goal "${payload.teaching_goal}" 不在允许值列表中`);
  }

  if ((et === 'grammar' || et === 'sentence_analysis') && fragment && typeof fragment === 'object') {
    if (!fragment.label || !fragment.label.trim()) {
      errors.push(`${et} 类型必须有非空的 output_fragment.label`);
    }
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
    syncLabelFromFragment(payload);
    syncTargetNodeFromExampleType(payload);

    const errors = validatePayload(payload);
    if (errors.length) {
      throw new InvalidPayloadError({ reason: errors.join('; ') });
    }

    return payload;
  });
};
