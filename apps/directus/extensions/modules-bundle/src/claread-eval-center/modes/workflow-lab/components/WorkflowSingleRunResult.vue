<script setup>
import { computed } from "vue";
import ResultBlock from "../../../components/ResultBlock.vue";
import JsonTreeView from "../../../components/JsonTreeView.vue";
import WorkflowSentenceNotebook from "./WorkflowSentenceNotebook.vue";
import { dash, normalizeSingleRunPayload } from "../composables/workflowLabFormatting.js";

const props = defineProps({
  result: { type: Object, default: null },
  loading: { type: Boolean, default: false },
  savingHistory: { type: Boolean, default: false },
});
const emit = defineEmits(["go-to-dataset-runs", "save-run-history", "open-run-history"]);

const normalized = computed(() => normalizeSingleRunPayload(props.result));
const warnings = computed(() => normalized.value.warnings || []);
const runtimeSummary = computed(() => (
  normalized.value.runtimeSummary && typeof normalized.value.runtimeSummary === "object"
    ? normalized.value.runtimeSummary
    : {}
));
const usageAggregate = computed(() => (
  runtimeSummary.value?.aggregate && typeof runtimeSummary.value.aggregate === "object"
    ? runtimeSummary.value.aggregate
    : runtimeSummary.value
));

const succeeded = computed(() => normalized.value.status === "succeeded" || normalized.value.status === "complete");
const savedHistoryRunId = computed(() => normalized.value.savedHistoryRunId || "");

const preparedSentences = computed(() => {
  const raw = props.result;
  if (!raw) return [];
  const candidates = [
    raw.prepared_sentences,
    raw.run?.prepared_sentences,
    raw.output?.article?.sentences,
    raw.scene?.prepared_sentences,
  ];
  for (const candidate of candidates) {
    if (Array.isArray(candidate) && candidate.length) return candidate;
  }
  return [];
});

const sentenceCount = computed(() => preparedSentences.value.length || normalized.value.scene?.article?.sentences?.length || 0);
const lexicalAnnotationCount = computed(() => (
  Array.isArray(normalized.value.scene?.inline_marks)
    ? normalized.value.scene.inline_marks.filter((item) => item?.annotation_type !== "grammar_note").length
    : 0
));
const grammarAnnotationCount = computed(() => {
  const marks = Array.isArray(normalized.value.scene?.inline_marks)
    ? normalized.value.scene.inline_marks.filter((item) => item?.annotation_type === "grammar_note").length
    : 0;
  const entries = Array.isArray(normalized.value.scene?.sentence_entries)
    ? normalized.value.scene.sentence_entries.filter((item) => item?.entry_type === "grammar_note" || item?.entry_type === "sentence_analysis").length
    : 0;
  return marks + entries;
});

