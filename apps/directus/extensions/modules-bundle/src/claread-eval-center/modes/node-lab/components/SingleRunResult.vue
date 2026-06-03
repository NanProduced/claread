<script setup>
import { computed, ref, watch } from "vue";
import JsonTreeView from "../../../components/JsonTreeView.vue";
import NodeProbeOutputView from "../../../components/NodeProbeOutputView.vue";
import ResultBlock from "../../../components/ResultBlock.vue";
import XmlPromptViewer from "../../../components/XmlPromptViewer.vue";
import { useNodeLabState } from "../composables/useNodeLabState";
import { useNodeLabApi } from "../composables/useNodeLabApi";
import {
  statusLabel,
  formatDurationMs,
  formatRuntimeTokens,
  formatClockTime,
  compactFactRows,
  quickValidationLabel,
  resultIssue,
  buildPromptPacketSections,
  parseNestedJson,
  isStructuredJsonValue,
  formatJson,
} from "../composables/useNodeLabFormatting";

const { singleRunResult, singleRunUiState, state, loading } = useNodeLabState();
const { saveSingleRunToHistory } = useNodeLabApi();

const showRefreshBanner = ref(true);

const singleRunRefreshState = computed(() => {
  const uiState = singleRunUiState.value || {};
  const hasResult = Boolean(singleRunResult.value?.run);
  if (loading.run && hasResult) {
    return {
      active: true,
      mode: "refreshing",
      title: "正在刷新本次结果",
      detail: `${uiState.requestLabel || "本次运行"} 已发出请求。当前先保留上一轮结果供参考，完成后会自动替换。`,
    };
  }
  if (loading.run) {
    return {
      active: true,
      mode: "loading",
      title: "正在生成首条结果",
      detail: `${uiState.requestLabel || "本次运行"} 正在执行，结果返回后会显示在右侧。`,
    };
  }
  if (hasResult && uiState.lastCompletedAt) {
    return {
      active: true,
      mode: "updated",
      title: "结果已更新",
      detail: `最近一次完成于 ${formatClockTime(uiState.lastCompletedAt)}。如果内容看起来没有变化，也代表这次运行已经完成。`,
    };
  }
  return {
    active: false,
    mode: "idle",
    title: "",
    detail: "",
  };
});

watch(() => singleRunRefreshState.value?.mode, (mode) => {
  if (mode === 'updated') {
    showRefreshBanner.value = true;
    setTimeout(() => { showRefreshBanner.value = false; }, 4000);
  } else if (mode) {
    showRefreshBanner.value = true;
  }
});

const singleRunSummaryFacts = computed(() => {
  const result = singleRunResult.value?.run;
  if (!result) return [];
  const facts = [
    ["参与者", result.participant_label === "baseline" ? "Baseline" : "Candidate"],
    ["状态", statusLabel(result.status)],
    ["模型", result.model_identity?.model_name || "未记录"],
    ["Few-shot", result.example_summary?.selection_mode || "未记录"],
    ["Prompt Snapshot", result.prompt_identity?.prompt_snapshot_hash || "baseline"],
    ["延迟", formatDurationMs(result.runtime_summary?.latency_ms)],
    ["Tokens", formatRuntimeTokens(result.runtime_summary)],
  ];
  return compactFactRows(facts);
});

const singleRunGrammarValidation = computed(() => {
  if (state.activeNode !== "grammar") return null;
  return singleRunResult.value?.run?.quick_validation || null;
});

const singleRunIssue = computed(() => resultIssue(singleRunResult.value?.run));
</script>

