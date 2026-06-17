<script setup>
import { computed, ref } from "vue";
import ResultBlock from "../../../components/ResultBlock.vue";
import JsonTreeView from "../../../components/JsonTreeView.vue";
import WorkflowSentenceNotebook from "./WorkflowSentenceNotebook.vue";
import SentenceCompareDiffView from "./SentenceCompareDiffView.vue";
import WorkflowHealthPanel from "./WorkflowHealthPanel.vue";
import { dash, normalizeWorkflowScene, extractLLMConfigSnapshot } from "../composables/workflowLabFormatting.js";

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

const activeResultTab = ref("diff");

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
  const usageSummary = artifact.usage_summary || {};
  const tokens = usageSummary.total_tokens ?? null;
  
  const scene = normalizeWorkflowScene(artifact);
  const inlineMarks = Array.isArray(scene?.inline_marks) ? scene.inline_marks : [];
  const sentenceEntries = Array.isArray(scene?.sentence_entries) ? scene.sentence_entries : [];

  const grammarCount = sentenceEntries.filter(e => e?.entry_type === "grammar_note").length;
  const analysisCount = sentenceEntries.filter(e => e?.entry_type === "sentence_analysis").length;

  const vocabHighlightCount = inlineMarks.filter(m => m?.annotation_type === "vocab_highlight").length;
  const phraseGlossCount = inlineMarks.filter(m => m?.annotation_type === "phrase_gloss").length;
  const contextGlossCount = inlineMarks.filter(m => m?.annotation_type === "context_gloss").length;

  const llmConfig = extractLLMConfigSnapshot(artifact);

  return {
    adapter_status: adapterStatus,
    latency_seconds: Number.isFinite(latency) ? latency : 0,
    total_tokens: tokens,
    input_tokens: usageSummary.input_tokens ?? null,
    output_tokens: usageSummary.output_tokens ?? null,
    prompt_variant_id: artifact.prompt_identity?.prompt_variant_id || null,
    prompt_snapshot_hash: artifact.prompt_identity?.prompt_snapshot_hash || null,
    profile_name: artifact.model_identity?.profile_name || null,
    model_name: artifact.model_identity?.model_name || null,
    translation_count: Array.isArray(artifact.translations) ? artifact.translations.length : 0,
    inline_mark_count: Array.isArray(artifact.inline_marks) ? artifact.inline_marks.length : 0,
    sentence_entry_count: Array.isArray(artifact.sentence_entries) ? artifact.sentence_entries.length : 0,
    
    grammar_count: grammarCount,
    analysis_count: analysisCount,
    vocab_highlight_count: vocabHighlightCount,
    phrase_gloss_count: phraseGlossCount,
    context_gloss_count: contextGlossCount,

    llm_config_profile: llmConfig?.profile || null,
    llm_config_model: llmConfig?.model || null,
    llm_config_expected_tool_choice: llmConfig?.expected_tool_choice || null,
    llm_config_thinking: llmConfig?.thinking_enabled ? "开启" : llmConfig ? "关闭" : null,
    llm_config_structured_output_mode: llmConfig?.default_structured_output_mode || null,
    llm_config_parallel_tool_calls: llmConfig?.parallel_tool_calls ?? null,
    llm_config_expected_response_format: llmConfig?.expected_response_format ?? null,
  };
}

const baselineSummary = computed(() => summarizeRun("baseline"));
const candidateSummary = computed(() => summarizeRun("candidate"));

const latencyDiff = computed(() => {
  if (baselineSummary.value && candidateSummary.value) {
    return candidateSummary.value.latency_seconds - baselineSummary.value.latency_seconds;
  }
  return null;
});

const tokensDiff = computed(() => {
  if (
    baselineSummary.value &&
    candidateSummary.value &&
    baselineSummary.value.total_tokens !== null &&
    candidateSummary.value.total_tokens !== null
  ) {
    return candidateSummary.value.total_tokens - baselineSummary.value.total_tokens;
  }
  return null;
});

const translationDiff = computed(() => {
  if (baselineSummary.value && candidateSummary.value) {
    return candidateSummary.value.translation_count - baselineSummary.value.translation_count;
  }
  return null;
});

