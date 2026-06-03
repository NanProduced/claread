<script setup>
import { computed } from "vue";
import { useNodeLabState } from "../composables/useNodeLabState";
import { useNodeLabApi } from "../composables/useNodeLabApi";
import { HELP_TEXT } from "../composables/useNodeLabConstants";
import { safeJsonParse, compactFactRows } from "../composables/useNodeLabFormatting";

const {
  baselineConfig, currentDraft, currentSavedCandidates, selectedCandidateValue,
  modelProfiles, state, loading, feedback,
} = useNodeLabState();

const { saveCandidateDraft, selectSavedCandidate, resetDraftToBaseline } = useNodeLabApi();

const candidateDiffFacts = computed(() => {
  const baseline = baselineConfig.value;
  if (!baseline) return [];
  const draft = currentDraft.value;
  const cleanPolicyLines = (draft.policy_lines || []).map((line) => String(line || "").trim()).filter(Boolean);
  const baselinePolicy = JSON.stringify(baseline.policy_lines || []);
  const candidatePolicy = JSON.stringify(cleanPolicyLines);
  const exampleCount = draft.few_shot_mode === "candidate"
    ? (draft.examples_edit_mode === "raw"
      ? (safeJsonParse(draft.examples_raw_text || "[]", []) || []).length
      : (draft.examples || []).length)
    : 0;
  return [
    {
      key: "instructions",
      label: "说明文本",
      changed: draft.instruction_text.trim() !== String(baseline.agent_instructions || "").trim(),
      value: draft.instruction_text.trim() !== String(baseline.agent_instructions || "").trim() ? "已修改" : "沿用 baseline",
    },
    {
      key: "policy",
      label: "Policy Lines",
      changed: baselinePolicy !== candidatePolicy,
      value: baselinePolicy !== candidatePolicy ? `${cleanPolicyLines.length} 行已调整` : "沿用 baseline",
    },
    {
      key: "few_shot",
      label: "Few-shot",
      changed: draft.few_shot_mode !== "baseline",
      value: draft.few_shot_mode === "candidate" ? `Candidate examples（${exampleCount} 条）` : draft.few_shot_mode === "off" ? "已关闭" : draft.few_shot_mode === "rag" ? "RAG 观测" : "沿用 baseline",
    },
    {
      key: "model",
      label: "模型",
      changed: Boolean(draft.model_profile),
      value: draft.model_profile || "沿用 baseline route",
    },
  ];
});

const fewShotModesMapped = computed(() => {
  const modes = [
    { text: '使用 Claread 本地 examples', value: 'baseline' },
    { text: '关闭 few-shot', value: 'off' },
    { text: '使用 Candidate examples', value: 'candidate' },
  ];
  if (state.activeNode === 'grammar') {
    modes.push({ text: '开启 RAG 观测', value: 'rag' });
  }
  return modes;
});

const exampleTypesMapped = computed(() => {
  if (state.activeNode === 'translation') return [{ text: 'translation', value: 'translation' }];
  if (state.activeNode === 'vocabulary') return [{ text: 'vocab', value: 'vocab' }];
  return [
    { text: 'grammar', value: 'grammar' },
    { text: 'sentence_analysis', value: 'sentence_analysis' },
    { text: 'vocab', value: 'vocab' },
    { text: 'phrase', value: 'phrase' },
    { text: 'context', value: 'context' },
    { text: 'translation', value: 'translation' },
  ];
});

const modelProfilesMapped = computed(() => [
  { text: '使用 Claread baseline route', value: '' },
  ...(modelProfiles.value || []).map((p) => ({ text: `${p.profile_name} · ${p.model_name}`, value: p.profile_name })),
]);

const savedCandidatesMapped = computed(() => [
  { text: '载入已保存 Candidate', value: '' },
  ...(currentSavedCandidates.value || []).map((c) => ({ text: c.label, value: c.candidate_id })),
]);

const exampleEditModesMapped = [
  { text: '结构化列表', value: 'structured' },
  { text: 'Raw JSON', value: 'raw' },
];

const rawJsonError = computed(() => {
  if (currentDraft.value.examples_edit_mode !== 'raw' || !currentDraft.value.examples_raw_text?.trim()) return null;
  try {
    const parsed = JSON.parse(currentDraft.value.examples_raw_text);
    if (!Array.isArray(parsed)) return 'JSON 内容必须是数组格式';
    return null;
  } catch (e) {
    return `JSON 语法错误：${e.message.replace(/^JSON\.parse:\s*/, '')}`;
  }
});

function scrollToSection(id) {
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}
</script>

