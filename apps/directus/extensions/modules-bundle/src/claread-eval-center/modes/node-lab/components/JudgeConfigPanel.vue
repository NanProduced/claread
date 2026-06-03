<script setup>
import { computed } from "vue";
import { useNodeLabState } from "../composables/useNodeLabState";
import { useNodeLabApi } from "../composables/useNodeLabApi";
import {
  safeJsonParse,
  nodeLabel,
  readingGoalLabel,
  readingVariantLabel,
  normalizePreviewText,
  buildInputPreview,
  statusLabel,
  compareViewSourceLabel,
  compareViewSourceTone,
  compareTrialSourceLabel,
  shortId,
  trialJudgeCount,
  defaultJudgeModeForNode,
  judgeModeAllowedForNode,
  defaultJudgeDraft,
  defaultVariantForGoal,
  normalizeGoal,
  normalizeVariantForGoal,
} from "../composables/useNodeLabFormatting";
import {
  JUDGE_MODES as judgeModes,
  HELP_TEXT as helpText,
} from "../composables/useNodeLabConstants";

const {
  currentJudgeDraft,
  state,
  activeCompareTrial,
  judgePanelOpen,
  compareResult,
  activeCompareView,
  modelProfiles,
  currentJudgePresets,
  currentSavedJudgeConfigs,
  availableJudgeModes,
  currentCompareTrialId,
  latestCompareTrialId,
  pendingJudgeRequestId,
  loading,
  feedback,
  currentText,
  currentReadingGoal,
  currentReadingVariant,
} = useNodeLabState();

const {
  applyJudgeModeTemplate,
  applyJudgePreset,
  saveJudgeConfig,
  queueJudgeCompare,
  executeJudgeRequest,
} = useNodeLabApi();

const compareRequestSnapshot = computed(() => {
  return compareResult.value?.request_snapshot || null;
});

const activeCompareInputPreview = computed(() => {
  return String(
    activeCompareView.value?.inputPreview
    || activeCompareTrial.value?.input_excerpt
    || compareRequestSnapshot.value?.source_excerpt
    || ""
  ).trim();
});

const compareSnapshotContextMismatchReason = computed(() => {
  const snapshot = compareRequestSnapshot.value;
  if (!snapshot) return null;

  const snapshotNode = String(snapshot.node_name || compareResult.value?.node_name || "").trim();
  const snapshotGoal = String(snapshot.reading_goal || "").trim();
  const snapshotVariant = String(snapshot.reading_variant || "").trim();

  if (snapshotNode && snapshotNode !== state.activeNode) {
    return `当前页面节点是 ${nodeLabel(state.activeNode)}，但右侧结果来自 ${nodeLabel(snapshotNode)}`;
  }
  if (snapshotGoal && snapshotGoal !== currentReadingGoal.value) {
    return `当前页面阅读目标是 ${readingGoalLabel(currentReadingGoal.value)}，但右侧结果来自 ${readingGoalLabel(snapshotGoal)}`;
  }
  if (snapshotVariant && snapshotVariant !== currentReadingVariant.value) {
    return `当前页面阅读变体是 ${readingVariantLabel(currentReadingVariant.value)}，但右侧结果来自 ${readingVariantLabel(snapshotVariant)}`;
  }
  const comparePreview = normalizePreviewText(activeCompareInputPreview.value);
  const currentPreview = buildInputPreview(currentText.value);
  if (comparePreview && currentPreview && !currentPreview.startsWith(comparePreview)) {
    return "当前输入文本已变化，但右侧仍显示上一条 Compare 结果";
  }
  return null;
});

const judgePrerequisite = computed(() => {
  const hasPreset = Boolean(currentJudgeDraft.value.preset_id);
  const hasJudgerModel = currentJudgeDraft.value.judger_models.some((value) => String(value || "").trim());
  const mismatch = compareSnapshotContextMismatchReason.value;
  if (!hasPreset) {
    return {
      ready: false,
      title: "请先选择一个 Judge 预设",
      detail: "Judge 首版优先使用系统预设。先选定本节点的评测预设，再排队 Judge Request。",
    };
  }
  if (!hasJudgerModel) {
    return {
      ready: false,
      title: "请至少选择一个 Judger 模型",
      detail: "Judge Request 需要至少一个 Judger 模型 profile，才能真正发起评审。",
    };
  }
  if (currentCompareTrialId.value && !mismatch) {
    return {
      ready: true,
      title: "当前 Compare 已持久化",
      detail: `将基于当前 Compare 对应的 Trial ${currentCompareTrialId.value} 发起 Judge 评审。`,
    };
  }
  if (compareResult.value && !mismatch) {
    return {
      ready: true,
      title: "当前 Compare 结果可用于 Judge",
      detail: "Compare 结果尚未持久化，排队 Judge 时会自动保存为独立 Trial（不绑定 Session）。",
    };
  }
  if (compareResult.value && mismatch) {
    return {
      ready: true,
      title: "当前页面上下文已变化",
      detail: `${mismatch}。Judge 将评估右侧仍显示的上一条 Compare 结果；如需评当前表单，请先重新运行 Compare。`,
    };
  }
  if (latestCompareTrialId.value) {
    return {
      ready: true,
      title: "有历史持久化 Trial 可用",
      detail: `当前无 Compare 结果，但有历史 Trial ${latestCompareTrialId.value}。注意：Judge 将评历史结果，非当前页面内容。`,
    };
  }
  return {
    ready: false,
    title: "还没有可用的 Compare 结果",
    detail: "Judge 需要先运行一次 Compare。跑出结果后即可排队 Judge Request。",
  };
});