const grammarDiff = computed(() => {
  if (baselineSummary.value && candidateSummary.value) {
    return candidateSummary.value.grammar_count - baselineSummary.value.grammar_count;
  }
  return null;
});

const analysisDiff = computed(() => {
  if (baselineSummary.value && candidateSummary.value) {
    return candidateSummary.value.analysis_count - baselineSummary.value.analysis_count;
  }
  return null;
});

const vocabDiff = computed(() => {
  if (baselineSummary.value && candidateSummary.value) {
    return candidateSummary.value.vocab_highlight_count - baselineSummary.value.vocab_highlight_count;
  }
  return null;
});

const phraseDiff = computed(() => {
  if (baselineSummary.value && candidateSummary.value) {
    return candidateSummary.value.phrase_gloss_count - baselineSummary.value.phrase_gloss_count;
  }
  return null;
});

const contextDiff = computed(() => {
  if (baselineSummary.value && candidateSummary.value) {
    return candidateSummary.value.context_gloss_count - baselineSummary.value.context_gloss_count;
  }
  return null;
});

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
          <p class="section-kicker">Compare Workspace</p>
          <h2>单篇 baseline / candidate compare</h2>
        </div>
        <span :class="`verdict-pill is-${verdictTone}`">{{ verdictLabel }}</span>
      </header>

      <!-- Metrics Panel: Combined Input Context & Deterministic Overview -->
      <section class="dashboard-metrics-section">
        <article class="metric-block input-context-card">
          <header class="block-header">
            <strong>输入文章 (双跑共享)</strong>
            <small class="meta-hash">input_hash {{ compare?.input_hash || "—" }}</small>
          </header>
          <div class="horizontal-stats">
            <div class="stat-widget">
              <span class="stat-label">阅读目标</span>
              <span class="stat-value">{{ readingGoalLabel(inputSnapshot.reading_goal) }}</span>
            </div>
            <div class="stat-widget">
              <span class="stat-label">阅读场景</span>
              <span class="stat-value">{{ readingVariantLabel(inputSnapshot.reading_variant) }}</span>
            </div>
            <div class="stat-widget">
              <span class="stat-label">差异句数</span>
              <span class="stat-value highlight-primary">{{ dash(report?.total_cases, "—") }}</span>
            </div>
          </div>
          <details v-if="inputSnapshot.text" class="text-preview">
            <summary>查看输入文本 (共 {{ inputSnapshot.text.length }} 字符)</summary>
            <pre>{{ inputSnapshot.text }}</pre>
          </details>
        </article>

        <article class="metric-block delta-summary-card">
          <header class="block-header">
            <strong>Deterministic 概览</strong>
            <small class="meta-note">辅助参考，不等同于质量判断</small>
          </header>
          <div class="horizontal-stats">
            <div class="stat-widget is-success">
              <span class="stat-label">更好 (Wins)</span>
              <span class="stat-value">{{ report?.wins ?? 0 }}</span>
            </div>
            <div class="stat-widget is-danger">
              <span class="stat-label">变差 (Losses)</span>
              <span class="stat-value">{{ report?.losses ?? 0 }}</span>
            </div>
            <div class="stat-widget is-neutral">
              <span class="stat-label">持平 (Ties)</span>
              <span class="stat-value">{{ report?.ties ?? 0 }}</span>
            </div>
            <div class="stat-widget is-primary">
              <span class="stat-label">差异句数</span>
              <span class="stat-value">{{ report?.total_cases ?? 0 }}</span>
            </div>
          </div>
        </article>
      </section>

      <section v-if="identityWarnings.length" class="warnings">
        <strong>运行身份提醒</strong>
        <ul>
          <li v-for="(warning, index) in identityWarnings" :key="index">{{ warning }}</li>
        </ul>
      </section>

      <section class="run-grid">
        <article class="run-pane is-baseline">
          <header class="run-pane-header">
            <div class="pane-title-group">
              <span class="pane-indicator"></span>
              <strong>Baseline</strong>
            </div>
            <span :class="`status-pill is-${sideStatus('baseline') === 'succeeded' ? 'success' : sideStatus('baseline') === 'failed' ? 'danger' : 'neutral'}`">{{ sideStatus("baseline") }}</span>
          </header>
          <div class="run-meta-list">
            <div class="meta-row"><span class="meta-label">候选版本</span><span class="meta-value">{{ dash(baselineSummary?.prompt_variant_id, "baseline default") }}</span></div>
            <div class="meta-row"><span class="meta-label">Snapshot</span><span class="meta-value mono-val">{{ dash(baselineSummary?.prompt_snapshot_hash) }}</span></div>
            <div class="meta-row"><span class="meta-label">模型</span><span class="meta-value">{{ dash(baselineSummary?.profile_name || baselineSummary?.model_name) }}</span></div>
            <div class="meta-row"><span class="meta-label">tool_choice</span><span class="meta-value">{{ dash(baselineSummary?.llm_config_expected_tool_choice) }}</span></div>
            <div class="meta-row"><span class="meta-label">结构化输出</span><span class="meta-value">{{ dash(baselineSummary?.llm_config_structured_output_mode) }}</span></div>
            <div class="meta-row"><span class="meta-label">response_format</span><span class="meta-value">{{ dash(baselineSummary?.llm_config_expected_response_format) }}</span></div>
            <div class="meta-row"><span class="meta-label">parallel_tool_calls</span><span class="meta-value">{{ baselineSummary?.llm_config_parallel_tool_calls === true ? '是' : baselineSummary?.llm_config_parallel_tool_calls === false ? '否' : dash(baselineSummary?.llm_config_parallel_tool_calls) }}</span></div>
            <div class="meta-row"><span class="meta-label">Thinking</span><span class="meta-value">{{ dash(baselineSummary?.llm_config_thinking) }}</span></div>
            <div class="meta-row">
              <span class="meta-label">耗时</span>
              <div class="meta-val-with-diff">
                <span
                  class="meta-value"
                  :class="{
                    'is-faster': latencyDiff !== null && latencyDiff > 0,
                    'is-slower': latencyDiff !== null && latencyDiff < 0
                  }"
                >
                  {{ baselineSummary ? `${baselineSummary.latency_seconds.toFixed(2)} s` : "—" }}
                </span>
                <span
                  v-if="latencyDiff !== null && latencyDiff !== 0"
                  class="meta-delta"
                  :class="{
                    'is-faster': latencyDiff > 0,
                    'is-slower': latencyDiff < 0
                  }"
                >
                  {{ latencyDiff > 0 ? '-' : '+' }}{{ Math.abs(latencyDiff).toFixed(2) }} s
                </span>
              </div>
            </div>
            <div class="meta-row token-row">
              <span class="meta-label">Tokens</span>
              <div class="meta-val-col">
                <div class="meta-val-with-diff">
                  <span
                    class="meta-value"
                    :class="{
                      'is-faster': tokensDiff !== null && tokensDiff > 0,
                      'is-slower': tokensDiff !== null && tokensDiff < 0
                    }"
                  >
                    {{ baselineSummary?.total_tokens != null ? baselineSummary.total_tokens : "—" }}
                  </span>
                  <span
                    v-if="tokensDiff !== null && tokensDiff !== 0"
                    class="meta-delta"
                    :class="{
                      'is-faster': tokensDiff > 0,
                      'is-slower': tokensDiff < 0
                    }"
                  >
                    {{ tokensDiff > 0 ? '-' : '+' }}{{ Math.abs(tokensDiff) }}
                  </span>
                </div>
                <small class="meta-breakdown" v-if="baselineSummary?.total_tokens != null">
                  入 {{ baselineSummary.input_tokens ?? "—" }} / 出 {{ baselineSummary.output_tokens ?? "—" }}
                </small>
              </div>
            </div>
            <div class="meta-row"><span class="meta-label">翻译</span><span class="meta-value">{{ baselineSummary ? `${baselineSummary.translation_count} 条` : "—" }}</span></div>
            <div class="meta-row"><span class="meta-label">语法</span><span class="meta-value">{{ baselineSummary ? `${baselineSummary.grammar_count} 条` : "—" }}</span></div>
            <div class="meta-row"><span class="meta-label">句法</span><span class="meta-value">{{ baselineSummary ? `${baselineSummary.analysis_count} 条` : "—" }}</span></div>
            <div class="meta-row"><span class="meta-label">词汇</span><span class="meta-value">{{ baselineSummary ? `${baselineSummary.vocab_highlight_count} 条` : "—" }}</span></div>
            <div class="meta-row"><span class="meta-label">短语</span><span class="meta-value">{{ baselineSummary ? `${baselineSummary.phrase_gloss_count} 条` : "—" }}</span></div>
            <div class="meta-row"><span class="meta-label">语境</span><span class="meta-value">{{ baselineSummary ? `${baselineSummary.context_gloss_count} 条` : "—" }}</span></div>
          </div>
        </article>

        <article class="run-pane is-candidate">
          <header class="run-pane-header">
            <div class="pane-title-group">
              <span class="pane-indicator"></span>
              <strong>Candidate</strong>
            </div>
            <span :class="`status-pill is-${sideStatus('candidate') === 'succeeded' ? 'success' : sideStatus('candidate') === 'failed' ? 'danger' : 'neutral'}`">{{ sideStatus("candidate") }}</span>
          </header>
          <div class="run-meta-list">
            <div class="meta-row"><span class="meta-label">候选版本</span><span class="meta-value">{{ dash(candidateSummary?.prompt_variant_id, "baseline") }}</span></div>
            <div class="meta-row"><span class="meta-label">Snapshot</span><span class="meta-value mono-val">{{ dash(candidateSummary?.prompt_snapshot_hash) }}</span></div>
            <div class="meta-row"><span class="meta-label">模型</span><span class="meta-value">{{ dash(candidateSummary?.profile_name || candidateSummary?.model_name) }}</span></div>
            <div class="meta-row"><span class="meta-label">tool_choice</span><span class="meta-value">{{ dash(candidateSummary?.llm_config_expected_tool_choice) }}</span></div>
            <div class="meta-row"><span class="meta-label">结构化输出</span><span class="meta-value">{{ dash(candidateSummary?.llm_config_structured_output_mode) }}</span></div>
            <div class="meta-row"><span class="meta-label">response_format</span><span class="meta-value">{{ dash(candidateSummary?.llm_config_expected_response_format) }}</span></div>
            <div class="meta-row"><span class="meta-label">parallel_tool_calls</span><span class="meta-value">{{ candidateSummary?.llm_config_parallel_tool_calls === true ? '是' : candidateSummary?.llm_config_parallel_tool_calls === false ? '否' : dash(candidateSummary?.llm_config_parallel_tool_calls) }}</span></div>
            <div class="meta-row"><span class="meta-label">Thinking</span><span class="meta-value">{{ dash(candidateSummary?.llm_config_thinking) }}</span></div>
            <div class="meta-row">
              <span class="meta-label">耗时</span>
              <div class="meta-val-with-diff">
                <span
                  class="meta-value"
                  :class="{
                    'is-faster': latencyDiff !== null && latencyDiff < 0,
                    'is-slower': latencyDiff !== null && latencyDiff > 0
                  }"
                >
                  {{ candidateSummary ? `${candidateSummary.latency_seconds.toFixed(2)} s` : "—" }}
                </span>
                <span
                  v-if="latencyDiff !== null && latencyDiff !== 0"
                  class="meta-delta"
                  :class="{
                    'is-faster': latencyDiff < 0,
                    'is-slower': latencyDiff > 0
                  }"
                >
                  {{ latencyDiff > 0 ? '+' : '-' }}{{ Math.abs(latencyDiff).toFixed(2) }} s
                </span>
              </div>
            </div>
            <div class="meta-row token-row">
              <span class="meta-label">Tokens</span>
              <div class="meta-val-col">
                <div class="meta-val-with-diff">
                  <span
                    class="meta-value"
                    :class="{
                      'is-faster': tokensDiff !== null && tokensDiff < 0,
                      'is-slower': tokensDiff !== null && tokensDiff > 0
                    }"
                  >
                    {{ candidateSummary?.total_tokens != null ? candidateSummary.total_tokens : "—" }}
                  </span>
                  <span
                    v-if="tokensDiff !== null && tokensDiff !== 0"
                    class="meta-delta"
                    :class="{
                      'is-faster': tokensDiff < 0,
                      'is-slower': tokensDiff > 0
                    }"
                  >
                    {{ tokensDiff > 0 ? '+' : '-' }}{{ Math.abs(tokensDiff) }}
                  </span>
                </div>
                <small class="meta-breakdown" v-if="candidateSummary?.total_tokens != null">
                  入 {{ candidateSummary.input_tokens ?? "—" }} / 出 {{ candidateSummary.output_tokens ?? "—" }}
                </small>
              </div>
            </div>
            <div class="meta-row">
              <span class="meta-label">翻译</span>
              <div class="meta-val-with-diff">
                <span class="meta-value">{{ candidateSummary ? `${candidateSummary.translation_count} 条` : "—" }}</span>
                <span v-if="translationDiff !== null && translationDiff !== 0" class="meta-delta is-neutral">
                  {{ translationDiff > 0 ? '+' : '' }}{{ translationDiff }}
                </span>
              </div>
            </div>
            <div class="meta-row">
              <span class="meta-label">语法</span>
              <div class="meta-val-with-diff">
                <span class="meta-value">{{ candidateSummary ? `${candidateSummary.grammar_count} 条` : "—" }}</span>
                <span v-if="grammarDiff !== null && grammarDiff !== 0" class="meta-delta is-neutral">
                  {{ grammarDiff > 0 ? '+' : '' }}{{ grammarDiff }}
                </span>
              </div>
            </div>
            <div class="meta-row">
              <span class="meta-label">句法</span>
              <div class="meta-val-with-diff">
                <span class="meta-value">{{ candidateSummary ? `${candidateSummary.analysis_count} 条` : "—" }}</span>
                <span v-if="analysisDiff !== null && analysisDiff !== 0" class="meta-delta is-neutral">
                  {{ analysisDiff > 0 ? '+' : '' }}{{ analysisDiff }}
                </span>
              </div>
            </div>
            <div class="meta-row">
              <span class="meta-label">词汇</span>
              <div class="meta-val-with-diff">
                <span class="meta-value">{{ candidateSummary ? `${candidateSummary.vocab_highlight_count} 条` : "—" }}</span>
                <span v-if="vocabDiff !== null && vocabDiff !== 0" class="meta-delta is-neutral">
                  {{ vocabDiff > 0 ? '+' : '' }}{{ vocabDiff }}
                </span>
              </div>
            </div>
            <div class="meta-row">
              <span class="meta-label">短语</span>
              <div class="meta-val-with-diff">
                <span class="meta-value">{{ candidateSummary ? `${candidateSummary.phrase_gloss_count} 条` : "—" }}</span>
                <span v-if="phraseDiff !== null && phraseDiff !== 0" class="meta-delta is-neutral">
                  {{ phraseDiff > 0 ? '+' : '' }}{{ phraseDiff }}
                </span>
              </div>
            </div>
            <div class="meta-row">
              <span class="meta-label">语境</span>
              <div class="meta-val-with-diff">
                <span class="meta-value">{{ candidateSummary ? `${candidateSummary.context_gloss_count} 条` : "—" }}</span>
                <span v-if="contextDiff !== null && contextDiff !== 0" class="meta-delta is-neutral">
                  {{ contextDiff > 0 ? '+' : '' }}{{ contextDiff }}
                </span>
              </div>
            </div>
          </div>
        </article>
      </section>

      <!-- Workflow Health: 优先于句子级内容差异 -->
      <WorkflowHealthPanel
        v-if="baselineArtifact || candidateArtifact"
        :baseline-artifact="baselineArtifact"
        :candidate-artifact="candidateArtifact"
      />

      <!-- View Selector Tabs -->
      <section v-if="baselineArtifact || candidateArtifact" class="result-tabs-container">
        <div class="tab-selectors">
          <button
            type="button"
            class="tab-btn"
            :class="{ active: activeResultTab === 'diff' }"
            @click="activeResultTab = 'diff'"
          >
            双边差异对照
          </button>
          <button
            type="button"
            class="tab-btn"
            :class="{ active: activeResultTab === 'notebook' }"
            @click="activeResultTab = 'notebook'"
          >
            候选侧完整数据
          </button>
        </div>

        <div class="tab-pane-content">
          <!-- Diff View Tab -->
          <div v-if="activeResultTab === 'diff'" class="tab-pane-view">
            <header class="tab-pane-header">
              <h3>句子级差异</h3>
              <small>主视图，与双边句子证据同源</small>
            </header>
            <SentenceCompareDiffView
              :baseline-artifact="baselineArtifact"
              :candidate-artifact="candidateArtifact"
              :prepared-sentences="preparedSentences"
              :compare-case="firstComparison"
              empty-text="本次 compare 暂无可比较差异句。"
            />
            <p v-if="firstComparison" class="case-delta-note">
              <span :class="`verdict-pill is-${firstComparison.verdict === 'win' ? 'success' : firstComparison.verdict === 'loss' ? 'danger' : 'neutral'}`">{{ firstComparison.verdict || "—" }}</span>
              <span v-if="firstComparison.reasons?.length">{{ firstComparison.reasons.join("; ") }}</span>
            </p>
          </div>

          <!-- Full Notebook View Tab -->
          <div v-else-if="activeResultTab === 'notebook'" class="tab-pane-view">
            <header class="tab-pane-header">
              <h3>句子级证据</h3>
              <small>候选侧完整标注，主视图</small>
            </header>
            <WorkflowSentenceNotebook
              :payload="candidateArtifact?.render_scene || candidateArtifact || null"
              :prepared-sentences="preparedSentences"
              empty-text="本次候选侧没有可用的句子级证据。"
            />
          </div>
        </div>
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
  gap: 16px;
  border: 1px solid var(--theme--border-color);
  border-radius: 10px;
  background: var(--theme--background);
  padding: 18px;
}

