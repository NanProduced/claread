<script setup>
import { computed } from "vue";
import JsonTreeView from "../../../components/JsonTreeView.vue";
import { useNodeLabState } from "../composables/useNodeLabState";
import { HELP_TEXT } from "../composables/useNodeLabConstants";
import { compactFactRows, parseNestedJson } from "../composables/useNodeLabFormatting";

const { baselineConfig, loading } = useNodeLabState();

const baselineSummaryFacts = computed(() => {
  const baseline = baselineConfig.value;
  if (!baseline) return [];
  return compactFactRows([
    ["Prompt Profile", baseline.prompt_profile || "未记录"],
    ["Policy Focus", baseline.policy_focus || "未记录"],
    ["Baseline Model", baseline.baseline_model_profile || "未记录"],
    ["Few-shot 来源", Array.isArray(baseline.baseline_examples) && baseline.baseline_examples.length > 0 ? `本地 examples（${baseline.baseline_examples.length} 条）` : "无本地 examples"],
  ]);
});
</script>

<template>
  <section class="panel-section section-readonly">
    <div class="section-header">
      <h3 class="section-title">Baseline 参考</h3>
      <div class="header-actions">
        <span class="badge badge-readonly">Read-only</span>
        <span class="help-icon" tabindex="0" :title="HELP_TEXT.baseline_snapshot">?</span>
      </div>
    </div>
    <div v-if="loading.baseline" class="skeleton-grid">
      <div class="skeleton-item" v-for="i in 4" :key="i">
        <div class="skeleton-line skeleton-line--short"></div>
        <div class="skeleton-line"></div>
      </div>
    </div>
    <div v-else-if="!baselineConfig" class="empty-state" role="status">
      <p>Baseline 配置尚未加载</p>
      <span class="empty-hint">选择节点和阅读目标后将自动加载 Baseline 配置。</span>
    </div>
    <template v-else>
    <div class="meta-grid">
      <div class="meta-item" v-for="[label, value] in baselineSummaryFacts" :key="label">
        <span class="meta-label">{{ label }}</span>
        <span class="meta-value">{{ value }}</span>
      </div>
    </div>
    <div class="details-group">
      <details class="detail-card">
        <summary>Baseline 说明文本</summary>
        <div class="detail-content"><pre>{{ baselineConfig.agent_instructions }}</pre></div>
      </details>
      <details class="detail-card">
        <summary>Baseline Policy</summary>
        <div class="detail-content">
          <ul class="policy-list">
            <li v-for="(line, index) in baselineConfig.policy_lines" :key="`baseline-policy-${index}`">{{ line }}</li>
          </ul>
        </div>
      </details>
      <details class="detail-card">
        <summary>Baseline Examples</summary>
        <div class="detail-content">
          <JsonTreeView :value="parseNestedJson(baselineConfig.baseline_examples || [])" empty-text="暂无 baseline examples。" />
        </div>
      </details>
    </div>
    </template>
  </section>
</template>

<style scoped>
.panel-section {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: 24px;
}

.section-readonly {
  background: var(--color-surface-subdued);
  border-style: dashed;
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

.header-actions {
  display: flex;
  gap: 8px;
  align-items: center;
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

.badge-readonly {
  background: transparent;
  border-style: dashed;
  color: var(--color-text-subdued);
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

.details-group {
  display: flex;
  flex-direction: column;
  gap: 8px;
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
  transition: background-color 0.15s;
}
.detail-card summary:hover {
  background: var(--theme--background-subdued);
}
.detail-card[open] summary {
  background: var(--theme--background-subdued);
}

.detail-content {
  padding: 16px;
  border-top: 1px solid var(--color-border);
}

pre {
  margin: 0;
  white-space: pre-wrap;
  font-family: var(--theme--fonts--monospace--font-family, monospace);
  font-size: 12px;
  color: var(--color-text-subdued);
}

.policy-list {
  list-style: none;
  padding: 0;
  margin: 0;
}

.policy-list li {
  padding: 6px 0;
  font-size: 13px;
  line-height: 1.55;
  border-bottom: 1px solid var(--color-border);
}

.policy-list li:last-child {
  border-bottom: none;
}

.skeleton-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 16px;
  padding: 16px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  margin-bottom: 16px;
}
.skeleton-item {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.skeleton-line {
  height: 14px;
  border-radius: 4px;
  background: linear-gradient(90deg, var(--color-surface-subdued) 25%, color-mix(in srgb, var(--color-surface-subdued) 70%, var(--color-surface)) 50%, var(--color-surface-subdued) 75%);
  background-size: 200% 100%;
  animation: skeleton-shimmer 1.5s ease-in-out infinite;
}
.skeleton-line--short {
  height: 10px;
  width: 60%;
}
@keyframes skeleton-shimmer {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 24px 16px;
  text-align: center;
  border: 1px dashed var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-subdued);
  color: var(--color-text-subdued);
}
.empty-hint { font-size: 13px; margin-top: 4px; }
</style>
