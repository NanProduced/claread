<script setup>
import { computed } from "vue";
import ResultBlock from "../../../components/ResultBlock.vue";
import JsonTreeView from "../../../components/JsonTreeView.vue";
import WorkflowArtifactScene from "./WorkflowArtifactScene.vue";
import { dash, normalizeSingleRunPayload } from "../composables/workflowLabFormatting.js";

const props = defineProps({
  result: { type: Object, default: null },
  loading: { type: Boolean, default: false },
});

const normalized = computed(() => normalizeSingleRunPayload(props.result));
const warnings = computed(() => normalized.value.warnings || []);
</script>

<template>
  <section class="single-run-result">
    <div v-if="loading" class="empty">正在验证这篇文章...</div>
    <div v-else-if="!result" class="empty">完成一次单篇验证后，这里会显示结构化结果。它只服务当前调试，不会进入队列或已完成列表。</div>
    <template v-else>
      <header>
        <div>
          <p>单篇验证结果</p>
          <h2>{{ normalized.status }}</h2>
        </div>
        <span :class="normalized.status">{{ normalized.status }}</span>
      </header>

      <div class="notice">
        这是临时调试结果，不进入运行队列，也不会出现在已完成 runs 列表。
      </div>

      <div class="meta-grid">
        <div><dt>候选版本</dt><dd>{{ dash(normalized.promptIdentity?.prompt_variant_id, "baseline") }}</dd></div>
        <div><dt>Snapshot</dt><dd>{{ dash(normalized.promptIdentity?.prompt_snapshot_hash) }}</dd></div>
        <div><dt>模型方案</dt><dd>{{ dash(normalized.modelIdentity?.profile_name || normalized.modelIdentity?.model_name) }}</dd></div>
        <div><dt>耗时</dt><dd>{{ dash(normalized.runtimeSummary?.latency_ms) }} ms</dd></div>
        <div><dt>提醒</dt><dd>{{ warnings.length }}</dd></div>
        <div><dt>输出状态</dt><dd>{{ dash(normalized.scene?.user_facing_state) }}</dd></div>
      </div>

      <section v-if="normalized.error" class="error-box">
        <strong>{{ normalized.error.code || "workflow_error" }}</strong>
        <p>{{ normalized.error.message || "单篇验证执行失败。" }}</p>
      </section>

      <WorkflowArtifactScene
        :payload="normalized.scene || normalized.raw"
        title="Workflow 输出"
        empty-text="当前没有可展示的 workflow 输出。"
      />

      <ResultBlock title="完整响应 JSON" :open="false">
        <JsonTreeView :value="result" label="workflow_single_run" />
      </ResultBlock>
    </template>
  </section>
</template>

<style scoped>
.single-run-result {
  container-type: inline-size;
  display: grid;
  gap: 14px;
  border: 1px solid var(--theme--border-color);
  border-radius: 10px;
  background: var(--theme--background);
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

header > div {
  flex: 1 1 auto;
  min-width: 0;
}

header span {
  flex: 0 0 auto;
  align-self: flex-start;
  border: 1px solid var(--theme--border-color);
  border-radius: 999px;
  padding: 4px 8px;
  font-size: 12px;
  font-weight: 700;
  white-space: nowrap;
}

header span.succeeded {
  border-color: var(--theme--success);
  background: var(--theme--success-background);
}

header span.failed,
header span.timeout {
  border-color: var(--theme--danger);
  background: var(--theme--danger-background);
}

.notice {
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
  padding: 10px 12px;
  background: var(--theme--background-subdued);
  color: var(--theme--foreground-subdued);
  font-size: 13px;
  line-height: 1.55;
}

.meta-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 1px;
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
  overflow: hidden;
}

.meta-grid div {
  min-width: 0;
  background: var(--theme--background-subdued);
  padding: 10px;
}

dd {
  margin: 4px 0 0;
  overflow-wrap: anywhere;
}

.error-box {
  border: 1px solid var(--theme--danger);
  border-radius: 8px;
  padding: 12px;
  background: var(--theme--danger-background);
}

.error-box p {
  margin: 6px 0 0;
}

@container (max-width: 760px) {
  .meta-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@container (max-width: 520px) {
  header {
    display: grid;
  }

  .meta-grid {
    grid-template-columns: 1fr;
  }
}
</style>