const availableJudgeModesMapped = computed(() => availableJudgeModes.value.map((m) => ({ text: m.label, value: m.id })));

const judgerModelOptions = computed(() => [
  { text: '不启用', value: '' },
  ...(modelProfiles.value || []).map(p => ({ text: p.model_name, value: p.profile_name }))
]);

const judgePresetOptions = computed(() => currentJudgePresets.value.map((preset) => ({
  text: `${preset.ui_label || preset.title}`,
  value: preset.preset_id,
})));

const savedJudgeConfigsMapped = computed(() => [
  { text: '载入已保存配置', value: '' },
  ...(currentSavedJudgeConfigs.value || []).map(c => ({ text: c.label, value: c.judge_config_id }))
]);

const currentJudgePreset = computed(() => {
  const presetId = currentJudgeDraft.value.preset_id;
  return currentJudgePresets.value.find((item) => item.preset_id === presetId) || null;
});

function jsonValidationError(value) {
  if (!value || !value.trim()) return null;
  try {
    JSON.parse(value);
    return null;
  } catch (e) {
    return e.message.replace(/^JSON\.parse:\s*/, '');
  }
}

const currentStep = computed(() => {
  if (!currentJudgeDraft.value.preset_id) return 1;
  if (!currentJudgeDraft.value.judger_models.some(v => String(v || '').trim())) return 2;
  return 3;
});
</script>