<template>
  <section class="panel-section">
    <div class="section-header">
      <h3 class="section-title">Candidate 编辑</h3>
      <span class="help-icon" tabindex="0" :title="HELP_TEXT.candidate_delta">?</span>
    </div>
    <div v-if="feedback.info || feedback.error" class="feedback-banner" :class="feedback.error ? 'is-error' : 'is-success'" role="status">
      {{ feedback.error || feedback.info }}
    </div>
    <div class="toolbar mb-4">
      <v-select class="toolbar-select" v-model="selectedCandidateValue" :items="savedCandidatesMapped" @update:modelValue="selectSavedCandidate($event)" placeholder="载入已保存 Candidate" />
      <div class="toolbar-actions">
        <v-button class="btn-ghost" small @click="resetDraftToBaseline">重置草稿</v-button>
        <v-button secondary small :disabled="loading.saveCandidate" @click="saveCandidateDraft">保存草稿</v-button>
      </div>
    </div>

    <nav class="section-nav">
      <button class="section-nav-item" @click="scrollToSection('diff-summary')">差异摘要</button>
      <button class="section-nav-item" @click="scrollToSection('form-config')">配置</button>
      <button class="section-nav-item" @click="scrollToSection('form-instructions')">说明文本</button>
      <button class="section-nav-item" @click="scrollToSection('form-policy')">Policy</button>
      <button v-if="currentDraft.few_shot_mode === 'candidate'" class="section-nav-item" @click="scrollToSection('form-examples')">Examples</button>
    </nav>

    <div id="diff-summary" class="meta-grid highlight-changes mb-4">
      <div class="meta-item" v-for="item in candidateDiffFacts" :key="item.key">
        <span class="meta-label">{{ item.label }}</span>
        <span class="meta-value" :class="{ 'text-changed': item.changed }">{{ item.value }}</span>
      </div>
    </div>

    <div id="form-config" class="form-row">
      <div class="form-field">
        <span class="field-label">Few-shot 模式 <span class="help-icon inline" tabindex="0" :title="HELP_TEXT.few_shot_mode">?</span></span>
        <v-select v-model="currentDraft.few_shot_mode" :items="fewShotModesMapped" />
      </div>
      <div class="form-field">
        <span class="field-label">模型 Profile</span>
        <v-select v-model="currentDraft.model_profile" :items="modelProfilesMapped" />
      </div>
    </div>

    <div id="form-instructions" class="form-field mb-4">
      <span class="field-label">Agent Instructions</span>
      <v-textarea v-model="currentDraft.instruction_text" :rows="6" />
    </div>

    <div id="form-policy" class="form-field mb-4">
      <span class="field-label">Policy Lines</span>
      <div class="list-editor">
        <div v-for="(line, index) in currentDraft.policy_lines" :key="`policy-${index}`" class="list-row">
          <v-input class="flex-1" v-model="currentDraft.policy_lines[index]" />
          <v-button icon small class="btn-danger-text" @click="currentDraft.policy_lines.splice(index, 1)">
            <v-icon name="delete" />
          </v-button>
        </div>
        <v-button class="btn-ghost align-start" small @click="currentDraft.policy_lines.push('')">+ 新增 Policy Line</v-button>
      </div>
    </div>

    <div v-if="currentDraft.few_shot_mode === 'candidate'" id="form-examples" class="form-field mb-4">
      <div class="field-header mb-2">
        <span class="field-label">Candidate Examples</span>
        <v-select class="w-auto" style="min-width: 140px;" v-model="currentDraft.examples_edit_mode" :items="exampleEditModesMapped" />
      </div>
      <div v-if="currentDraft.examples_edit_mode === 'structured'" class="list-editor">
        <div v-for="(example, index) in currentDraft.examples" :key="`example-${index}`" class="example-card">
          <div class="example-header">
            <v-select class="w-auto" style="min-width: 160px;" v-model="currentDraft.examples[index].example_type" :items="exampleTypesMapped" />
            <v-button icon small class="btn-danger-text" @click="currentDraft.examples.splice(index, 1)">
              <v-icon name="delete" />
            </v-button>
          </div>
          <v-input placeholder="示例原句" v-model="currentDraft.examples[index].sentence_text" />
          <v-textarea placeholder="输出片段" v-model="currentDraft.examples[index].output_fragment" :rows="3" />
        </div>
        <v-button class="btn-ghost align-start" small @click="currentDraft.examples.push({ example_type: state.activeNode === 'translation' ? 'translation' : state.activeNode === 'vocabulary' ? 'vocab' : 'grammar', sentence_text: '', output_fragment: '' })">
          + 新增 Example
        </v-button>
      </div>
      <v-textarea v-else class="code-font" v-model="currentDraft.examples_raw_text" :rows="10" />
      <p v-if="rawJsonError" class="validation-error">{{ rawJsonError }}</p>
    </div>

    <details class="detail-card mt-3">
      <summary>草稿元数据管理</summary>
      <div class="detail-content">
        <div class="form-row">
          <div class="form-field">
            <span class="field-label">Label</span>
            <v-input v-model="currentDraft.label" />
          </div>
          <div class="form-field">
            <span class="field-label">Description</span>
            <v-input v-model="currentDraft.description" />
          </div>
        </div>
        <div class="form-field mt-3">
          <span class="field-label">Notes</span>
          <v-textarea v-model="currentDraft.notes" :rows="2" />
        </div>
      </div>
    </details>
  </section>