const latencySeconds = computed(() => {
  const raw = Number(runtimeSummary.value?.latency_ms);
  if (!Number.isFinite(raw) || raw <= 0) return "—";
  return `${(raw / 1000).toFixed(raw >= 10000 ? 1 : 2)} s`;
});
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
        这是临时验证结果，<strong>不进入运行队列</strong>，也不会出现在已完成 runs 列表。
        如果需要保留这次结果，请手动保存到 <strong>Run History</strong>。通过后建议到「数据集验证」批量跑；失败则回「候选版本」调整。
      </div>

      <div class="history-actions">
        <button type="button" class="ghost-cta" :disabled="savingHistory" @click="emit('save-run-history')">
          {{ savingHistory ? "保存中..." : (savedHistoryRunId ? "已保存到 Run History" : "保存到 Run History") }}
        </button>
        <button
          v-if="savedHistoryRunId"
          type="button"
          class="ghost-cta"
          @click="emit('open-run-history', savedHistoryRunId)"
        >
          在 Run History 中打开
        </button>
      </div>

      <section class="overview-panel">
        <div class="overview-facts">
          <article>
            <dt>候选版本</dt>
            <dd>{{ dash(normalized.promptIdentity?.prompt_variant_id, "baseline") }}</dd>
          </article>
          <article>
            <dt>Snapshot</dt>
            <dd>{{ dash(normalized.promptIdentity?.prompt_snapshot_hash) }}</dd>
          </article>
          <article>
            <dt>模型方案</dt>
            <dd>{{ dash(normalized.modelIdentity?.profile_name || normalized.modelIdentity?.model_name) }}</dd>
          </article>
          <article>
            <dt>耗时</dt>
            <dd>{{ latencySeconds }}</dd>
          </article>
          <article>
            <dt>Tokens</dt>
            <dd>
              {{ dash(usageAggregate?.total_tokens, "—") }}
              <span class="inline-detail">
                Input {{ dash(usageAggregate?.input_tokens, "—") }} / Output {{ dash(usageAggregate?.output_tokens, "—") }}
              </span>
            </dd>
          </article>
          <article>
            <dt>输出状态</dt>
            <dd>{{ dash(normalized.scene?.user_facing_state) }}</dd>
          </article>
        </div>

        <div class="summary-strip">
          <span>句子 {{ sentenceCount }}</span>
          <span>词汇标注 {{ lexicalAnnotationCount }}</span>
          <span>语法标注 {{ grammarAnnotationCount }}</span>
          <span>提醒 {{ warnings.length }}</span>
        </div>
      </section>

      <section v-if="normalized.error" class="error-box">
        <strong>{{ normalized.error.code || "workflow_error" }}</strong>
        <p>{{ normalized.error.message || "单篇验证执行失败。" }}</p>
      </section>

      <WorkflowSentenceNotebook
        :payload="normalized.scene || normalized.raw"
        :prepared-sentences="preparedSentences"
        empty-text="本次单篇验证没有可用的句子级证据。"
      />

      <ResultBlock title="完整响应 JSON" :open="false">
        <JsonTreeView :value="result" label="workflow_single_run" />
      </ResultBlock>

      <section v-if="succeeded" class="next-step-cta" role="region" aria-label="下一步 CTA">
        <div>
          <strong>验证通过?</strong>
          <small>下一步可去「数据集验证」批量跑,得到逐 case 证据;或继续调「候选版本」迭代。</small>
        </div>
        <div class="next-step-cta-actions">
          <button type="button" class="primary-cta" @click="emit('go-to-dataset-runs')">去数据集验证</button>
        </div>
      </section>
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

header > span {
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

.history-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.overview-panel {
  display: grid;
  gap: 12px;
}

.overview-facts {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 1px;
  border: 1px solid var(--theme--border-color);
  border-radius: 10px;
  overflow: hidden;
}

.overview-facts article {
  min-width: 0;
  background: var(--theme--background-subdued);
  padding: 12px;
}

.summary-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.summary-strip span {
  display: inline-flex;
  align-items: center;
  min-height: 28px;
  padding: 0 10px;
  border: 1px solid var(--theme--border-color);
  border-radius: 999px;
  background: var(--theme--background-subdued);
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  font-weight: 700;
}

dd {
  margin: 4px 0 0;
  overflow-wrap: anywhere;
}

.inline-detail {
  display: block;
  margin-top: 4px;
  color: var(--theme--foreground-subdued);
  font-size: 11px;
  font-weight: 400;
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

.next-step-cta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  border: 1px solid color-mix(in srgb, var(--theme--primary) 45%, var(--theme--border-color));
  border-radius: 8px;
  background: color-mix(in srgb, var(--theme--primary) 4%, var(--theme--background));
  padding: 10px 14px;
  position: relative;
}
.next-step-cta::before {
  content: "";
  position: absolute;
  top: 50%;
  left: 12px;
  transform: translateY(-50%);
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--theme--primary);
}
.next-step-cta {
  padding-left: 28px;
}

.next-step-cta strong {
  color: var(--theme--foreground);
}

.next-step-cta small {
  display: block;
  margin-top: 4px;
  color: var(--theme--foreground-subdued);
  font-size: 11px;
  font-weight: 400;
}

.next-step-cta-actions {
  display: flex;
  gap: 8px;
}

.primary-cta {
  background: var(--theme--primary);
  color: var(--theme--primary-foreground, #fff);
  border: 1px solid var(--theme--primary);
  padding: 6px 14px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
}

.ghost-cta {
  background: transparent;
  color: var(--theme--foreground);
  border: 1px solid var(--theme--border-color);
  padding: 6px 14px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
}

@container (max-width: 760px) {
  .overview-facts {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@container (max-width: 520px) {
  header {
    display: grid;
  }

  .overview-facts {
    grid-template-columns: 1fr;
  }
}
</style>
