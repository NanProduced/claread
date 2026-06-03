<script setup>
import { computed } from "vue";
import JsonTreeView from "../../../components/JsonTreeView.vue";

const props = defineProps({
  result: { type: Object, default: null },
  loading: { type: Boolean, default: false },
});

const warnings = computed(() => Array.isArray(props.result?.warnings) ? props.result.warnings : []);

function dash(value) {
  return value === null || value === undefined || value === "" ? "-" : value;
}
</script>

<template>
  <section class="single-run-result">
    <div v-if="loading" class="empty">Workflow 正在运行...</div>
    <div v-else-if="!result" class="empty">运行一条文章后，这里会显示 workflow 输出、prompt identity 和 render scene。</div>
    <template v-else>
      <header>
        <div>
          <p>Single Run Result</p>
          <h2>{{ result.status || "unknown" }}</h2>
        </div>
        <span :class="result.status">{{ result.status }}</span>
      </header>

      <div class="meta-grid">
        <div><dt>Prompt Variant</dt><dd>{{ dash(result.prompt_identity?.prompt_variant_id) }}</dd></div>
        <div><dt>Snapshot</dt><dd>{{ dash(result.prompt_identity?.prompt_snapshot_hash) }}</dd></div>
        <div><dt>Model</dt><dd>{{ dash(result.model_identity?.profile_name || result.model_identity?.model_name) }}</dd></div>
        <div><dt>Latency</dt><dd>{{ dash(result.runtime_summary?.latency_ms) }} ms</dd></div>
      </div>

      <section v-if="result.error" class="error-box">
        <strong>{{ result.error.code }}</strong>
        <p>{{ result.error.message }}</p>
      </section>

      <section v-if="warnings.length" class="warnings">
        <h3>Warnings</h3>
        <ul>
          <li v-for="(warning, index) in warnings" :key="`warning-${index}`">
            {{ warning.code || warning.level || "warning" }}: {{ warning.message || JSON.stringify(warning) }}
          </li>
        </ul>
      </section>

      <details open>
        <summary>Render Scene JSON</summary>
        <JsonTreeView :value="result.render_scene || {}" label="render_scene" />
      </details>

      <details>
        <summary>完整结果</summary>
        <JsonTreeView :value="result" label="workflow_single_run" />
      </details>
    </template>
  </section>
</template>

<style scoped>
.single-run-result {
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  background: var(--theme--background);
  margin-top: 14px;
  padding: 16px;
}
header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}
header p,
dt,
.empty {
  margin: 0;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  font-weight: 700;
}
header h2 {
  margin: 2px 0 0;
  font-size: 18px;
}
header span {
  border: 1px solid var(--theme--border-color);
  border-radius: 999px;
  padding: 4px 8px;
  font-size: 12px;
  font-weight: 700;
}
header span.succeeded {
  border-color: var(--theme--success);
}
header span.failed,
header span.timeout {
  border-color: var(--theme--danger);
}
.meta-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 1px;
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  margin-top: 14px;
  overflow: hidden;
}
.meta-grid div {
  min-width: 0;
  background: var(--theme--background-subdued);
  padding: 9px;
}
dd {
  margin: 4px 0 0;
  overflow-wrap: anywhere;
}
.error-box,
.warnings {
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  margin-top: 14px;
  padding: 10px;
}
.error-box {
  background: var(--theme--danger-background);
}
.warnings h3 {
  margin: 0 0 8px;
  font-size: 14px;
}
ul {
  margin: 0;
  padding-left: 18px;
}
details {
  border-top: 1px solid var(--theme--border-color);
  margin-top: 14px;
  padding-top: 12px;
}
summary {
  cursor: pointer;
  font-weight: 700;
}
@media (max-width: 900px) {
  .meta-grid {
    grid-template-columns: 1fr;
  }
}
</style>
