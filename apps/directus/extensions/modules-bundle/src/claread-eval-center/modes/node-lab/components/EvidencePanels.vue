<script setup>
import { computed, ref } from "vue";
import JsonTreeView from "../../../components/JsonTreeView.vue";
import XmlPromptViewer from "../../../components/XmlPromptViewer.vue";
import ResultBlock from "../../../components/ResultBlock.vue";
import NodeProbeOutputView from "../../../components/NodeProbeOutputView.vue";
import { useNodeLabState } from "../composables/useNodeLabState";
import { useNodeLabApi } from "../composables/useNodeLabApi";
import {
  statusLabel,
  statusTone,
  shortId,
} from "../../../composables/useEvalFormatting";
import {
  formatDurationMs,
  formatRuntimeTokens,
  compareDeltaTone,
  formatSignedDelta,
  formatClockTime,
  safeJsonParse,
  isStructuredJsonValue,
  quickValidationLabel,
  resultIssue,
  parseNestedJson,
  judgeRequestResultMode,
  judgeAggregatePassRateText,
  judgeItemResultLabel,
  judgeRequestIssue,
  judgeStepRunFacts,
  statusBadgeLabel,
  buildPromptPacketSections,
  formatJson,
} from "../composables/useNodeLabFormatting";

const {
  evidenceExpanded,
  compareResult,
  state,
  selectedJudgeRequestId,
  selectedJudgeRequestDetail,
  activeCompareTrial,
  currentJudgeRequests,
  comparePanelTab,
  loading,
} = useNodeLabState();

const debugTab = ref('summary');

const {
  loadJudgeRequestDetail,
  cancelJudgeRequest,
  retryJudgeRequest,
  executeJudgeRequest,
} = useNodeLabApi();

const activeCompareJudgeRequests = computed(() => {
  if (!activeCompareTrial.value?.trial_id) return [];
  return currentJudgeRequests.value || [];
});

const comparePromptSections = computed(() => {
  const baselineSections = buildPromptPacketSections(compareResult.value?.baseline);
  const candidateSections = buildPromptPacketSections(compareResult.value?.candidate);
  const orderedKeys = [];
  const sectionMap = new Map();

  for (const section of [...baselineSections, ...candidateSections]) {
    if (!sectionMap.has(section.key)) {
      sectionMap.set(section.key, {
        key: section.key,
        title: section.title,
        baseline: "未记录",
        candidate: "未记录",
      });
      orderedKeys.push(section.key);
    }
  }

  for (const section of baselineSections) {
    sectionMap.get(section.key).baseline = section.value;
  }

  for (const section of candidateSections) {
    sectionMap.get(section.key).candidate = section.value;
  }

  return orderedKeys.map((key) => sectionMap.get(key));
});

const judgeRequestSummaryFacts = computed(() => {
  const detail = selectedJudgeRequestDetail.value;
  if (!detail?.request) return [];
  const result = detail.result || {};
  return [
    ["请求状态", statusLabel(detail.request.status)],
    ["Judge 方法", detail.request.judge_method || detail.request.judge_config_snapshot_json?.judge_method || "未记录"],
    ["Preset", detail.request.judge_config_snapshot_json?.preset_id || "未记录"],
    ["Trial", shortId(detail.request.trial_id)],
    ["更新时间", formatClockTime(detail.request.date_updated || detail.request.finished_at || detail.request.started_at)],
  ].filter((row) => row[1] !== undefined);
});

const judgeStepRuns = computed(() => {
  const detail = selectedJudgeRequestDetail.value;
  if (!detail?.request) return [];
  const steps = [];
  const req = detail.request;
  if (req.rubric_step) {
    steps.push({ key: "rubric", label: "Rubric 评分", value: req.rubric_step });
  }
  if (req.pairwise_step) {
    steps.push({ key: "pairwise", label: "Pairwise 对比", value: req.pairwise_step });
  }
  if (req.probe_step) {
    steps.push({ key: "probe", label: "Probe 检测", value: req.probe_step });
  }
  return steps;
});
</script>

