import { computed, inject, nextTick, onMounted, ref, watch } from 'vue';

const STORAGE_PREFIX = 'claread:example-lab:ai-rag';
const GENERATED_FRAGMENT_KEY = '__ai_rag_generated';

function parseJsonValue(value, fallback) {
  if (value === null || value === undefined || value === '') return fallback;
  if (typeof value === 'string') {
    try {
      const parsed = JSON.parse(value);
      return parsed === null || parsed === undefined ? fallback : parsed;
    } catch {
      return fallback;
    }
  }
  return value;
}

function readSessionState(key) {
  if (typeof window === 'undefined' || !window.sessionStorage || !key) return null;
  try {
    const raw = window.sessionStorage.getItem(key);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function writeSessionState(key, value) {
  if (typeof window === 'undefined' || !window.sessionStorage || !key) return;
  try {
    if (!value) {
      window.sessionStorage.removeItem(key);
      return;
    }
    window.sessionStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Ignore storage failures.
  }
}

function unwrapInjectedValue(value) {
  if (value && typeof value === 'object' && 'value' in value) return value.value;
  return value;
}

function stringifyEditorValue(value) {
  if (typeof value === 'string') return value;
  if (value === undefined) return '';
  return JSON.stringify(value, null, 2);
}

function emitFieldValue(emit, field, value) {
  if (typeof emit !== 'function' || !field) return;

  // The Directus docs expose the setFieldValue event but don't document
  // the exact payload shape. Emit both common forms for compatibility.
  emit('setFieldValue', field, value);
  emit('setFieldValue', { field, value });
}

function syncTextareaField(root, value) {
  const textarea = root?.querySelector('textarea.sans-serif, textarea');
  if (!textarea) return false;

  const nextValue = typeof value === 'string' ? value : stringifyEditorValue(value);
  if (textarea.value === nextValue) return true;

  textarea.value = nextValue;
  textarea.dispatchEvent(new Event('input', { bubbles: true }));
  textarea.dispatchEvent(new Event('change', { bubbles: true }));
  return true;
}

function syncCodeMirrorField(root, value) {
  const editor = root?.querySelector('.CodeMirror')?.CodeMirror;
  if (!editor || typeof editor.setValue !== 'function') return false;

  const nextValue = stringifyEditorValue(value);
  if (editor.getValue?.() === nextValue) return true;

  editor.setValue(nextValue);
  if (typeof editor.save === 'function') editor.save();
  return true;
}

function syncFieldEditor(field, value) {
  if (typeof document === 'undefined' || !field || value === undefined) return;

  const root = document.querySelector(`[data-field="${field}"]`);
  if (!root) return;

  if (Array.isArray(value)) {
    if (syncCodeMirrorField(root, value)) return;
    syncTextareaField(root, value);
    return;
  }

  syncTextareaField(root, value);
}

export default {
  id: 'claread-ai-rag-generator-interface',
  name: 'Claread AI RAG Generator',
  icon: 'auto_awesome',
  description: 'AI-powered RAG field generator for Example Lab entries',
  localTypes: ['presentation'],
  component: {
    props: ['value', 'collection', 'primaryKey', 'field', 'disabled', 'loading'],
    emits: ['input', 'setFieldValue'],
    setup(props, { emit }) {
      const values = inject('values', ref({}));
      const api = inject('api', null);
      const primaryKeyRef = inject('primaryKey', ref(null));
      const collectionRef = inject('collection', ref('eval_example_lab_entries'));

      const generating = ref(false);
      const error = ref(null);
      const lastResult = ref(null);
      const modelProfile = ref(null);
      const modelProfiles = ref([]);
      const showReasoning = ref(false);

      const sentenceText = computed(() => String(values?.value?.sentence_text || ''));
      const outputFragment = computed(() => {
        const parsed = parseJsonValue(values?.value?.output_fragment, {});
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
        return parsed;
      });
      const readingVariant = computed(() => String(values?.value?.reading_variant || 'intermediate_reading'));
      const exampleType = computed(() => String(values?.value?.example_type || ''));
      const exampleId = computed(() => String(values?.value?.example_id || 'draft'));
      const collectionName = computed(() =>
        props.collection || unwrapInjectedValue(collectionRef) || 'eval_example_lab_entries'
      );
      const itemPrimaryKey = computed(() =>
        props.primaryKey ?? unwrapInjectedValue(primaryKeyRef) ?? null
      );

      const isRagEligible = computed(() =>
        exampleType.value === 'grammar' || exampleType.value === 'sentence_analysis'
      );

      const canGenerate = computed(() =>
        isRagEligible.value &&
        sentenceText.value.trim().length > 0 &&
        outputFragment.value &&
        typeof outputFragment.value === 'object' &&
        !Array.isArray(outputFragment.value) &&
        String(outputFragment.value.type || '').trim().length > 0 &&
        String(modelProfile.value || '').trim().length > 0
      );

      const prereqMessage = computed(() => {
        if (!isRagEligible.value) {
          return 'Select example_type = grammar or sentence_analysis to enable AI generation.';
        }
        if (!sentenceText.value.trim()) {
          return 'Fill sentence text before generating.';
        }
        if (!String(outputFragment.value?.type || '').trim()) {
          return 'Choose an output fragment type before generating.';
        }
        if (!String(modelProfile.value || '').trim()) {
          return 'Choose a model profile before generating.';
        }
        return '';
      });

      const storageKey = computed(() => {
        const collection = collectionName.value;
        const recordId = itemPrimaryKey.value || exampleId.value || 'draft';
        return `${STORAGE_PREFIX}:${collection}:${recordId}`;
      });

      const modelSelectOptions = computed(() =>
        modelProfiles.value.map((profile) => {
          const badges = [];
          if (profile.annotation_route_default) badges.push('annotation-default');
          if (profile.default_profile) badges.push('global-default');
          const suffix = badges.length ? ` [${badges.join(', ')}]` : '';
          return {
            text: `${profile.profile_name} (${profile.model_name || profile.provider})${suffix}`,
            value: profile.profile_name,
          };
        })
      );

      const confidenceLabel = computed(() => {
        const value = String(lastResult.value?.confidence || '').toLowerCase();
        if (!value) return '';
        if (value === 'high') return 'High confidence';
        if (value === 'medium') return 'Medium confidence';
        if (value === 'low') return 'Low confidence';
        return value;
      });

      const usage = computed(() => lastResult.value?.usage || null);

      function restoreSessionState() {
        const stored = readSessionState(storageKey.value);
        if (!stored) return;
        if (stored.modelProfile && !modelProfile.value) modelProfile.value = stored.modelProfile;
        if (stored.lastResult) lastResult.value = stored.lastResult;
      }

      async function loadModelProfiles() {
        if (!api) {
          error.value = 'Directus API is unavailable in this interface.';
          return;
        }

        try {
          const response = await api.get('/eval-center/example-lab/model-profiles');
          const profiles = Array.isArray(response?.data?.data) ? response.data.data : [];
          modelProfiles.value = profiles;

          if (!modelProfile.value && profiles.length > 0) {
            const preferred =
              profiles.find((item) => item.annotation_route_default)
              || profiles.find((item) => item.default_profile)
              || profiles[0];
            modelProfile.value = preferred?.profile_name || null;
          }
        } catch (requestError) {
          error.value =
            requestError?.response?.data?.errors?.[0]?.message
            || requestError?.message
            || 'Failed to load model profiles.';
        }
      }

      function updateSiblingFields(data) {
        const formValues = values?.value;
        const nextValues = {
          grammar_tags: data.grammar_tags,
          structure_signals: data.structure_signals,
          retrieval_text: data.retrieval_text,
          teaching_goal: data.teaching_goal,
        };

        for (const [field, value] of Object.entries(nextValues)) {
          if (value === undefined) continue;
          emitFieldValue(emit, field, value);
          if (formValues && typeof formValues === 'object') {
            formValues[field] = value;
          }
        }

        const nextFragment = {
          ...outputFragment.value,
          [GENERATED_FRAGMENT_KEY]: Object.fromEntries(
            Object.entries(nextValues).filter(([, value]) => value !== undefined)
          ),
        };
        emitFieldValue(emit, 'output_fragment', nextFragment);
        if (formValues && typeof formValues === 'object') {
          formValues.output_fragment = nextFragment;
        }

        void nextTick(() => {
          // Some built-in Directus editors (CodeMirror / textarea wrappers)
          // don't visually refresh from sibling field emits alone.
          window.setTimeout(() => {
            for (const [field, value] of Object.entries(nextValues)) {
              syncFieldEditor(field, value);
            }
          }, 0);
        });
      }

      async function generate() {
        if (!canGenerate.value || generating.value) return;
        if (!api) {
          error.value = 'Directus API is unavailable in this interface.';
          return;
        }

        generating.value = true;
        error.value = null;
        showReasoning.value = false;

        try {
          const payload = {
            sentence_text: sentenceText.value,
            output_fragment: outputFragment.value,
            reading_variant: readingVariant.value,
            model_profile: modelProfile.value,
          };

          const response = await api.post('/eval-center/example-lab/ai-generate-rag-fields', payload);
          const data = response?.data?.data;
          if (!data || typeof data !== 'object') {
            throw new Error('Empty response from AI generation API.');
          }

          lastResult.value = data;
          updateSiblingFields(data);
        } catch (requestError) {
          error.value =
            requestError?.response?.data?.errors?.[0]?.message
            || requestError?.message
            || 'Failed to generate AI RAG fields.';
        } finally {
          generating.value = false;
        }
      }

      watch(storageKey, () => {
        restoreSessionState();
      }, { immediate: true });

      watch([lastResult, modelProfile], () => {
        writeSessionState(storageKey.value, {
          modelProfile: modelProfile.value || null,
          lastResult: lastResult.value || null,
        });
      }, { deep: true });

      onMounted(() => {
        restoreSessionState();
        void loadModelProfiles();
      });

      return {
        canGenerate,
        confidenceLabel,
        error,
        exampleType,
        generate,
        generating,
        isRagEligible,
        lastResult,
        modelProfile,
        modelSelectOptions,
        prereqMessage,
        showReasoning,
        usage,
      };
    },
    template: `
      <div class="rag-gen">
        <div class="toolbar">
          <div class="toolbar-header">
            <div class="toolbar-left">
              <v-icon name="auto_awesome" small class="toolbar-icon" />
              <span class="toolbar-label">AI Generate</span>
            </div>
          </div>
          <div class="toolbar-controls">
            <div class="model-row">
              <v-select
                v-model="modelProfile"
                :items="modelSelectOptions"
                item-text="text"
                item-value="value"
                placeholder="Model profile"
                class="model-picker"
              />
            </div>
            <div class="action-row">
              <v-button
                :disabled="!canGenerate || generating"
                :loading="generating"
                small
                @click="generate"
              >
                Generate
              </v-button>
            </div>
          </div>
        </div>

        <div v-if="prereqMessage" class="prereq">
          <v-icon name="info_outline" x-small />
          {{ prereqMessage }}
        </div>

        <v-notice v-if="error" type="danger" dense>{{ error }}</v-notice>

        <div v-if="lastResult" class="result-summary">
          <div class="summary-meta">
            <span v-if="lastResult.generated_by" class="meta-item">{{ lastResult.generated_by }}</span>
            <span v-if="lastResult.profile_name" class="meta-item">{{ lastResult.profile_name }}</span>
            <span v-if="lastResult.model_name" class="meta-item">{{ lastResult.model_name }}</span>
            <span v-if="lastResult.latency_ms" class="meta-item">{{ lastResult.latency_ms }} ms</span>
            <span v-if="usage && usage.prompt_tokens !== undefined" class="meta-item">
              prompt {{ usage.prompt_tokens }}
            </span>
            <span v-if="usage && usage.completion_tokens !== undefined" class="meta-item">
              completion {{ usage.completion_tokens }}
            </span>
            <span v-if="usage && usage.total_tokens !== undefined" class="meta-item">
              total {{ usage.total_tokens }}
            </span>
            <span v-if="confidenceLabel" class="conf-badge" :class="'conf-' + lastResult.confidence">
              {{ confidenceLabel }}
            </span>
          </div>

          <div v-if="lastResult.reasoning" class="reasoning-accordion">
            <button class="reasoning-btn" @click="showReasoning = !showReasoning">
              <v-icon :name="showReasoning ? 'expand_less' : 'expand_more'" x-small />
              字段理由 / Rationale
            </button>
            <pre v-if="showReasoning" class="reasoning-body">{{ lastResult.reasoning }}</pre>
          </div>
        </div>
      </div>
    `,
    styles: `
      .rag-gen {
        font-family: inherit;
      }

      .toolbar {
        display: grid;
        gap: 10px;
        padding: 10px 12px;
        background: var(--theme--background-subdued, #f8f9fa);
        border: 1px solid var(--theme--border-color, #e0e4e8);
        border-radius: 6px;
      }

      .toolbar-header {
        display: flex;
        align-items: center;
      }

      .toolbar-left {
        display: flex;
        align-items: center;
        gap: 6px;
      }

      .toolbar-icon {
        color: var(--theme--primary, #5b6cf9);
      }

      .toolbar-label {
        font-size: 13px;
        font-weight: 600;
        color: var(--theme--foreground, #1a1d23);
        letter-spacing: -0.01em;
      }

      .toolbar-controls {
        display: grid;
        gap: 10px;
      }

      .model-row {
        min-width: 0;
      }

      .model-picker {
        width: 100%;
        min-width: 0;
      }

      .action-row {
        display: flex;
        align-items: center;
        justify-content: flex-start;
      }

      .prereq {
        display: flex;
        align-items: center;
        gap: 5px;
        margin-top: 8px;
        padding: 6px 10px;
        border-radius: 4px;
        font-size: 11.5px;
        line-height: 1.5;
        color: var(--theme--foreground-subdued, #7a8294);
        background: transparent;
      }

      .result-summary {
        margin-top: 10px;
        padding: 8px 0 0;
        border-top: 1px dashed var(--theme--border-color, #e0e4e8);
      }

      .summary-meta {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
        align-items: center;
      }

      .meta-item {
        font-size: 11px;
        color: var(--theme--foreground-subdued, #7a8294);
        font-variant-numeric: tabular-nums;
        padding: 2px 7px;
        background: var(--theme--background-subdued, #f3f4f6);
        border-radius: 4px;
      }

      .conf-badge {
        font-size: 10.5px;
        font-weight: 700;
        padding: 2px 8px;
        border-radius: 10px;
        letter-spacing: 0.03em;
      }

      .conf-high {
        background: rgba(16, 185, 129, 0.1);
        color: #059669;
      }

      .conf-medium {
        background: rgba(245, 158, 11, 0.1);
        color: #d97706;
      }

      .conf-low {
        background: rgba(239, 68, 68, 0.1);
        color: #dc2626;
      }

      .reasoning-accordion {
        margin-top: 8px;
      }

      .reasoning-btn {
        display: inline-flex;
        align-items: center;
        gap: 3px;
        background: none;
        border: none;
        padding: 3px 0;
        font-size: 11.5px;
        font-weight: 500;
        color: var(--theme--primary, #5b6cf9);
        cursor: pointer;
      }

      .reasoning-btn:hover {
        opacity: 0.7;
      }

      .reasoning-body {
        margin: 6px 0 0;
        padding: 8px 10px;
        border-radius: 4px;
        font-size: 11.5px;
        line-height: 1.6;
        white-space: pre-wrap;
        word-break: break-word;
        color: var(--theme--foreground-subdued, #6b7280);
        background: var(--theme--background-subdued, #fafafb);
        max-height: 160px;
        overflow-y: auto;
      }
    `,
  },
};
