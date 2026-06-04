<script setup>
import { computed } from "vue";
import ResultBlock from "../../../components/ResultBlock.vue";
import JsonTreeView from "../../../components/JsonTreeView.vue";
import WorkflowSentenceNotebook from "./WorkflowSentenceNotebook.vue";
import SentenceCompareDiffView from "./SentenceCompareDiffView.vue";
import { dash } from "../composables/workflowLabFormatting.js";

// 接收单跑 compare 产物
// compareResult = { baseline, candidate, compare, input_snapshot }
// compare = { report, baseline_artifact, candidate_artifact, baseline_run_id, candidate_run_id, input_hash }
const props = defineProps({
  compareResult: { type: Object, default: null },
  loading: { type: Boolean, default: false },
});
const emit = defineEmits([
  "open-compare",
]);

const compare = computed(() => props.compareResult?.compare || null);
const baseline = computed(() => props.compareResult?.baseline || null);
const candidate = computed(() => props.compareResult?.candidate || null);
const report = computed(() => compare.value?.report || null);

const inputSnapshot = computed(() => props.compareResult?.input_snapshot || {});

const baselineArtifact = computed(() => compare.value?.baseline_artifact || baseline.value?.case_artifact || null);
const candidateArtifact = computed(() => compare.value?.candidate_artifact || candidate.value?.case_artifact || null);

const verdict = computed(() => {
  const wins = report.value?.wins || 0;
  const losses = report.value?.losses || 0;
  const ties = report.value?.ties || 0;
  if (wins > losses) return "win";
  if (losses > wins) return "loss";
  if (wins || losses) return "tie";
  return "no_delta";
});

const verdictTone = computed(() => {
  if (verdict.value === "win") return "success";
  if (verdict.value === "loss") return "danger";
  if (verdict.value === "tie") return "warning";
  return "neutral";
});

const verdictLabel = computed(() => {
  if (verdict.value === "win") return "候选更优";
  if (verdict.value === "loss") return "候选更差";
  if (verdict.value === "tie") return "持平";
  return "无 deterministic delta";
});

const comparisons = computed(() => Array.isArray(report.value?.comparisons) ? report.value.comparisons : []);
const firstComparison = computed(() => comparisons.value[0] || null);

const identityWarnings = computed(() => Array.isArray(report.value?.identity_warnings) ? report.value.identity_warnings : []);

function summarizeRun(side) {
  const artifact = side === "baseline" ? baselineArtifact.value : candidateArtifact.value;
  if (!artifact) return null;
  const adapterStatus = artifact.adapter_status || "unknown";
  const latency = Number(artifact.latency_seconds || 0);
  const tokens = artifact.usage_summary?.total_tokens ?? null;
  return {
    adapter_status: adapterStatus,
    latency_seconds: Number.isFinite(latency) ? latency : 0,
    total_tokens: tokens,
    prompt_variant_id: artifact.prompt_identity?.prompt_variant_id || null,
    prompt_snapshot_hash: artifact.prompt_identity?.prompt_snapshot_hash || null,
    profile_name: artifact.model_identity?.profile_name || null,
    model_name: artifact.model_identity?.model_name || null,
    translation_count: Array.isArray(artifact.translations) ? artifact.translations.length : 0,
    inline_mark_count: Array.isArray(artifact.inline_marks) ? artifact.inline_marks.length : 0,
    sentence_entry_count: Array.isArray(artifact.sentence_entries) ? artifact.sentence_entries.length : 0,
  };
}

const baselineSummary = computed(() => summarizeRun("baseline"));
const candidateSummary = computed(() => summarizeRun("candidate"));

const preparedSentences = computed(() => {
  const candidates = [
    baselineArtifact.value?.prepared_sentences,
    baselineArtifact.value?.input_snapshot?.prepared_sentences,
    candidateArtifact.value?.prepared_sentences,
    candidateArtifact.value?.input_snapshot?.prepared_sentences,
  ];
  for (const c of candidates) {
    if (Array.isArray(c) && c.length) return c;
  }
  return [];
});

function sideStatus(side) {
  return side === "baseline"
    ? (baseline.value?.result?.status || baselineArtifact.value?.adapter_status || "unknown")
    : (candidate.value?.result?.status || candidateArtifact.value?.adapter_status || "unknown");
}

function readingGoalLabel(value) {
  return value === "exam" ? "考试阅读" : value === "daily_reading" ? "日常阅读" : value || "—";
}

