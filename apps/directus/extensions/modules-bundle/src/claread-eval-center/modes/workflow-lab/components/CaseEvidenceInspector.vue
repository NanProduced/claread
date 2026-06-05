<script setup>
import { computed } from "vue";
import SentenceEvidenceView from "./SentenceEvidenceView.vue";
import WorkflowArtifactScene from "./WorkflowArtifactScene.vue";
import { dash, normalizeWorkflowScene } from "../composables/workflowLabFormatting.js";

const props = defineProps({
  artifact: { type: Object, default: null },
  baselineArtifact: { type: Object, default: null },
  candidateArtifact: { type: Object, default: null },
  compareCase: { type: Object, default: null },
  loading: { type: Boolean, default: false },
});

function diagnoseMissing(side) {
  if (!side) return "尚未加载或后端未返回 artifact。";
  if (side.adapter_status === "failed") return "执行失败，未生成可用 artifact。";
  if (side.adapter_status === "timeout") return "执行超时，未生成可用 artifact。";
  return "未发现 artifact。";
}

const headerCaseId = computed(() => {
  return props.artifact?.case_id
    || props.compareCase?.case_id
    || props.baselineArtifact?.case_id
    || props.candidateArtifact?.case_id
    || "未选择 case";
});

const preparedSentences = computed(() => {
  const candidates = [
    props.baselineArtifact?.prepared_sentences,
    props.baselineArtifact?.input_snapshot?.prepared_sentences,
    props.candidateArtifact?.prepared_sentences,
    props.candidateArtifact?.input_snapshot?.prepared_sentences,
    props.artifact?.prepared_sentences,
    props.artifact?.input_snapshot?.prepared_sentences,
  ];
  for (const candidate of candidates) {
    if (Array.isArray(candidate) && candidate.length) return candidate;
  }
  return [];
});

const hasCompare = computed(() => Boolean(props.baselineArtifact || props.candidateArtifact || props.compareCase));
const compareMissingMessage = computed(() => {
  if (!hasCompare.value) return "";
  if (!props.baselineArtifact && !props.candidateArtifact) return "Baseline 与候选两侧的 artifact 都还没加载出来。";
  if (!props.baselineArtifact) return `Baseline 缺失：${diagnoseMissing(props.baselineArtifact)}`;
  if (!props.candidateArtifact) return `候选侧缺失：${diagnoseMissing(props.candidateArtifact)}`;
  return "";
});

function sceneCounts(artifact) {
  const scene = normalizeWorkflowScene(artifact);
  return {
    translations: scene?.translations?.length ?? 0,
    marks: scene?.inline_marks?.length ?? 0,
    entries: scene?.sentence_entries?.length ?? 0,
  };
}

const debugPanels = computed(() => [
  {
    key: "baseline",
    label: "Baseline",
    artifact: props.baselineArtifact,
    status: dash(props.baselineArtifact?.adapter_status, "缺失 artifact"),
    diagnose: diagnoseMissing(props.baselineArtifact),
    counts: sceneCounts(props.baselineArtifact),
  },
  {
    key: "candidate",
    label: "Candidate",
    artifact: props.candidateArtifact,
    status: dash(props.candidateArtifact?.adapter_status, "缺失 artifact"),
    diagnose: diagnoseMissing(props.candidateArtifact),
    counts: sceneCounts(props.candidateArtifact),
  },
]);

const compareSummaryCards = computed(() => {
  if (!props.compareCase) return [];
  return [
    { label: "deterministic 结论", value: dash(props.compareCase?.verdict, "未生成") },
    {
      label: "Baseline 结构/轻微",
      value: `${props.compareCase?.baseline_hard_failures ?? 0}/${props.compareCase?.baseline_soft_failures ?? 0}`,
    },
    {
      label: "候选 结构/轻微",
      value: `${props.compareCase?.candidate_hard_failures ?? 0}/${props.compareCase?.candidate_soft_failures ?? 0}`,
    },
  ];
});
</script>