<template>
  <div class="judge-config-panel">
    <div class="judge-panel__body">
      <div class="step-indicator">
        <div class="step" :class="{ 'is-active': currentStep === 1, 'is-done': currentStep > 1 }">
          <span class="step-number">1</span>
          <span class="step-label">选择预设</span>
        </div>
        <div class="step-connector" :class="{ 'is-done': currentStep > 1 }"></div>
        <div class="step" :class="{ 'is-active': currentStep === 2, 'is-done': currentStep > 2 }">
          <span class="step-number">2</span>
          <span class="step-label">选择模型</span>
        </div>
        <div class="step-connector" :class="{ 'is-done': currentStep > 2 }"></div>
        <div class="step" :class="{ 'is-active': currentStep === 3, 'is-done': currentStep > 3 }">
          <span class="step-number">3</span>
          <span class="step-label">执行 Judge</span>
        </div>
      </div>
      <div v-if="feedback.info || feedback.error" class="feedback-banner" :class="feedback.error ? 'is-error' : 'is-success'" role="status">
        {{ feedback.error || feedback.info }}
      </div>
      <div v-if="judgePrerequisite.ready" class="quick-action-bar">
        <v-button :disabled="loading.queueJudge" @click="queueJudgeCompare({ autoExecute: true })">
          {{ loading.queueJudge ? '执行中...' : '创建并执行 Judge' }}
        </v-button>
        <span class="quick-action-hint">快捷操作：预设和模型已就绪</span>
      </div>
      <div class="form-field">
        <span class="field-label">系统预设</span>
        <v-select
          v-model="currentJudgeDraft.preset_id"
          :items="judgePresetOptions"
          @update:modelValue="applyJudgePreset($event)"
        />
      </div>

      <div v-if="currentJudgePreset" class="status-banner is-ready mt-3" role="status">
        <strong>{{ currentJudgePreset.ui_label || currentJudgePreset.title }}</strong>
        <p class="text-sm mt-1">
          适用节点：{{ nodeLabel(currentJudgePreset.node_name) }}
          <span class="divider">/</span>
          Strategy：{{ currentJudgePreset.strategy }}
          <span class="divider">/</span>
          Method：{{ currentJudgePreset.method }}
        </p>
      </div>

      <div class="form-row triple mt-3">
        <div class="form-field" v-for="slot in [0, 1, 2]" :key="`judger-${slot}`">
          <span class="field-label">Judger {{ slot + 1 }}</span>
          <v-select v-model="currentJudgeDraft.judger_models[slot]" :items="judgerModelOptions" />
        </div>
      </div>

      <div class="status-banner mt-3" :class="judgePrerequisite.ready ? 'is-ready' : 'is-warning'" role="status">
        <strong>{{ judgePrerequisite.title }}</strong>
        <p class="text-sm mt-1">{{ judgePrerequisite.detail }}</p>
      </div>

      <div class="action-buttons mt-3">
        <v-button :disabled="loading.queueJudge || !judgePrerequisite.ready" @click="queueJudgeCompare({ autoExecute: true })">创建并执行 Judge</v-button>
      </div>

      <details class="detail-card detail-card--compact mt-3">
        <summary>调试操作</summary>
        <div class="detail-content">
          <div class="action-buttons mb-3">
            <v-button secondary :disabled="loading.queueJudge || !judgePrerequisite.ready" @click="queueJudgeCompare()">仅创建 Request</v-button>
            <v-button v-if="pendingJudgeRequestId" :disabled="loading.executeJudge" @click="executeJudgeRequest(pendingJudgeRequestId)">执行这条 Request</v-button>
          </div>
          <p class="block-hint mb-3">仅在调试 Judge 链路时使用。日常流程优先直接点"创建并执行 Judge"。</p>
          <div class="form-field mb-3">
            <span class="field-label">Judge 方式</span>
            <v-select v-model="currentJudgeDraft.judge_mode" :items="availableJudgeModesMapped" @update:modelValue="applyJudgeModeTemplate($event)" />
          </div>
          <div class="form-field mb-3">
            <span class="field-label">Persona</span>
            <v-textarea v-model="currentJudgeDraft.persona_text" :rows="2" />
          </div>
          <div class="form-field mb-3">
            <span class="field-label">System Prompt</span>
            <v-textarea v-model="currentJudgeDraft.system_prompt" :rows="3" />
          </div>
          <div class="form-field mb-3">
            <span class="field-label">User Prompt</span>
            <v-textarea v-model="currentJudgeDraft.user_prompt" :rows="3" />
          </div>
          <div class="form-field">
            <span class="field-label">Rubric Bundle JSON</span>
            <v-textarea class="code-font" :class="{ 'is-invalid': jsonValidationError(currentJudgeDraft.rubric_bundle_json) }" v-model="currentJudgeDraft.rubric_bundle_json" :rows="6" />
            <p v-if="jsonValidationError(currentJudgeDraft.rubric_bundle_json)" class="validation-error">JSON 语法错误：{{ jsonValidationError(currentJudgeDraft.rubric_bundle_json) }}</p>
          </div>
          <div class="form-field mt-3">
            <span class="field-label">Packet Policy JSON</span>
            <v-textarea class="code-font" :class="{ 'is-invalid': jsonValidationError(currentJudgeDraft.packet_policy_json) }" v-model="currentJudgeDraft.packet_policy_json" :rows="6" />
            <p v-if="jsonValidationError(currentJudgeDraft.packet_policy_json)" class="validation-error">JSON 语法错误：{{ jsonValidationError(currentJudgeDraft.packet_policy_json) }}</p>
          </div>
          <div class="form-field mt-3">
            <span class="field-label">Probe Appendix JSON</span>
            <v-textarea class="code-font" :class="{ 'is-invalid': jsonValidationError(currentJudgeDraft.probe_appendix_json) }" v-model="currentJudgeDraft.probe_appendix_json" :rows="5" />
            <p v-if="jsonValidationError(currentJudgeDraft.probe_appendix_json)" class="validation-error">JSON 语法错误：{{ jsonValidationError(currentJudgeDraft.probe_appendix_json) }}</p>
          </div>
          <div class="form-field mt-3">
            <span class="field-label">Rubric Source JSON</span>
            <v-textarea class="code-font" :class="{ 'is-invalid': jsonValidationError(currentJudgeDraft.rubric_json) }" v-model="currentJudgeDraft.rubric_json" :rows="6" />
            <p v-if="jsonValidationError(currentJudgeDraft.rubric_json)" class="validation-error">JSON 语法错误：{{ jsonValidationError(currentJudgeDraft.rubric_json) }}</p>
          </div>
          <div class="form-field mt-3">
            <span class="field-label">Output Schema JSON</span>
            <v-textarea class="code-font" :class="{ 'is-invalid': jsonValidationError(currentJudgeDraft.output_schema_json) }" v-model="currentJudgeDraft.output_schema_json" :rows="6" />
            <p v-if="jsonValidationError(currentJudgeDraft.output_schema_json)" class="validation-error">JSON 语法错误：{{ jsonValidationError(currentJudgeDraft.output_schema_json) }}</p>
          </div>
        </div>
      </details>
    </div>
  </div>