function readingVariantLabel(value) {
  const map = {
    gaokao: "高考",
    cet: "CET",
    kaoyan: "考研",
    tem: "TEM",
    ielts_toefl: "IELTS / TOEFL",
    beginner_reading: "入门阅读",
    intermediate_reading: "进阶阅读",
    intensive_reading: "精读",
  };
  return map[value] || value || "—";
}

</script>

<template>
  <section class="compare-workspace-result">
    <div v-if="loading" class="empty">正在并发执行 baseline 与 candidate 两次 workflow execution...</div>
    <div v-else-if="!compareResult" class="empty">完成一次单篇双跑后,这里会显示 baseline / candidate 完整执行结果和 deterministic compare 摘要。Workflow Lab 默认会直接物化 compare 记录，不再要求先手动保存 run 历史。</div>
    <template v-else>
      <header class="cw-header">
        <div>
          <p>Compare Workspace</p>
          <h2>单篇 baseline / candidate compare</h2>
        </div>
        <span :class="`verdict-pill is-${verdictTone}`">{{ verdictLabel }}</span>
      </header>

      <section class="input-context">
        <header>
          <strong>输入文章(双跑共享)</strong>
          <small>input_hash {{ compare?.input_hash || "—" }}</small>
        </header>
        <div class="input-grid">
          <div>
            <dt>阅读目标</dt>
            <dd>{{ readingGoalLabel(inputSnapshot.reading_goal) }}</dd>
          </div>
          <div>
            <dt>阅读场景</dt>
            <dd>{{ readingVariantLabel(inputSnapshot.reading_variant) }}</dd>
          </div>
          <div>
            <dt>Case 数</dt>
            <dd>{{ dash(report?.total_cases, "—") }}</dd>
          </div>
        </div>
        <details v-if="inputSnapshot.text" class="text-preview">
          <summary>查看输入文本(共 {{ inputSnapshot.text.length }} 字符)</summary>
          <pre>{{ inputSnapshot.text }}</pre>
        </details>
      </section>

      <section v-if="identityWarnings.length" class="warnings">
        <strong>运行身份提醒</strong>
        <ul>
          <li v-for="(warning, index) in identityWarnings" :key="index">{{ warning }}</li>
        </ul>
      </section>

      <section class="run-grid">
        <article class="run-pane">
          <header>
            <strong>Baseline</strong>
            <span :class="`status-pill is-${sideStatus('baseline') === 'succeeded' ? 'success' : sideStatus('baseline') === 'failed' ? 'danger' : 'neutral'}`">{{ sideStatus("baseline") }}</span>
          </header>
          <dl>
            <div><dt>候选版本</dt><dd>{{ dash(baselineSummary?.prompt_variant_id, "baseline default") }}</dd></div>
            <div><dt>Snapshot</dt><dd>{{ dash(baselineSummary?.prompt_snapshot_hash) }}</dd></div>
            <div><dt>模型</dt><dd>{{ dash(baselineSummary?.profile_name || baselineSummary?.model_name) }}</dd></div>
            <div><dt>耗时</dt><dd>{{ baselineSummary ? `${baselineSummary.latency_seconds.toFixed(2)} s` : "—" }}</dd></div>
            <div><dt>Tokens</dt><dd>{{ dash(baselineSummary?.total_tokens, "—") }}</dd></div>
            <div><dt>句子标注</dt><dd>{{ baselineSummary ? `${baselineSummary.sentence_entry_count} 条` : "—" }}</dd></div>
          </dl>
          <p v-if="baselineSummary?.inline_mark_count || baselineSummary?.translation_count" class="aux-line">
            词汇标注 {{ baselineSummary.inline_mark_count }} · 翻译 {{ baselineSummary.translation_count }}
          </p>
        </article>

        <article class="run-pane">
          <header>
            <strong>Candidate</strong>
            <span :class="`status-pill is-${sideStatus('candidate') === 'succeeded' ? 'success' : sideStatus('candidate') === 'failed' ? 'danger' : 'neutral'}`">{{ sideStatus("candidate") }}</span>
          </header>
          <dl>
            <div><dt>候选版本</dt><dd>{{ dash(candidateSummary?.prompt_variant_id, "baseline") }}</dd></div>
            <div><dt>Snapshot</dt><dd>{{ dash(candidateSummary?.prompt_snapshot_hash) }}</dd></div>
            <div><dt>模型</dt><dd>{{ dash(candidateSummary?.profile_name || candidateSummary?.model_name) }}</dd></div>
            <div><dt>耗时</dt><dd>{{ candidateSummary ? `${candidateSummary.latency_seconds.toFixed(2)} s` : "—" }}</dd></div>
            <div><dt>Tokens</dt><dd>{{ dash(candidateSummary?.total_tokens, "—") }}</dd></div>
            <div><dt>句子标注</dt><dd>{{ candidateSummary ? `${candidateSummary.sentence_entry_count} 条` : "—" }}</dd></div>
          </dl>
          <p v-if="candidateSummary?.inline_mark_count || candidateSummary?.translation_count" class="aux-line">
            词汇标注 {{ candidateSummary.inline_mark_count }} · 翻译 {{ candidateSummary.translation_count }}
          </p>
        </article>
      </section>

      <section class="delta-summary">
        <header>
          <strong>Deterministic 概览</strong>
          <small>辅助参考,不等同于质量判断</small>
        </header>
        <dl>
          <div><dt>更好</dt><dd>{{ report?.wins ?? 0 }}</dd></div>
          <div><dt>变差</dt><dd>{{ report?.losses ?? 0 }}</dd></div>
          <div><dt>持平</dt><dd>{{ report?.ties ?? 0 }}</dd></div>
          <div><dt>总 case</dt><dd>{{ report?.total_cases ?? 0 }}</dd></div>
        </dl>
      </section>

      <section v-if="baselineArtifact || candidateArtifact" class="sentence-diff">
        <header>
          <strong>句子级差异</strong>
          <small>主视图,与 CaseEvidenceInspector 同源</small>
        </header>
        <SentenceCompareDiffView
          :baseline-artifact="baselineArtifact"
          :candidate-artifact="candidateArtifact"
          :prepared-sentences="preparedSentences"
          :compare-case="firstComparison"
          empty-text="本次 compare 暂无可比较 case。"
        />
        <p v-if="firstComparison" class="case-delta-note">
          <span :class="`verdict-pill is-${firstComparison.verdict === 'win' ? 'success' : firstComparison.verdict === 'loss' ? 'danger' : 'neutral'}`">{{ firstComparison.verdict || "—" }}</span>
          <span v-if="firstComparison.reasons?.length">{{ firstComparison.reasons.join("; ") }}</span>
        </p>
      </section>

      <section v-if="baselineArtifact || candidateArtifact" class="notebook">
        <header>
          <strong>句子级证据</strong>
          <small>候选侧完整标注,主视图</small>
        </header>
        <WorkflowSentenceNotebook
          :payload="candidateArtifact?.render_scene || candidateArtifact || null"
          :prepared-sentences="preparedSentences"
          empty-text="本次候选侧没有可用的句子级证据。"
        />
      </section>

      <section class="archive-actions">
        <header>
          <strong>继续</strong>
          <small>这次双跑已经自动物化成 workflow compare；下一个工作区直接消费 compare_id 级别的证据、judge 和 review。</small>
        </header>
        <div class="action-row">
          <button
            type="button"
            class="primary-cta"
            :disabled="!compareResult"
            @click="emit('open-compare')"
          >
            进入 Compare 结果
          </button>
        </div>
        <p class="archive-note">
          Workflow Lab 现在默认以 compare 为唯一公开历史对象。底层 baseline / candidate run artifact 仍会生成，
          但只作为 compare 证据依赖，不再作为用户可见的 Run History 顶层记录。
        </p>
        <dl v-if="compareResult?.compare" class="run-id-grid">
          <div>
            <dt>Compare id</dt>
            <dd>{{ compareResult.compare.compare_id || compareResult.compare_id || "—" }}</dd>
          </div>
          <div>
            <dt>Baseline run id</dt>
            <dd>{{ compareResult.compare.baseline_run_id || "—" }}</dd>
          </div>
          <div>
            <dt>Candidate run id</dt>
            <dd>{{ compareResult.compare.candidate_run_id || "—" }}</dd>
          </div>
        </dl>
      </section>

      <ResultBlock title="完整 compare workspace JSON" :open="false">
        <JsonTreeView :value="compareResult" label="workflow_compare_workspace" />
      </ResultBlock>
    </template>
  </section>