<template>
  <div class="evidence-panels">
    <!-- Prompt Packet Panel -->
    <ResultBlock title="Prompt Packet 对比" :open="false">
      <div class="compare-prompt-stack">
        <section
          v-for="section in comparePromptSections"
          :key="section.key"
          class="compare-prompt-row"
        >
          <header class="compare-prompt-row__title">
            <h4>{{ section.title }}</h4>
          </header>
          <div class="compare-row__body compare-prompt-grid">
            <div class="compare-column">
              <div class="compare-column__header">
                <h5>Baseline</h5>
              </div>
              <div class="packet-item">
                <JsonTreeView
                  v-if="isStructuredJsonValue(parseNestedJson(section.baseline))"
                  :value="parseNestedJson(section.baseline)"
                  :empty-text="`${section.title} 暂无数据。`"
                />
                <XmlPromptViewer v-else-if="section.key === 'runtime_prompt'" :text="String(section.baseline || '')" />
                <pre v-else class="packet-content">{{ formatJson(section.baseline) }}</pre>
              </div>
            </div>
            <div class="compare-column">
              <div class="compare-column__header">
                <h5>Candidate</h5>
              </div>
              <div class="packet-item">
                <JsonTreeView
                  v-if="isStructuredJsonValue(parseNestedJson(section.candidate))"
                  :value="parseNestedJson(section.candidate)"
                  :empty-text="`${section.title} 暂无数据。`"
                />
                <XmlPromptViewer v-else-if="section.key === 'runtime_prompt'" :text="String(section.candidate || '')" />
                <pre v-else class="packet-content">{{ formatJson(section.candidate) }}</pre>
              </div>
            </div>
          </div>
        </section>
      </div>
    </ResultBlock>

    <ResultBlock
      v-if="resultIssue(compareResult?.baseline, 'Baseline') || resultIssue(compareResult?.candidate, 'Candidate')"
      title="Compare 调试信息"
      :open="!!resultIssue(compareResult?.baseline, 'Baseline') || !!resultIssue(compareResult?.candidate, 'Candidate')"
    >
      <div class="debug-tabs">
        <button class="debug-tab" :class="{ active: debugTab === 'summary' }" @click="debugTab = 'summary'">摘要</button>
        <button class="debug-tab" :class="{ active: debugTab === 'raw' }" @click="debugTab = 'raw'">完整 JSON</button>
      </div>
      <div v-if="debugTab === 'summary'">
        <div class="packet-list">
          <div v-if="resultIssue(compareResult?.baseline, 'Baseline')" class="packet-item">
            <div class="packet-title">Baseline 调试信息</div>
            <JsonTreeView :value="parseNestedJson(resultIssue(compareResult?.baseline, 'Baseline').debug)" empty-text="暂无调试信息。" />
          </div>
          <div v-if="resultIssue(compareResult?.candidate, 'Candidate')" class="packet-item">
            <div class="packet-title">Candidate 调试信息</div>
            <JsonTreeView :value="parseNestedJson(resultIssue(compareResult?.candidate, 'Candidate').debug)" empty-text="暂无调试信息。" />
          </div>
        </div>
      </div>
      <div v-else>
        <JsonTreeView :value="parseNestedJson(compareResult)" empty-text="暂无 Compare 结果 JSON。" />
      </div>
    </ResultBlock>

    <!-- Judge Results Panel -->
    <div v-if="activeCompareJudgeRequests.length" class="output-block mt-4">
      <h4 class="block-title">Judge Requests</h4>
      <div class="request-list mt-3">
        <button
          class="request-item request-item--interactive"
          :class="{ active: selectedJudgeRequestId === item.judge_request_id }"
          v-for="item in activeCompareJudgeRequests"
          :key="item.judge_request_id"
          @click="loadJudgeRequestDetail(item.judge_request_id)"
        >
          <div class="request-main">
            <span class="request-id">{{ item.judge_request_id }}</span>
            <span class="request-meta">Trial {{ shortId(item.trial_id) }}<template v-if="item.preset_id"> · {{ item.preset_id }}</template></span>
          </div>
          <div class="request-side">
            <span class="badge">{{ statusLabel(item.status) }}</span>
          </div>
        </button>
      </div>
    </div>
    <div v-else class="empty-state compact mt-4">
      <p>当前 Compare 还没有 Judge Request</p>
      <span class="empty-hint">Judge tab 只显示这条 Compare 的评审记录，不再混入当前 node 的历史 requests。</span>
    </div>

    <div v-if="loading.executeJudge && !selectedJudgeRequestDetail" class="judge-loading" role="status">
      <div class="loading-spinner-sm"></div>
      <span>正在加载 Judge 请求...</span>
    </div>
    <div v-if="selectedJudgeRequestDetail?.request" class="output-block mt-4">
      <h4 class="block-title">Judge 结果详情</h4>
      <div class="meta-grid mt-3">
        <div class="meta-item" v-for="[label, value] in judgeRequestSummaryFacts" :key="label">
          <span class="meta-label">{{ label }}</span>
          <span class="meta-value">{{ value }}</span>
        </div>
      </div>

      <div class="action-buttons mt-3">
        <v-button
          secondary
          small
          v-if="['queued', 'running'].includes(selectedJudgeRequestDetail.request.status)"
          @click="cancelJudgeRequest(selectedJudgeRequestDetail.request.judge_request_id)"
        >
          取消 Request
        </v-button>
        <v-button
          secondary
          small
          v-if="['queued'].includes(selectedJudgeRequestDetail.request.status)"
          :disabled="loading?.executeJudge"
          @click="executeJudgeRequest(selectedJudgeRequestDetail.request.judge_request_id)"
        >
          执行这条 Request
        </v-button>
        <v-button
          secondary
          small
          v-if="['failed', 'cancelled'].includes(selectedJudgeRequestDetail.request.status)"
          @click="retryJudgeRequest(selectedJudgeRequestDetail.request.judge_request_id)"
        >
          重新排队
        </v-button>
      </div>

      <div v-if="judgeStepRuns.length" class="compare-overview mt-4">
        <article
          v-for="step in judgeStepRuns"
          :key="step.key"
          class="compare-status-card"
          :class="`is-${statusTone(step.value?.status)}`"
        >
          <div class="compare-status-card__header">
            <h4>{{ step.label }}</h4>
            <span class="badge" :class="`badge-${statusTone(step.value?.status)}`">{{ statusLabel(step.value?.status) }}</span>
          </div>
          <div class="compare-status-card__facts">
            <div class="status-fact" v-for="[label, value] in judgeStepRunFacts(step.value)" :key="`${step.key}-${label}`">
              <span class="meta-label">{{ label }}</span>
              <span class="meta-value">{{ value }}</span>
            </div>
          </div>
          <p v-if="step.value?.error?.message" class="text-sm mt-2 text-danger">
            {{ step.value.error.message }}
          </p>
        </article>
      </div>

      <div v-if="judgeRequestIssue(selectedJudgeRequestDetail)" class="execution-alert is-danger mt-4">
        <div class="execution-alert__header">
          <strong>Judge 执行失败</strong>
          <span class="badge badge-danger">{{ judgeRequestIssue(selectedJudgeRequestDetail).code }}</span>
        </div>
        <p>{{ judgeRequestIssue(selectedJudgeRequestDetail).message }}</p>
        <p class="text-sm mt-1">如果下面已有 rubric / pairwise / probe 结果，说明这是部分失败，可继续参考已成功部分。</p>
      </div>

      <div v-if="judgeRequestResultMode(selectedJudgeRequestDetail) === 'rubric'" class="compare-overview mt-4">
        <article
          v-for="side in [
            { key: 'baseline', title: 'Baseline', value: selectedJudgeRequestDetail.result.rubric_scoring_result?.baseline },
            { key: 'candidate', title: 'Candidate', value: selectedJudgeRequestDetail.result.rubric_scoring_result?.candidate },
          ]"
          :key="side.key"
          class="compare-status-card is-neutral"
        >
          <div class="compare-status-card__header">
            <h4>{{ side.title }}</h4>
            <span class="badge badge-neutral">Judge</span>
          </div>
          <div class="compare-status-card__facts">
            <div class="status-fact">
              <span class="meta-label">条目数</span>
              <span class="meta-value">{{ side.value?.aggregate?.item_count ?? 0 }}</span>
            </div>
            <div class="status-fact">
              <span class="meta-label">通过</span>
              <span class="meta-value">{{ side.value?.aggregate?.passed ?? 0 }}</span>
            </div>
            <div class="status-fact">
              <span class="meta-label">部分通过</span>
              <span class="meta-value">{{ side.value?.aggregate?.partial ?? 0 }}</span>
            </div>
            <div class="status-fact">
              <span class="meta-label">失败</span>
              <span class="meta-value">{{ side.value?.aggregate?.failed ?? 0 }}</span>
            </div>
            <div class="status-fact">
              <span class="meta-label">得分率</span>
              <span class="meta-value">{{ judgeAggregatePassRateText(side.value) }}</span>
              <span v-if="side.value?.aggregate?.partial > 0" class="meta-hint">三值加权</span>
            </div>
          </div>
        </article>
      </div>

      <div
        v-if="selectedJudgeRequestDetail.result?.pairwise_result?.pairwise_review"
        class="status-banner is-ready mt-4"
      >
        <strong>Pairwise 整体评估倾向：{{ selectedJudgeRequestDetail.result.pairwise_result.pairwise_review.preferred_side }}</strong>
        <p class="text-sm mt-1">{{ selectedJudgeRequestDetail.result.pairwise_result.pairwise_review.overall_judgment }}</p>
      </div>

      <div
        v-if="selectedJudgeRequestDetail.result?.probe_appendix_result"
        class="status-banner is-warning mt-4"
      >
        <strong>专项 Probe：{{ selectedJudgeRequestDetail.result.probe_appendix_result.probe_type }}</strong>
        <p class="text-sm mt-1">{{ selectedJudgeRequestDetail.result.probe_appendix_result.summary || "请展开下方问题列表查看细节。" }}</p>
      </div>

      <div class="details-group mt-4">
        <ResultBlock
          v-if="selectedJudgeRequestDetail.result?.probe_appendix_result"
          title="Probe Findings"
          :open="true"
        >
          <div class="packet-list">
            <div
              v-for="question in selectedJudgeRequestDetail.result.probe_appendix_result.questions || []"
              :key="question.question_id"
              class="packet-item"
            >
              <div class="packet-title">
                {{ question.question_id }}
                <span class="badge badge-sm" :class="question.detected ? 'badge-warning' : 'badge-success'">
                  {{ question.detected ? "发现问题" : "未发现" }}
                </span>
              </div>
              <p class="packet-content">{{ question.description }}</p>
              <ul v-if="question.evidence?.length" class="insight-list mt-2">
                <li v-for="(evidence, index) in question.evidence" :key="`${question.question_id}-${index}`">{{ evidence }}</li>
              </ul>
            </div>
          </div>
        </ResultBlock>

        <ResultBlock title="Judge Artifact JSON" :open="false">
          <JsonTreeView :value="parseNestedJson(selectedJudgeRequestDetail)" empty-text="暂无 Judge 结果。" />
        </ResultBlock>
      </div>
    </div>
  </div>