.cw-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.cw-header p,
.section-kicker,
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

/* Dashboard Metrics Header Layout */
.dashboard-metrics-section {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
  gap: 16px;
  max-width: 1480px;
}

.metric-block {
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
  background: var(--theme--background);
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  box-shadow: 0 4px 18px rgba(17, 17, 17, 0.03);
}

.block-header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  border-bottom: 1px dashed var(--theme--border-color);
  padding-bottom: 8px;
  margin-bottom: 12px;
}

.block-header strong {
  font-size: 13px;
  font-weight: 700;
  color: var(--theme--foreground);
}

.meta-hash, .meta-note {
  font-size: 11px;
  color: var(--theme--foreground-subdued);
  font-family: var(--theme--fonts--monospace--font-family, monospace);
}

.horizontal-stats {
  display: flex;
  gap: 20px 24px;
  flex-wrap: wrap;
}

.stat-widget {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 80px;
}

.stat-label {
  font-size: 11px;
  color: var(--theme--foreground-subdued);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.02em;
}

.stat-value {
  font-size: 18px;
  font-weight: 800;
  color: var(--theme--foreground);
}

.stat-value.highlight-primary {
  color: var(--theme--primary);
}

.stat-widget.is-success .stat-value {
  color: var(--theme--success);
}

.stat-widget.is-danger .stat-value {
  color: var(--theme--danger);
}

