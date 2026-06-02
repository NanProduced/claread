<script setup>
import { computed } from "vue";
import JsonTreeView from "../../../components/JsonTreeView.vue";
import { useNodeLabState } from "../composables/useNodeLabState";
import { HELP_TEXT } from "../composables/useNodeLabConstants";
import { compactFactRows, parseNestedJson } from "../composables/useNodeLabFormatting";

const { baselineConfig } = useNodeLabState();

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
        <span class="help-icon" :title="HELP_TEXT.baseline_snapshot">?</span>
      </div>
    </div>
    <div v-if="baselineConfig" class="meta-grid">
      <div class="meta-item" v-for="[label, value] in baselineSummaryFacts" :key="label">
        <span class="meta-label">{{ label }}</span>
        <span class="meta-value">{{ value }}</span>
      </div>
    </div>
    <div v-if="baselineConfig" class="details-group">
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
  width: 18px;
  height: 18px;
  border-radius: 999px;
  border: 1px solid var(--color-text-subdued);
  color: var(--color-text-subdued);
  font-size: 11px;
  font-weight: 600;
  cursor: help;
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
  font-size: 12px;
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
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  background: var(--color-surface-subdued);
}

.detail-content {
  padding: 16px;
  border-top: 1px solid var(--color-border);
}

pre {
  margin: 0;
  white-space: pre-wrap;
  font-family: ui-monospace, monospace;
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
</style>