</template>

<style scoped>
.evidence-panels {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.debug-tabs {
  display: flex;
  gap: 4px;
  margin-bottom: 12px;
  border-bottom: 1px solid var(--color-border);
  padding-bottom: 8px;
}
.debug-tab {
  padding: 4px 12px;
  font-size: 13px;
  font-weight: 500;
  color: var(--color-text-subdued);
  background: none;
  border: 1px solid transparent;
  border-radius: var(--radius-sm, 6px);
  cursor: pointer;
}
.debug-tab.active {
  color: var(--color-primary);
  background: color-mix(in srgb, var(--color-primary) 8%, var(--color-surface));
  border-color: color-mix(in srgb, var(--color-primary) 25%, var(--color-border));
}

.evidence-panel {
  border: 1px solid var(--color-border, #e5e7eb);
  border-radius: var(--radius-lg, 12px);
  overflow: hidden;
  background: var(--color-surface, #ffffff);
}

.runtime-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.runtime-table th,
.runtime-table td {
  padding: 8px 12px;
  text-align: left;
  border-bottom: 1px solid var(--color-border, #e5e7eb);
}

.runtime-table th {
  font-weight: 600;
  color: var(--color-text-subdued, #6b7280);
  background: var(--color-surface-subdued, #f9fafb);
}

.compare-prompt-stack {
  display: grid;
  gap: 16px;
}

.compare-prompt-row {
  border: 1px solid var(--color-border, #e5e7eb);
  border-radius: var(--radius-lg, 12px);
  background: var(--color-surface, #ffffff);
  overflow: hidden;
}

.compare-prompt-row__title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-bottom: 1px solid var(--color-border-subdued, #e5e7eb);
  background: var(--color-surface-subdued, #f9fafb);
}

.compare-prompt-row__title h4 {
  margin: 0;
  font-size: 13px;
  font-weight: 700;
  color: var(--color-text-subdued, #6b7280);
}

.compare-prompt-grid {
  padding: 0;
}

.compare-column {
  min-width: 0;
  display: grid;
  gap: 12px;
}

.compare-column__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.compare-column__header h5 {
  margin: 0;
  font-size: 13px;
  font-weight: 600;
  color: var(--color-text-subdued, #6b7280);
}

.packet-list { display: flex; flex-direction: column; gap: 12px; }
.packet-item { border: 1px solid var(--color-border, #e5e7eb); border-radius: var(--radius-md, 8px); overflow: hidden; }
.packet-title { font-size: 12px; font-weight: 600; padding: 8px 12px; background: var(--color-surface-subdued, #f9fafb); border-bottom: 1px solid var(--color-border, #e5e7eb); }
.packet-content { padding: 12px; }

pre { margin: 0; white-space: pre-wrap; font-family: ui-monospace, monospace; font-size: 12px; color: var(--color-text-subdued, #6b7280); }

.meta-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 16px;
  padding: 16px;
  background: var(--color-surface, #ffffff);
  border: 1px solid var(--color-border, #e5e7eb);
  border-radius: var(--radius-md, 8px);
  margin-bottom: 16px;
}
.meta-grid .meta-item { display: flex; flex-direction: column; gap: 4px; }
.meta-label { font-size: 13px; color: var(--color-text-subdued, #6b7280); font-weight: 500; }
.meta-value { font-size: 14px; font-weight: 500; }
.meta-hint { font-size: 11px; color: var(--color-text-subdued, #6b7280); margin-left: 4px; font-weight: 400; }

.compare-split { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.compare-pane { border: 1px solid var(--color-border, #e5e7eb); border-radius: var(--radius-md, 8px); padding: 16px; }
.pane-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.pane-header h4 { font-size: 14px; font-weight: 500; }

.compare-overview {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  margin-bottom: 16px;
}

.compare-status-card {
  border: 1px solid var(--color-border, #e5e7eb);
  border-radius: var(--radius-lg, 12px);
  padding: 18px 18px 16px;
  background: var(--color-surface, #ffffff);
}

.compare-status-card__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
}

.compare-status-card__header h4 {
  font-size: 15px;
  font-weight: 600;
}

.compare-status-card__facts {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px 18px;
}

.status-fact {
  display: grid;
  gap: 4px;
}

.compare-status-card.is-success {
  border-color: color-mix(in srgb, var(--theme--success, #10b981) 24%, var(--color-border, #e5e7eb));
}

.compare-status-card.is-warning {
  border-color: color-mix(in srgb, var(--theme--warning, #f59e0b) 24%, var(--color-border, #e5e7eb));
}

.compare-status-card.is-danger {
  border-color: color-mix(in srgb, var(--theme--danger, #dc2626) 24%, var(--color-border, #e5e7eb));
}

.compare-status-card.is-neutral {
  /* no special border */
}

.execution-alert {
  padding: 14px 16px;
  border-radius: var(--radius-md, 8px);
  border: 1px solid var(--color-border, #e5e7eb);
  background: var(--color-surface-subdued, #f9fafb);
}

.execution-alert.is-danger {
  border-color: color-mix(in srgb, var(--theme--danger, #dc2626) 32%, var(--color-border, #e5e7eb));
  background: color-mix(in srgb, var(--theme--danger, #dc2626) 7%, var(--color-surface, #ffffff));
}

.execution-alert__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 6px;
}

.execution-alert p {
  margin: 0;
  font-size: 13px;
  line-height: 1.55;
  color: var(--color-text-subdued, #6b7280);
}

.status-banner { padding: 16px; border-radius: var(--radius-md, 8px); border: 1px solid; }
.status-banner.is-ready { background: color-mix(in srgb, var(--theme--success, #10b981) 5%, var(--color-surface, #ffffff)); border-color: color-mix(in srgb, var(--theme--success) 30%, var(--color-border, #e5e7eb)); }
.status-banner.is-warning { background: color-mix(in srgb, var(--theme--warning, #f59e0b) 5%, var(--color-surface, #ffffff)); border-color: color-mix(in srgb, var(--theme--warning) 30%, var(--color-border, #e5e7eb)); }

.badge {
  display: inline-flex;
  padding: 2px 8px;
  font-size: 12px;
  font-weight: 500;
  border-radius: 999px;
  background: var(--color-surface-subdued, #f9fafb);
  border: 1px solid var(--color-border, #e5e7eb);
}
.badge-sm { padding: 1px 6px; font-size: 11px; }
.badge-success { border-color: color-mix(in srgb, var(--theme--success, #10b981) 45%, var(--color-border, #e5e7eb)); color: var(--theme--success, #10b981); }
.badge-warning { border-color: color-mix(in srgb, #d97706 45%, var(--color-border, #e5e7eb)); color: #b45309; }
.badge-danger { border-color: color-mix(in srgb, var(--theme--danger, #dc2626) 45%, var(--color-border, #e5e7eb)); color: var(--theme--danger, #dc2626); }
.badge-neutral { color: var(--color-text-subdued, #6b7280); }
.badge-active { background: color-mix(in srgb, var(--color-primary, #2563eb) 10%, var(--color-surface, #ffffff)); border-color: var(--color-primary, #2563eb); color: var(--color-primary, #2563eb); }

.request-list { display: flex; flex-direction: column; gap: 8px; }
.request-item { display: flex; justify-content: space-between; padding: 12px; border: 1px solid var(--color-border, #e5e7eb); border-radius: var(--radius-md, 8px); }
.request-item--interactive { text-align: left; width: 100%; }
.request-item--interactive:hover { border-color: color-mix(in srgb, var(--color-primary, #2563eb) 25%, var(--color-border, #e5e7eb)); background: color-mix(in srgb, var(--color-primary, #2563eb) 4%, var(--color-surface, #ffffff)); }
.request-item--interactive.active { border-color: color-mix(in srgb, var(--color-primary, #2563eb) 40%, var(--color-border, #e5e7eb)); background: color-mix(in srgb, var(--color-primary, #2563eb) 6%, var(--color-surface, #ffffff)); }
.request-main { display: flex; flex-direction: column; gap: 4px; }
.request-id { font-weight: 500; font-size: 14px; }
.request-meta { font-size: 12px; color: var(--color-text-subdued, #6b7280); }
.request-side { display: flex; align-items: center; }

.output-block { margin-top: 16px; }
.block-title { font-size: 14px; font-weight: 600; margin-bottom: 12px; }

.details-group { display: flex; flex-direction: column; gap: 8px; }

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 32px 16px;
  text-align: center;
  border: 1px dashed var(--color-border, #e5e7eb);
  border-radius: var(--radius-md, 8px);
  background: var(--color-surface-subdued, #f9fafb);
  color: var(--color-text-subdued, #6b7280);
}
.empty-state.compact { padding: 20px 12px; }
.empty-hint { font-size: 13px; margin-top: 4px; }

.insight-list { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 6px; }
.insight-list li { font-size: 13px; line-height: 1.55; display: flex; align-items: flex-start; gap: 8px; }

.rubric-indicator {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 700;
  flex-shrink: 0;
  margin-top: 1px;
}
.rubric-indicator.is-pass {
  background: color-mix(in srgb, var(--theme--success, #10b981) 15%, var(--color-surface));
  color: var(--theme--success, #10b981);
}
.rubric-indicator.is-partial {
  background: color-mix(in srgb, var(--theme--warning, #f59e0b) 16%, var(--color-surface));
  color: #b45309;
}
.rubric-indicator.is-fail {
  background: color-mix(in srgb, var(--theme--danger, #dc2626) 15%, var(--color-surface));
  color: var(--theme--danger, #dc2626);
}

.action-buttons { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }

.mt-1 { margin-top: 4px; }
.mt-2 { margin-top: 8px; }
.mt-3 { margin-top: 12px; }
.mt-4 { margin-top: 16px; }
.mb-4 { margin-bottom: 16px; }
.text-sm { font-size: 13px; }
.text-danger { color: var(--theme--danger, #dc2626); }
.text-muted { color: var(--color-text-subdued, #6b7280); }

.compare-row__body {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  padding: 16px;
}

.judge-loading {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 16px;
  color: var(--color-text-subdued);
  font-size: 13px;
}

.loading-spinner-sm {
  width: 16px;
  height: 16px;
  border: 2px solid var(--color-border);
  border-top-color: var(--color-primary);
  border-radius: 50%;
  animation: node-lab-spin 0.8s linear infinite;
}

@keyframes node-lab-spin {
  to { transform: rotate(360deg); }
}
</style>