.stat-widget.is-warning .stat-value {
  color: var(--theme--warning);
}

.stat-widget.is-neutral .stat-value {
  color: var(--theme--foreground-subdued);
}

.stat-widget.is-primary .stat-value {
  color: var(--theme--primary);
}

/* Run Panes with top indicator colors */
.run-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  background: var(--theme--background-subdued);
  border: 0;
  padding: 0;
}

.run-pane {
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
  background: var(--theme--background);
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  position: relative;
  overflow: hidden;
  box-shadow: 0 4px 18px rgba(17, 17, 17, 0.03);
}

.run-pane.is-baseline {
  border-top: 3px solid var(--theme--foreground-subdued);
}

.run-pane.is-candidate {
  border-top: 3px solid var(--theme--primary);
}

.run-pane-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.pane-title-group {
  display: flex;
  align-items: center;
  gap: 8px;
}

.pane-title-group strong {
  font-size: 14px;
}

.pane-indicator {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--theme--foreground-subdued);
}

.run-pane.is-candidate .pane-indicator {
  background: var(--theme--primary);
}

.run-meta-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  background: var(--theme--background-subdued);
  padding: 12px;
  border-radius: 6px;
  border: 1px solid var(--theme--border-color);
}

.meta-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 12px;
  font-size: 12px;
}

