<script setup>
import { computed, ref } from "vue";
import AbCompareMode from "./modes/AbCompareMode.vue";
import JudgeMode from "./modes/JudgeMode.vue";
import NodeLabMode from "./modes/NodeLabMode.vue";
import PromptVariantMode from "./modes/PromptVariantMode.vue";
import RunHistoryMode from "./modes/RunHistoryMode.vue";
import WorkflowEvalMode from "./modes/WorkflowEvalMode.vue";
import PlaceholderPanel from "./components/PlaceholderPanel.vue";

const modes = [
  {
    id: "node-lab",
    label: "Node Lab",
    kicker: "Node Lab",
    description: "单 node 的 prompt、few-shot、模型实验工作台，包含 Single Run、Baseline Compare、Judge Compare 和 Sessions。",
    ready: true,
    questions: ["当前 baseline 与 candidate 的差异是什么？", "这个 node 的 prompt、few-shot、模型调整是否更好？", "这轮 Session 下的 trial 和 judge 结论是否可追溯？"],
  },
  {
    id: "workflow-eval",
    label: "Workflow 评测",
    kicker: "Workflow Eval",
    description: "生成 eval run config 并在终端执行。历史 run 回看请使用运行历史模式。",
    ready: true,
    questions: ["整条 workflow 是否稳定完成？", "render scene 是否可渲染且信息完整？", "prompt 或 few-shot 改动是否带来整体提升？"],
  },
  {
    id: "ab-compare",
    label: "A/B 对比",
    kicker: "A/B Report",
    description: "比较 baseline 与 candidate run，输出成对差异和回归风险。",
    ready: true,
    questions: ["candidate 是否比 baseline 更好？", "差异来自 prompt、模型还是样本输入？", "是否存在明显回归 case？"],
  },
  {
    id: "prompt-variants",
    label: "Prompt Variant",
    kicker: "Prompt Lab",
    description: "管理 eval-only prompt variant draft、manifest 和导出。",
    ready: true,
    questions: ["有哪些候选 prompt variant？", "variant 是否可复现？", "是否可以导出到文件型 eval harness？"],
  },
  {
    id: "run-history",
    label: "运行历史",
    kicker: "Run History",
    description: "回看 evals/ run artifacts、报告和人工观察记录。",
    ready: true,
    questions: ["历史 run 存在哪里？", "某次运行使用了什么模型和 prompt？", "哪些 case 需要复查或补入数据集？"],
  },
  {
    id: "llm-judge",
    label: "LLM Judge",
    kicker: "LLM-as-a-Judge",
    description: "创建 judge request，查看 judge worker 状态和 report artifact。",
    ready: true,
    questions: ["哪些 case 需要模型裁判补充证据？", "judge request 当前执行到哪里？", "judge report 是否支持人工复查结论？"],
  },
  {
    id: "few-shot-rag",
    label: "Few-shot / RAG",
    kicker: "RAG Workbench",
    description: "few-shot candidate、RAG example 验证和 promotion。后置。",
    ready: false,
    questions: ["哪些 example 值得进入候选集？", "检索结果是否污染输出风格？", "何时 promotion 到 fallback baseline？"],
  },
];

const activeMode = ref("node-lab");
const runHistoryInitialRunId = ref("");
const runHistoryInitialSource = ref("workflow");
const runHistoryInitialNodeProbeRunId = ref("");
const abInitialBaselineRunId = ref("");
const abInitialCandidateRunId = ref("");

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

function openAbCompare(selection) {
  if (selection?.baseline_run_id) abInitialBaselineRunId.value = selection.baseline_run_id;
  if (selection?.candidate_run_id) abInitialCandidateRunId.value = selection.candidate_run_id;
  activeMode.value = "ab-compare";
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
      <WorkflowEvalMode v-else-if="activeMode === 'workflow-eval'" @open-run-history="openRunHistory" />
      <AbCompareMode
        v-else-if="activeMode === 'ab-compare'"
        :initial-baseline-run-id="abInitialBaselineRunId"
        :initial-candidate-run-id="abInitialCandidateRunId"
      />
      <PromptVariantMode v-else-if="activeMode === 'prompt-variants'" />
      <RunHistoryMode
        v-else-if="activeMode === 'run-history'"
        :initial-run-id="runHistoryInitialRunId"
        :initial-source="runHistoryInitialSource"
        :initial-node-probe-run-id="runHistoryInitialNodeProbeRunId"
        @compare-run="openAbCompare"
      />
      <JudgeMode v-else-if="activeMode === 'llm-judge'" />
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
  max-width: 760px;
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