</template>

<style scoped>
.panel-section {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: 24px;
}

.section-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}

.section-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--color-text);
}

.help-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 999px;
  border: 1px solid var(--color-text-subdued);
  color: var(--color-text-subdued);
  font-size: 13px;
  font-weight: 600;
  cursor: help;
  transition: border-color 0.15s, color 0.15s;
}
.help-icon:hover,
.help-icon:focus-visible {
  border-color: var(--color-primary);
  color: var(--color-primary);
  outline: none;
}

.help-icon.inline {
  margin-left: 6px;
}

.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

.toolbar-select {
  max-width: 200px;
}

.toolbar-actions {
  display: flex;
  gap: 8px;
  align-items: center;
}

.section-nav {
  display: flex;
  gap: 4px;
  padding: 8px 0;
  margin-bottom: 12px;
  border-bottom: 1px solid var(--color-border);
  position: sticky;
  top: 0;
  z-index: 10;
  background: var(--color-surface);
}
.section-nav-item {
  padding: 4px 10px;
  font-size: 12px;
  font-weight: 500;
  color: var(--color-text-subdued);
  background: none;
  border: 1px solid transparent;
  border-radius: var(--radius-sm, 6px);
  cursor: pointer;
}
.section-nav-item:hover {
  color: var(--color-text);
  background: var(--color-surface-subdued);
  border-color: var(--color-border);
}

.meta-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 16px;
  padding: 16px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  margin-bottom: 16px;
}

.highlight-changes {
  background: color-mix(in srgb, var(--color-surface-subdued) 50%, var(--color-surface));
}

.meta-grid .meta-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.meta-label {
  font-size: 13px;
  color: var(--color-text-subdued);
  font-weight: 500;
}

.meta-value {
  font-size: 14px;
  font-weight: 500;
}

.text-changed {
  color: var(--color-primary);
}

.form-row {
  display: flex;
  gap: 16px;
  margin-bottom: 16px;
}

.form-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  flex: 1;
  margin-bottom: 16px;
}

.field-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.field-label {
  font-size: 13px;
  font-weight: 500;
  color: var(--color-text-subdued);
}

.w-auto {
  width: auto;
}

.code-font {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 13px;
}

.list-editor {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.list-row {
  display: flex;
  gap: 8px;
  align-items: center;
}

.flex-1 {
  flex: 1;
  min-width: 0;
}

.example-card {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  background: var(--color-surface-subdued);
}

.example-header {
  display: flex;
  justify-content: space-between;
}

.align-start {
  align-self: flex-start;
}

.btn-ghost {
  color: var(--color-text-subdued);
}

.btn-ghost:hover {
  color: var(--color-text);
  background: var(--color-surface-subdued);
}

.btn-danger-text {
  color: var(--theme--danger, #dc2626);
  font-size: 13px;
  padding: 4px 8px;
}

.detail-card {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  overflow: hidden;
}

.detail-card summary {
  padding: 12px 16px;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  background: var(--color-surface-subdued);
  border-left: 3px solid var(--color-border);
  transition: border-left-color 0.15s;
}
.detail-card summary:hover {
  border-left-color: var(--color-primary);
}
.detail-card[open] summary {
  border-left-color: var(--color-primary);
}

.detail-content {
  padding: 16px;
  border-top: 1px solid var(--color-border);
}

.mb-2 { margin-bottom: 8px; }
.mb-4 { margin-bottom: 16px; }
.mt-3 { margin-top: 12px; }

.feedback-banner {
  padding: 10px 14px;
  border-radius: var(--radius-md);
  font-size: 13px;
  line-height: 1.5;
  margin-bottom: 16px;
}
.feedback-banner.is-success {
  background: color-mix(in srgb, var(--theme--success, #10b981) 8%, var(--color-surface));
  border: 1px solid color-mix(in srgb, var(--theme--success, #10b981) 30%, var(--color-border));
  color: var(--theme--success, #10b981);
}
.feedback-banner.is-error {
  background: color-mix(in srgb, var(--theme--danger, #dc2626) 8%, var(--color-surface));
  border: 1px solid color-mix(in srgb, var(--theme--danger, #dc2626) 30%, var(--color-border));
  color: var(--theme--danger, #dc2626);
}

.validation-error {
  margin: -8px 0 8px;
  font-size: 12px;
  color: var(--theme--danger, #dc2626);
  line-height: 1.45;
}
</style>