.meta-row:not(:last-child) {
  border-bottom: 1px solid color-mix(in srgb, var(--theme--border-color) 40%, transparent);
  padding-bottom: 6px;
}

.meta-label {
  color: var(--theme--foreground-subdued);
  font-weight: 500;
}

.meta-value {
  color: var(--theme--foreground);
  font-weight: 700;
  text-align: right;
}

.meta-value.mono-val {
  font-family: var(--theme--fonts--monospace--font-family, monospace);
  font-size: 11px;
}

.meta-value.highlight-num {
  color: var(--theme--primary);
}

.meta-value.is-faster {
  color: var(--theme--success) !important;
}

.meta-value.is-slower {
  color: var(--theme--danger) !important;
}

.meta-val-with-diff {
  display: flex;
  align-items: center;
  gap: 6px;
  justify-content: flex-end;
}

.meta-delta {
  font-size: 10px;
  font-weight: 700;
  white-space: nowrap;
  padding: 1px 5px;
  border-radius: 4px;
  border: 1px solid transparent;
}

.meta-delta.is-faster {
  color: var(--theme--success);
  background: color-mix(in srgb, var(--theme--success) 10%, var(--theme--background));
  border-color: color-mix(in srgb, var(--theme--success) 30%, var(--theme--border-color));
}