<template>
  <aside class="inspector">
    <header>
      <p>证据查看</p>
      <h2>{{ headerCaseId }}</h2>
    </header>

    <div v-if="loading" class="empty">正在读取证据...</div>
    <div v-else-if="!hasCompare && !props.artifact" class="empty">选择差异句后，这里会展示双侧或单侧句子级证据。</div>

    <template v-else-if="hasCompare">
      <p v-if="compareMissingMessage" class="compare-missing">{{ compareMissingMessage }}</p>

      <section v-if="compareCase" class="auxiliary">
        <header><strong>证据摘要</strong><small>主视图负责完整句子差异；这里保留差异句元信息与原始 artifact 入口。</small></header>
        <dl class="signal-grid">
          <div v-for="card in compareSummaryCards" :key="card.label">
            <dt>{{ card.label }}</dt>
            <dd>{{ card.value }}</dd>
          </div>
        </dl>
        <p class="reasons">结构失败 = error / timeout / schema 缺失；轻微信号 = warning / drop / degraded_light。这里只是排查线索，不是最终裁决。</p>
        <header><strong>Deterministic 原因</strong><small>来自 compare report</small></header>
        <dl class="signal-grid">
          <div>
            <dt>当前差异句</dt>
            <dd>{{ headerCaseId }}</dd>
          </div>
          <div>
            <dt>Baseline 状态</dt>
            <dd>{{ dash(compareCase?.baseline_status, "—") }}</dd>
          </div>
          <div>
            <dt>候选状态</dt>
            <dd>{{ dash(compareCase?.candidate_status, "—") }}</dd>
          </div>
        </dl>
        <p v-if="compareCase?.reasons?.length" class="reasons">{{ compareCase.reasons.join("; ") }}</p>
      </section>

      <details class="legacy-split">
        <summary>原始 artifact / 调试入口（仅在需要排查 shape 时展开）</summary>
        <p class="debug-tip">左侧主视图已经负责完整句子差异；这里不再重复展示一遍同样内容，只保留原始 artifact 的结构入口。</p>
        <div class="split-view compact">
          <section v-for="panel in debugPanels" :key="panel.key" class="split-pane">
            <div class="pane-head">
              <strong>{{ panel.label }}</strong>
              <span>{{ panel.status }}</span>
            </div>
            <p v-if="!panel.artifact" class="diagnose">{{ panel.diagnose }}</p>
            <template v-else>
              <dl class="debug-counts">
                <div><dt>翻译</dt><dd>{{ panel.counts.translations }}</dd></div>
                <div><dt>标注</dt><dd>{{ panel.counts.marks }}</dd></div>
                <div><dt>条目</dt><dd>{{ panel.counts.entries }}</dd></div>
              </dl>
              <details class="artifact-section">
                <summary>展开原始 artifact section</summary>
                <WorkflowArtifactScene
                  :payload="panel.artifact"
                  :title="`${panel.label} 输出`"
                  :empty-text="`${panel.label} 侧当前没有可展示 artifact。`"
                  :compact="true"
                  :show-debug="false"
                />
              </details>
            </template>
          </section>
        </div>
      </details>
    </template>

    <template v-else>
      <SentenceEvidenceView
        :payload="props.artifact"
        :prepared-sentences="preparedSentences"
        empty-text="当前差异句没有可展示证据。"
      />
      <details v-if="props.artifact" class="artifact-section">
        <summary>调试 / 原始结构查看</summary>
                <WorkflowArtifactScene
                  :payload="props.artifact"
                  title="差异句证据"
                  empty-text="当前差异句没有可展示证据。"
                  :compact="true"
                  :show-debug="true"
                />
      </details>
    </template>
  </aside>
</template>

<style scoped>
.inspector {
  display: grid;
  gap: 14px;
  border: 1px solid var(--theme--border-color);
  border-radius: 10px;
  background: var(--theme--background);
  min-height: 320px;
  padding: 14px;
}

header p,
dt,
.empty,
.reasons,
.diagnose,
.auxiliary small {
  margin: 0;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
}

header h2 {
  margin: 2px 0 0;
  font-size: 16px;
  overflow-wrap: anywhere;
}

.auxiliary {
  border: 1px solid var(--theme--border-color-subdued, var(--theme--border-color));
  border-radius: 8px;
  background: var(--theme--background-subdued);
  padding: 12px 14px;
  display: grid;
  gap: 8px;
}

.compare-missing {
  margin: 0;
  border: 1px dashed var(--theme--border-color-subdued, var(--theme--border-color));
  border-radius: 6px;
  padding: 10px 12px;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  line-height: 1.55;
}

.auxiliary header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}

.signal-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 1px;
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  overflow: hidden;
}

.signal-grid div {
  background: var(--theme--background);
  padding: 8px 10px;
}

.signal-grid dd {
  margin: 4px 0 0;
  font-size: 14px;
  font-weight: 700;
  overflow-wrap: anywhere;
}

.reasons {
  font-weight: 400;
  line-height: 1.55;
  background: var(--theme--background);
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  padding: 8px 10px;
}

.legacy-split summary {
  cursor: pointer;
  color: var(--theme--foreground-subdued);
  font-size: 13px;
  font-weight: 700;
  padding: 6px 0;
}

.debug-tip {
  margin: 8px 0 0;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  line-height: 1.55;
}

.split-view {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
  margin-top: 8px;
}

.split-view.compact {
  align-items: start;
}

.split-pane {
  display: grid;
  gap: 10px;
  min-width: 0;
}

.pane-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.pane-head span {
  color: var(--theme--foreground-subdued);
  font-size: 11px;
}

.diagnose {
  border: 1px dashed var(--theme--border-color-subdued, var(--theme--border-color));
  border-radius: 6px;
  padding: 10px 12px;
  font-weight: 400;
  line-height: 1.55;
}

.debug-counts {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 1px;
  border: 1px solid var(--theme--border-color-subdued, var(--theme--border-color));
  border-radius: 6px;
  overflow: hidden;
}

.debug-counts div {
  background: var(--theme--background);
  padding: 8px 10px;
}

.debug-counts dt {
  color: var(--theme--foreground-subdued);
  font-size: 11px;
  font-weight: 700;
}

.debug-counts dd {
  margin: 4px 0 0;
  font-size: 14px;
  font-weight: 700;
}

.artifact-section {
  border: 1px solid var(--theme--border-color-subdued, var(--theme--border-color));
  border-radius: 6px;
  background: var(--theme--background-subdued);
  padding: 6px 10px;
}

.artifact-section summary {
  cursor: pointer;
  color: var(--theme--foreground-subdued);
  font-size: 11px;
  font-weight: 700;
  padding: 4px 0;
}

@media (max-width: 1200px) {
  .signal-grid,
  .split-view {
    grid-template-columns: 1fr;
  }
}
</style>
