<script setup>
import { computed, ref } from "vue";
import PlaceholderPanel from "./components/PlaceholderPanel.vue";
import NodeLabMode from "./modes/NodeLabMode.vue";
import RunHistoryMode from "./modes/RunHistoryMode.vue";
import WorkflowLabMode from "./modes/workflow-lab/WorkflowLabMode.vue";

const modes = [
  {
    id: "node-lab",
    label: "Node Lab",
    kicker: "Node Lab",
    description: "单 node 的 prompt、few-shot、模型实验工作台。",
    ready: true,
  },
  {
    id: "workflow-lab",
    label: "Workflow Lab",
    kicker: "Learning Workflow",
    description: "整条 learning workflow 的 candidate、dataset run、compare、judge 和 review 工作台。",
    ready: true,
  },
  {
    id: "run-history",
    label: "运行历史",
    kicker: "Run History",
    description: "浏览已保存的 eval 结果、artifact 和人工 review 记录。",
    ready: true,
  },
  {
    id: "example-lab",
    label: "Example Lab",
    kicker: "Few-shot Examples",
    description: "人工维护 few-shot examples 的后续模块。本轮仅保留占位。",
    ready: false,
  },
];

const activeMode = ref("node-lab");
const runHistoryInitialRunId = ref("");
const runHistoryInitialSource = ref("workflow");
const runHistoryInitialNodeProbeRunId = ref("");
const workflowInitialBaselineRunId = ref("");
const workflowInitialCandidateRunId = ref("");

const currentMode = computed(() => modes.find((item) => item.id === activeMode.value) ?? modes[0]);

function openRunHistory(selection) {
  if (!selection) return;
  if (typeof selection === "string") {
    runHistoryInitialSource.value = "workflow";
    runHistoryInitialRunId.value = selection;
    runHistoryInitialNodeProbeRunId.value = "";
    activeMode.value = "run-history";
    return;
  }
  if (selection.source === "node_probe" && selection.recordId) {
    runHistoryInitialSource.value = "node_probe";
    runHistoryInitialNodeProbeRunId.value = selection.recordId;
    runHistoryInitialRunId.value = "";
    activeMode.value = "run-history";
    return;
  }
  if (!selection.runId) return;
  runHistoryInitialSource.value = "workflow";
  runHistoryInitialRunId.value = selection.runId;
  runHistoryInitialNodeProbeRunId.value = "";
  activeMode.value = "run-history";
}

function openWorkflowCompare(selection) {
  workflowInitialBaselineRunId.value = selection?.baseline_run_id || "";
  workflowInitialCandidateRunId.value = selection?.candidate_run_id || "";
  activeMode.value = "workflow-lab";
}
</script>

<template>
  <private-view title="Eval Center">
    <template #headline>
      Claread Console
    </template>

    <template #navigation>
      <nav class="eval-nav" aria-label="Eval Center modes">
        <div class="eval-nav-label">评测模式</div>
        <button
          v-for="mode in modes"
          :key="mode.id"
          class="eval-nav-item"
          :class="{ 'is-active': activeMode === mode.id }"
          type="button"
          @click="activeMode = mode.id"
        >
          <span>
            <strong>{{ mode.label }}</strong>
            <small>{{ mode.kicker }}</small>
          </span>
          <em>{{ mode.ready ? "可用" : "占位" }}</em>
        </button>
      </nav>
    </template>

    <main class="eval-center">
      <header class="eval-header">
        <div>
          <p class="eyebrow">Eval Center</p>
          <h1>{{ currentMode.label }}</h1>
          <p>{{ currentMode.description }}</p>
        </div>
        <div class="mode-state" :class="{ ready: currentMode.ready }">
          {{ currentMode.ready ? "MVP 可用" : "规划中" }}
        </div>
      </header>

      <NodeLabMode v-if="activeMode === 'node-lab'" @open-run-history="openRunHistory" />
      <WorkflowLabMode
        v-else-if="activeMode === 'workflow-lab'"
        :initial-baseline-run-id="workflowInitialBaselineRunId"
        :initial-candidate-run-id="workflowInitialCandidateRunId"
        @open-run-history="openRunHistory"
      />
      <RunHistoryMode
        v-else-if="activeMode === 'run-history'"
        :initial-run-id="runHistoryInitialRunId"
        :initial-source="runHistoryInitialSource"
        :initial-node-probe-run-id="runHistoryInitialNodeProbeRunId"
        @compare-run="openWorkflowCompare"
      />
      <PlaceholderPanel v-else :mode="currentMode" />
    </main>
  </private-view>
</template>

<style scoped>
.eval-nav {
  padding: 16px 12px;
}

.eval-nav-label {
  margin: 0 0 10px 8px;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  font-weight: 700;
}

.eval-nav-item {
  display: flex;
  width: 100%;
  align-items: center;
  justify-content: space-between;
  border: 0;
  border-radius: 6px;
  background: transparent;
  padding: 10px 8px;
  color: var(--theme--foreground);
  cursor: pointer;
  font: inherit;
  text-align: left;
  transition: background-color 160ms ease, color 160ms ease;
}

.eval-nav-item:hover,
.eval-nav-item.is-active {
  background: var(--theme--background-subdued);
}

.eval-nav-item strong,
.eval-nav-item small {
  display: block;
}

.eval-nav-item small,
.eval-nav-item em {
  color: var(--theme--foreground-subdued);
  font-size: 11px;
  font-style: normal;
  font-weight: 600;
}

.eval-center {
  padding: 24px;
}

.eval-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 24px;
  margin-bottom: 20px;
}

.eval-header > div {
  min-width: 0;
}

.eval-header h1 {
  margin: 0;
  font-size: 24px;
  line-height: 1.25;
}

.eval-header p {
  max-width: 780px;
  margin: 6px 0 0;
  color: var(--theme--foreground-subdued);
  overflow-wrap: anywhere;
}

.eyebrow {
  margin: 0 0 4px !important;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  font-weight: 700;
}

.mode-state {
  flex: 0 0 auto;
  border: 1px solid var(--theme--border-color);
  border-radius: 999px;
  padding: 6px 10px;
  background: var(--theme--background-subdued);
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  font-weight: 700;
}

.mode-state.ready {
  background: var(--theme--success-background);
  color: var(--theme--foreground);
}

@media (max-width: 720px) {
  .eval-center {
    padding: 16px;
  }

  .eval-header {
    flex-direction: column;
    gap: 8px;
  }
}
</style>