.meta-delta.is-slower {
  color: var(--theme--danger);
  background: color-mix(in srgb, var(--theme--danger) 10%, var(--theme--background));
  border-color: color-mix(in srgb, var(--theme--danger) 30%, var(--theme--border-color));
}

.meta-delta.is-neutral {
  color: var(--theme--primary);
  background: color-mix(in srgb, var(--theme--primary) 8%, var(--theme--background));
  border-color: color-mix(in srgb, var(--theme--primary) 25%, var(--theme--border-color));
}

.token-row {
  align-items: flex-start !important;
}

.meta-val-col {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 2px;
}

.meta-breakdown {
  font-size: 10px;
  color: var(--theme--foreground-subdued);
  opacity: 0.85;
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
  border: 1px solid var(--theme--warning);
  background: var(--theme--warning-background);
  padding: 12px 14px;
  border-radius: 8px;
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

/* Tabs styles */
.result-tabs-container {
  display: flex;
  flex-direction: column;
  gap: 16px;
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
  padding: 16px;
  background: var(--theme--background);
}

.tab-selectors {
  display: flex;
  gap: 6px;
  border-bottom: 1px solid var(--theme--border-color);
  padding-bottom: 10px;
}

.tab-btn {
  border: 1px solid var(--theme--border-color);
  background: var(--theme--background);
  color: var(--theme--foreground-subdued);
  padding: 6px 14px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
  transition: all 0.15s ease-in-out;
}

.tab-btn:hover {
  border-color: var(--theme--foreground-subdued);
  color: var(--theme--foreground);
}

.tab-btn.active {
  background: var(--theme--primary);
  color: var(--theme--primary-foreground, #fff);
  border-color: var(--theme--primary);
}

.tab-pane-header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 12px;
}

.tab-pane-header h3 {
  margin: 0;
  font-size: 14px;
  font-weight: 700;
  color: var(--theme--foreground);
}

.tab-pane-header small {
  color: var(--theme--foreground-subdued);
  font-size: 11px;
}

.archive-actions {
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
  padding: 12px 14px;
  background: var(--theme--background-subdued);
}

.archive-actions header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}

.archive-actions header strong {
  font-size: 13px;
  color: var(--theme--foreground);
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

.primary-cta:disabled {
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
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
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
  font-size: 11px;
  color: var(--theme--foreground-subdued);
  user-select: all;
  overflow-wrap: break-word;
}

.status-pill,
.verdict-pill {
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  padding: 0 8px;
  border: 1px solid var(--theme--border-color);
  font-size: 11px;
  font-weight: 650;
  white-space: nowrap;
  background: var(--theme--background);
  border-radius: 4px;
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
  .dashboard-metrics-section {
    grid-template-columns: 1fr;
  }
}
</style>