<template>
  <template v-if="state.activeWorkspace === 'single_run'">
    <div
      v-if="singleRunRefreshState.active && showRefreshBanner"
      class="refresh-banner"
      :class="`is-${singleRunRefreshState.mode}`"
      role="status"
    >
      <div class="refresh-banner__title">
        <span v-if="singleRunRefreshState.mode === 'refreshing' || singleRunRefreshState.mode === 'loading'" class="refresh-spinner" aria-hidden="true"></span>
        <strong>{{ singleRunRefreshState.title }}</strong>
      </div>
      <p>{{ singleRunRefreshState.detail }}</p>
    </div>
    <div v-if="singleRunResult?.run">
      <div
        class="single-run-surface"
        :class="{ 'is-stale': loading.run && singleRunResult?.run }"
      >
        <div class="single-run-actions">
          <v-button small secondary :disabled="loading.saveRunHistory" @click="saveSingleRunToHistory">
            {{ loading.saveRunHistory ? "保存中..." : "保存到 Run History" }}
          </v-button>
        </div>
        <div class="meta-grid">
          <div class="meta-item" v-for="[label, value] in singleRunSummaryFacts" :key="label">
            <span class="meta-label">{{ label }}</span>
            <span class="meta-value">{{ value }}</span>
          </div>
        </div>
        <div
          v-if="singleRunIssue"
          class="execution-alert mt-4"
          :class="`is-${singleRunIssue.tone}`"
          role="alert"
        >
          <div class="execution-alert__header">
            <strong>{{ singleRunIssue.title }}</strong>
            <span class="badge badge-sm" :class="`badge-${singleRunIssue.tone}`">{{ statusLabel(singleRunResult.run.status) }}</span>
          </div>
          <p>{{ singleRunIssue.detail }}</p>
        </div>
        <div class="output-block mt-4">
          <div class="output-block__header">
            <h4 class="block-title">结构化输出</h4>
            <div
              v-if="singleRunGrammarValidation"
              class="validation-summary"
              :class="`is-${singleRunGrammarValidation.status === 'pass' ? 'success' : singleRunGrammarValidation.status === 'warning' ? 'warning' : 'danger'}`"
            >
              <strong>Grammar 快速校验</strong>
              <span>{{ quickValidationLabel(singleRunGrammarValidation) }}</span>
            </div>
          </div>
          <p
            v-if="singleRunGrammarValidation?.status === 'warning'"
            class="validation-hint"
          >
            这次输出里有锚点或拆解块需要人工复看。先看原句高亮，再决定是否信任这条解释。
          </p>
          <NodeProbeOutputView
            :node-name="state.activeNode"
            :output="singleRunResult.run.node_output || null"
            :prepared-sentences="singleRunResult.run.prepared_sentences || []"
            :quick-validation="singleRunResult.run.quick_validation || null"
            empty-text="当前没有结构化输出。"
          />
        </div>
        <div class="details-group mt-4">
          <ResultBlock
            v-if="singleRunIssue"
            title="调试信息"
            :open="true"
          >
            <div class="packet-list">
              <div class="packet-item">
                <div class="packet-title">运行失败摘要</div>
                <div class="packet-content">
                  <ul class="insight-list">
                    <li><strong>状态：</strong>{{ statusLabel(singleRunResult.run.status) }}</li>
                    <li><strong>错误码：</strong>{{ singleRunResult.run.error?.code || "未记录" }}</li>
                    <li><strong>错误信息：</strong>{{ singleRunResult.run.error?.message || "未记录" }}</li>
                    <li><strong>Trace Request：</strong>{{ singleRunResult.run.trace_refs?.request_id || "未记录" }}</li>
                    <li><strong>耗时：</strong>{{ formatDurationMs(singleRunResult.run.runtime_summary?.latency_ms) }}</li>
                  </ul>
                </div>
              </div>
              <div class="packet-item">
                <div class="packet-title">调试原始信息</div>
                <JsonTreeView :value="parseNestedJson(singleRunIssue.debug)" empty-text="暂无调试信息。" />
              </div>
            </div>
          </ResultBlock>
          <ResultBlock title="发送给模型的内容" :open="false">
            <div class="packet-list">
              <div class="packet-item" v-for="section in buildPromptPacketSections(singleRunResult?.run)" :key="section.key">
                <div class="packet-title">{{ section.title }}</div>
                <JsonTreeView
                  v-if="isStructuredJsonValue(parseNestedJson(section.value))"
                  :value="parseNestedJson(section.value)"
                  :empty-text="`${section.title} 暂无数据。`"
                />
                <XmlPromptViewer v-else-if="section.key === 'runtime_prompt'" :text="String(section.value || '')" />
                <pre v-else class="packet-content">{{ formatJson(section.value) }}</pre>
              </div>
            </div>
          </ResultBlock>
          <ResultBlock title="完整结果 JSON" :open="false">
            <JsonTreeView :value="parseNestedJson(singleRunResult)" empty-text="暂无结果 JSON。" />
          </ResultBlock>
        </div>
      </div>
    </div>
    <div v-else class="empty-state">
      <p>暂无执行结果</p>
      <span class="empty-hint">请在左侧点击"运行"以查看输出。</span>
    </div>
  </template>
</template>

<style scoped>
.refresh-banner {
  margin-bottom: 16px;
  padding: 14px 16px;
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border);
  background: var(--color-surface-subdued);
}

