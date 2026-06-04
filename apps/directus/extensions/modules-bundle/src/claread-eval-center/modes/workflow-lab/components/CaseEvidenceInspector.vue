<script setup>
import { computed } from "vue";
import SentenceCompareDiffView from "./SentenceCompareDiffView.vue";
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
</script>

<template>
  <aside class="inspector">
    <header>
      <p>证据查看</p>
      <h2>{{ headerCaseId }}</h2>
    </header>

    <div v-if="loading" class="empty">正在读取证据...</div>
    <div v-else-if="!hasCompare && !props.artifact" class="empty">选择 case 后，这里会展示双侧或单侧句子级证据。</div>

    <template v-else-if="hasCompare">
      <p v-if="compareMissingMessage" class="compare-missing">{{ compareMissingMessage }}</p>

      <SentenceCompareDiffView
        :baseline-artifact="baselineArtifact"
        :candidate-artifact="candidateArtifact"
        :prepared-sentences="preparedSentences"
        :compare-case="compareCase"
        empty-text="请先在左侧选择一条 baseline run 与候选 run，并打开具体 case 加载证据。"
      />

      <section v-if="compareCase" class="auxiliary">
        <header><strong>Deterministic 信号</strong><small>辅助参考</small></header>
        <dl class="signal-grid">
          <div>
            <dt>结论</dt>
            <dd>{{ dash(compareCase?.verdict, "未生成对比") }}</dd>
          </div>
          <div>
            <dt>Baseline 硬/软</dt>
            <dd>{{ `${compareCase.baseline_hard_failures ?? 0}/${compareCase.baseline_soft_failures ?? 0}` }}</dd>
          </div>
          <div>
            <dt>候选 硬/软</dt>
            <dd>{{ `${compareCase.candidate_hard_failures ?? 0}/${compareCase.candidate_soft_failures ?? 0}` }}</dd>
          </div>
        </dl>
        <p v-if="compareCase?.reasons?.length" class="reasons">{{ compareCase.reasons.join("; ") }}</p>
      </section>

      <details class="legacy-split">
        <summary>调试 / 原始结构查看（仅在需要排查 artifact shape 时展开）</summary>
        <p class="debug-tip">主视图已经按句子显示差异；这里只保留原始 artifact 的结构入口，避免和主证据区重复。</p>
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
        empty-text="当前 case 没有可展示证据。"
      />
      <details v-if="props.artifact" class="artifact-section">
        <summary>调试 / 原始结构查看</summary>
        <WorkflowArtifactScene
          :payload="props.artifact"
          title="Case 证据"
          empty-text="当前 case 没有可展示证据。"
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