</template>

<style scoped>
.compare-workspace-result {
  container-type: inline-size;
  display: grid;
  gap: 14px;
  border: 1px solid var(--theme--border-color);
  border-radius: 10px;
  background: var(--theme--background);
  padding: 16px;
}

.cw-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.cw-header p,
header p,
header small,
.empty,
.warnings ul,
.archive-note {
  margin: 0;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  font-weight: 700;
}

.cw-header h2 {
  margin: 2px 0 0;
  font-size: 18px;
}

.cw-header > div {
  flex: 1 1 auto;
  min-width: 0;
}

.input-context,
.run-grid,
.delta-summary,
.sentence-diff,
.notebook,
.archive-actions,
.warnings {
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
  background: var(--theme--background);
  padding: 12px 14px;
}

.input-context header,
.run-pane header,
.delta-summary header,
.sentence-diff header,
.notebook header,
.archive-actions header,
.warnings {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 10px;
}

.input-context header strong,
.run-pane header strong,
.delta-summary header strong,
.sentence-diff header strong,
.notebook header strong,
.archive-actions header strong {
  font-size: 13px;
  color: var(--theme--foreground);
}

dt {
  color: var(--theme--foreground-subdued);
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

dd {
  margin: 4px 0 0;
  font-size: 13px;
  font-weight: 500;
  line-height: 1.4;
  overflow-wrap: anywhere;
}

.input-grid,
.delta-summary dl,
.run-pane dl {
  display: grid;
  gap: 1px;
}

.input-grid {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.delta-summary dl {
  grid-template-columns: repeat(4, minmax(0, 1fr));
}

.run-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  background: var(--theme--background-subdued);
  border: 0;
  padding: 0;
}

.run-pane {
  background: var(--theme--background);
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
  padding: 12px 14px;
  display: grid;
  gap: 10px;
}

.run-pane dl {
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 1px;
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  overflow: hidden;
}

.run-pane dl > div,
.input-grid > div,
.delta-summary dl > div {
  background: var(--theme--background-subdued);
  padding: 8px 10px;
  min-width: 0;
}

.aux-line {
  margin: 0;
  color: var(--theme--foreground-subdued);
  font-size: 11px;
}

.text-preview pre {
  margin: 8px 0 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: var(--theme--fonts--monospace--font-family, monospace);
  font-size: 11px;
  background: var(--theme--background-subdued);
  padding: 8px 10px;
  border-radius: 6px;
  max-height: 200px;
  overflow: auto;
}

.warnings {
  border-color: var(--theme--warning);
  background: var(--theme--warning-background);
}

.warnings ul {
  list-style: none;
  padding: 0;
  margin-top: 6px;
  display: grid;
  gap: 4px;
  font-weight: 400;
}

.case-delta-note {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin: 8px 0 0;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
}

.archive-actions {
  background: var(--theme--background-subdued);
}

.action-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 8px;
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

button:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.archive-note {
  margin-top: 8px;
  font-weight: 400;
  line-height: 1.55;
}

.run-id-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 1px;
  margin-top: 8px;
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  overflow: hidden;
}