</template>

<style scoped>
.judge-config-panel {
  background: var(--color-surface, #ffffff);
  border: 1px solid var(--color-border, #e5e7eb);
  border-radius: var(--radius-lg, 12px);
  overflow: hidden;
}

.judge-cta {
  display: flex;
  gap: 12px;
  align-items: center;
  flex-wrap: wrap;
}

.judge-panel__body {
  padding: 24px;
}

.form-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  flex: 1;
  margin-bottom: 16px;
}

.field-label {
  font-size: 13px;
  font-weight: 500;
  color: var(--color-text-subdued, #6b7280);
}

.form-row {
  display: flex;
  gap: 16px;
  margin-bottom: 16px;
}
.form-row.triple {
  grid-template-columns: repeat(3, 1fr);
  display: grid;
}

.status-banner { padding: 16px; border-radius: var(--radius-md, 8px); border: 1px solid; }
.status-banner.is-ready { background: color-mix(in srgb, var(--theme--success, #10b981) 5%, var(--color-surface, #ffffff)); border-color: color-mix(in srgb, var(--theme--success) 30%, var(--color-border, #e5e7eb)); }
.status-banner.is-warning { background: color-mix(in srgb, var(--theme--warning, #f59e0b) 5%, var(--color-surface, #ffffff)); border-color: color-mix(in srgb, var(--theme--warning) 30%, var(--color-border, #e5e7eb)); }

.action-buttons { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }

.detail-card {
  border: 1px solid var(--color-border, #e5e7eb);
  border-radius: var(--radius-md, 8px);
  background: var(--color-surface, #ffffff);
  overflow: hidden;
}
.detail-card summary {
  padding: 12px 16px;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  background: var(--color-surface-subdued, #f9fafb);
  transition: background-color 0.15s;
}
.detail-card summary:hover {
  background: var(--theme--background-subdued, #f3f4f6);
}
.detail-card[open] summary {
  background: var(--theme--background-subdued, #f3f4f6);
}
.detail-card--compact summary {
  font-size: 12px;
}
.detail-content { padding: 16px; border-top: 1px solid var(--color-border, #e5e7eb); }

.code-font { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 13px; }

.validation-error {
  margin: -8px 0 8px;
  font-size: 12px;
  color: var(--theme--danger, #dc2626);
  line-height: 1.45;
}

:deep(.is-invalid textarea) {
  border-color: var(--theme--danger, #dc2626) !important;
}

.divider { margin: 0 6px; color: var(--color-border, #e5e7eb); }

.block-hint {
  font-size: 12px;
  color: var(--color-text-subdued, #6b7280);
  line-height: 1.5;
  margin: -8px 0 8px;
}

.mt-1 { margin-top: 4px; }
.mt-3 { margin-top: 12px; }
.mb-3 { margin-bottom: 12px; }
.text-sm { font-size: 13px; }

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

.quick-action-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  border-radius: var(--radius-md);
  background: color-mix(in srgb, var(--theme--success, #10b981) 5%, var(--color-surface));
  border: 1px solid color-mix(in srgb, var(--theme--success, #10b981) 20%, var(--color-border));
  margin-bottom: 16px;
}
.quick-action-hint {
  font-size: 12px;
  color: var(--color-text-subdued);
}

.step-indicator {
  display: flex;
  align-items: center;
  gap: 0;
  margin-bottom: 20px;
  padding: 12px 16px;
  background: var(--color-surface-subdued);
  border-radius: var(--radius-md);
}
.step {
  display: flex;
  align-items: center;
  gap: 8px;
}
.step-number {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  font-size: 12px;
  font-weight: 600;
  background: var(--color-border);
  color: var(--color-text-subdued);
}
.step.is-active .step-number {
  background: var(--color-primary);
  color: var(--color-primary-text, #fff);
}
.step.is-done .step-number {
  background: var(--theme--success, #10b981);
  color: #fff;
}
.step-label {
  font-size: 13px;
  font-weight: 500;
  color: var(--color-text-subdued);
}
.step.is-active .step-label {
  color: var(--color-text);
}
.step-connector {
  flex: 1;
  height: 2px;
  margin: 0 12px;
  background: var(--color-border);
}
.step-connector.is-done {
  background: var(--theme--success, #10b981);
}
</style>
