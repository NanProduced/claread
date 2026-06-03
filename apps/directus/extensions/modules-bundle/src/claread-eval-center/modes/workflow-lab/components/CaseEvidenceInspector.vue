<script setup>
import WorkflowArtifactScene from "./WorkflowArtifactScene.vue";
import { dash } from "../composables/workflowLabFormatting.js";

defineProps({
  artifact: { type: Object, default: null },
  baselineArtifact: { type: Object, default: null },
  candidateArtifact: { type: Object, default: null },
  compareCase: { type: Object, default: null },
  loading: { type: Boolean, default: false },
});
</script>

<template>
  <aside class="inspector">
    <header>
      <p>证据查看</p>
      <h2>{{ artifact?.case_id || compareCase?.case_id || baselineArtifact?.case_id || candidateArtifact?.case_id || "未选择 case" }}</h2>
    </header>

    <div v-if="loading" class="empty">正在读取证据...</div>
    <div v-else-if="!artifact && !baselineArtifact && !candidateArtifact && !compareCase" class="empty">选择 case 后，这里会展示单侧或双侧证据。</div>
    <template v-else-if="baselineArtifact || candidateArtifact || compareCase">
      <section class="compare-summary">
        <div>
          <dt>结论</dt>
          <dd>{{ dash(compareCase?.verdict, "未生成差异报告") }}</dd>
        </div>
        <div>
          <dt>Baseline 硬/软</dt>
          <dd>{{ compareCase ? `${compareCase.baseline_hard_failures}/${compareCase.baseline_soft_failures}` : "-" }}</dd>
        </div>
        <div>
          <dt>Candidate 硬/软</dt>
          <dd>{{ compareCase ? `${compareCase.candidate_hard_failures}/${compareCase.candidate_soft_failures}` : "-" }}</dd>
        </div>
      </section>

      <section v-if="compareCase?.reasons?.length" class="reason-list">
        <strong>为什么会得到这个结论</strong>
        <ul>
          <li v-for="reason in compareCase.reasons" :key="reason">{{ reason }}</li>
        </ul>
      </section>

      <div class="split-view">
        <section class="split-pane">
          <div class="pane-head">
            <strong>Baseline</strong>
            <span>{{ dash(baselineArtifact?.adapter_status, "缺失 artifact") }}</span>
          </div>
          <WorkflowArtifactScene
            :payload="baselineArtifact"
            title="Baseline 输出"
            empty-text="Baseline 侧当前没有可展示 artifact。"
            :compact="true"
            :show-debug="false"
          />
        </section>

        <section class="split-pane">
          <div class="pane-head">
            <strong>Candidate</strong>
            <span>{{ dash(candidateArtifact?.adapter_status, "缺失 artifact") }}</span>
          </div>
          <WorkflowArtifactScene
            :payload="candidateArtifact"
            title="Candidate 输出"
            empty-text="Candidate 侧当前没有可展示 artifact。"
            :compact="true"
            :show-debug="false"
          />
        </section>
      </div>
    </template>
    <template v-else>
      <WorkflowArtifactScene
        :payload="artifact"
        title="Case 证据"
        empty-text="当前 case 没有可展示证据。"
        :compact="true"
        :show-debug="true"
      />
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
.reason-list li {
  margin: 0;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
}

header h2 {
  margin: 2px 0 0;
  font-size: 16px;
  overflow-wrap: anywhere;
}

.compare-summary {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 1px;
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
  overflow: hidden;
}

.compare-summary div {
  background: var(--theme--background-subdued);
  padding: 10px;
}

dd {
  margin: 4px 0 0;
  font-size: 14px;
  font-weight: 700;
}

.reason-list {
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
  padding: 12px;
  background: var(--theme--background-subdued);
}

.reason-list ul {
  margin: 8px 0 0;
  padding-left: 18px;
}

.split-view {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
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

@media (max-width: 1200px) {
  .compare-summary,
  .split-view {
    grid-template-columns: 1fr;
  }
}
</style>