.run-id-grid > div {
  background: var(--theme--background-subdued);
  padding: 8px 10px;
  min-width: 0;
}

.run-id-grid dt {
  color: var(--theme--foreground-subdued);
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.run-id-grid dd {
  margin: 4px 0 0;
  font-family: var(--theme--fonts--monospace--font-family, monospace);
  font-size: 12px;
  overflow-wrap: anywhere;
}

.status-pill,
.verdict-pill {
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  padding: 0 8px;
  border: 1px solid var(--theme--border-color);
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
  background: var(--theme--background);
}

.status-pill.is-success,
.verdict-pill.is-success {
  color: var(--theme--success);
  border-color: color-mix(in srgb, var(--theme--success) 45%, var(--theme--border-color));
}

.status-pill.is-danger,
.verdict-pill.is-danger {
  color: var(--theme--danger);
  border-color: color-mix(in srgb, var(--theme--danger) 45%, var(--theme--border-color));
}

.status-pill.is-warning,
.verdict-pill.is-warning {
  color: var(--theme--warning);
  border-color: color-mix(in srgb, var(--theme--warning) 45%, var(--theme--border-color));
}

.status-pill.is-neutral,
.verdict-pill.is-neutral {
  color: var(--theme--foreground-subdued);
}

@container (max-width: 760px) {
  .run-grid,
  .input-grid,
  .delta-summary dl {
    grid-template-columns: 1fr;
  }
  .run-pane dl {
    grid-template-columns: 1fr;
  }
}
</style>