.refresh-banner.is-refreshing,
.refresh-banner.is-loading {
  border-color: color-mix(in srgb, var(--theme--warning, #f59e0b) 30%, var(--color-border));
  background: color-mix(in srgb, var(--theme--warning, #f59e0b) 7%, var(--color-surface));
}

.refresh-banner.is-updated {
  border-color: color-mix(in srgb, var(--theme--success, #10b981) 30%, var(--color-border));
  background: color-mix(in srgb, var(--theme--success, #10b981) 6%, var(--color-surface));
}

.refresh-banner__title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  margin-bottom: 6px;
}

.refresh-banner p {
  margin: 0;
  font-size: 13px;
  color: var(--color-text-subdued);
  line-height: 1.5;
}

.refresh-spinner {
  width: 12px;
  height: 12px;
  border-radius: 999px;
  border: 2px solid color-mix(in srgb, var(--theme--warning, #f59e0b) 28%, transparent);
  border-top-color: var(--theme--warning, #f59e0b);
  animation: node-lab-spin 0.8s linear infinite;
}

.single-run-surface {
  position: relative;
  transition: opacity 120ms ease;
}

.single-run-surface.is-stale {
  opacity: 0.62;
}

.single-run-actions {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 12px;
}

.meta-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 16px;
  padding: 16px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.meta-grid .meta-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.meta-label {
  font-size: 12px;
  color: var(--color-text-subdued);
  font-weight: 500;
}

.meta-value {
  font-size: 14px;
  font-weight: 500;
}

.execution-alert {
  padding: 14px 16px;
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border);
  background: var(--color-surface-subdued);
}

.execution-alert.is-warning {
  border-color: color-mix(in srgb, var(--theme--warning, #f59e0b) 32%, var(--color-border));
  background: color-mix(in srgb, var(--theme--warning, #f59e0b) 7%, var(--color-surface));
}

.execution-alert.is-danger {
  border-color: color-mix(in srgb, var(--theme--danger, #dc2626) 32%, var(--color-border));
  background: color-mix(in srgb, var(--theme--danger, #dc2626) 7%, var(--color-surface));
}

.execution-alert__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 6px;
}

.execution-alert p {
  margin: 0;
  font-size: 13px;
  line-height: 1.55;
  color: var(--color-text-subdued);
}

.badge {
  display: inline-flex;
  padding: 2px 8px;
  font-size: 12px;
  font-weight: 500;
  border-radius: 999px;
  background: var(--color-surface-subdued);
  border: 1px solid var(--color-border);
}

.badge-sm {
  padding: 1px 6px;
  font-size: 11px;
}

.badge-warning {
  border-color: color-mix(in srgb, #d97706 45%, var(--color-border));
  color: #b45309;
}

.badge-danger {
  border-color: color-mix(in srgb, var(--theme--danger, #dc2626) 45%, var(--color-border));
  color: var(--theme--danger, #dc2626);
}

.output-block__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.block-title {
  font-size: 14px;
  font-weight: 600;
  margin-bottom: 12px;
}

.output-block__header .block-title {
  margin-bottom: 0;
}

.validation-summary {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 28px;
  padding: 0 10px;
  border-radius: 999px;
  border: 1px solid var(--color-border);
  background: var(--color-surface-subdued);
  font-size: 12px;
  white-space: nowrap;
}

.validation-summary strong {
  font-weight: 600;
}

.validation-summary.is-success {
  color: var(--theme--success, #10b981);
  border-color: color-mix(in srgb, var(--theme--success, #10b981) 35%, var(--color-border));
  background: color-mix(in srgb, var(--theme--success, #10b981) 7%, var(--color-surface));
}

.validation-summary.is-warning {
  color: var(--theme--warning, #f59e0b);
  border-color: color-mix(in srgb, var(--theme--warning, #f59e0b) 35%, var(--color-border));
  background: color-mix(in srgb, var(--theme--warning, #f59e0b) 7%, var(--color-surface));
}

.validation-summary.is-danger {
  color: var(--theme--danger, #dc2626);
  border-color: color-mix(in srgb, var(--theme--danger, #dc2626) 35%, var(--color-border));
  background: color-mix(in srgb, var(--theme--danger, #dc2626) 7%, var(--color-surface));
}

.validation-hint {
  margin: 0 0 12px;
  font-size: 13px;
  line-height: 1.55;
  color: var(--color-text-subdued);
}

.details-group {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.packet-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.packet-item {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  overflow: hidden;
}

.packet-title {
  font-size: 12px;
  font-weight: 600;
  padding: 8px 12px;
  background: var(--color-surface-subdued);
  border-bottom: 1px solid var(--color-border);
}

.packet-content {
  padding: 12px;
}

.insight-list {
  list-style: none;
  padding: 0;
  margin: 0;
}

.insight-list li {
  padding: 4px 0;
  font-size: 13px;
  line-height: 1.55;
}

pre {
  margin: 0;
  white-space: pre-wrap;
  font-family: ui-monospace, monospace;
  font-size: 12px;
  color: var(--color-text-subdued);
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 32px 16px;
  text-align: center;
  border: 1px dashed var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-subdued);
  color: var(--color-text-subdued);
}

.empty-hint {
  font-size: 13px;
  margin-top: 4px;
}

.mt-4 { margin-top: 16px; }

@keyframes node-lab-spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
</style>
