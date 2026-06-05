<script setup>
import { computed } from "vue";
import {
  chipSideLabel,
  compareHealthInsights,
  computeRepairStatus,
  dash,
  extractHealthSignals,
  splitPostprocessAndRepair,
} from "../composables/workflowLabFormatting.js";

const props = defineProps({
  baselineArtifact: { type: [Object, null], default: null },
  candidateArtifact: { type: [Object, null], default: null },
  /** sentence 影响列表在 group 里默认展示多少个；超过会显示 “等 N 句”。 */
  visibleSidLimit: { type: Number, default: 4 },
  /** warning group 业务解释是否默认展开。默认折叠，避免页面纵向过长。 */
  explainByDefault: { type: Boolean, default: false },
});

const baseline = computed(() => extractHealthSignals(props.baselineArtifact));
const candidate = computed(() => extractHealthSignals(props.candidateArtifact));

const baselineSplit = computed(() => splitPostprocessAndRepair(baseline.value));
const candidateSplit = computed(() => splitPostprocessAndRepair(candidate.value));

const baselineRepair = computed(() => computeRepairStatus(baseline.value));
const candidateRepair = computed(() => computeRepairStatus(candidate.value));

const insights = computed(() => compareHealthInsights(baseline.value, candidate.value));

const CORE_AGENT_ORDER = ["vocabulary", "grammar", "translation", "repair"];

function orderedPerAgent(health) {
  const known = new Map(health.perAgent.map((row) => [row.name, row]));
  const result = [];
  for (const name of CORE_AGENT_ORDER) {
    const row = known.get(name);
    if (row) {
      result.push({ ...row, known: true });
    } else {
      result.push({ name, input: null, output: null, total: null, known: false, missing: true });
    }
  }
  for (const row of health.perAgent) {
    if (!CORE_AGENT_ORDER.includes(row.name)) {
      result.push({ ...row, known: true, missing: false });
    }
  }
  return result;
}

function agentLabel(name) {
  switch (name) {
    case "vocabulary": return "词汇";
    case "grammar": return "语法";
    case "translation": return "翻译";
    case "repair": return "修复";
    default: return name;
  }
}

function formatTokens(value) {
  if (value == null) return "—";
  return String(value);
}

function sharePercent(share) {
  if (share == null) return null;
  return `${(share * 100).toFixed(1)}%`;
}

function shareTone(share) {
  if (share == null) return "neutral";
  if (share >= 0.5) return "danger";
  if (share >= 0.25) return "warning";
  return "neutral";
}

function sign(value, suffix = "") {
  if (value == null) return "—";
  if (value > 0) return `+${value}${suffix}`;
  if (value < 0) return `${value}${suffix}`;
  return `±0${suffix}`;
}

function signPp(value) {
  if (value == null) return "—";
  if (value > 0) return `+${value.toFixed(1)}pp`;
  if (value < 0) return `${value.toFixed(1)}pp`;
  return "±0.0pp";
}

function directionTone(direction) {
  if (direction === "better") return "is-success";
  if (direction === "worse") return "is-danger";
  if (direction === "same") return "is-neutral";
  return "is-neutral";
}

function compactSidList(sids, limit) {
  if (!Array.isArray(sids) || sids.length === 0) return { visible: [], overflow: 0 };
  const cap = limit ?? props.visibleSidLimit;
  if (sids.length <= cap) return { visible: sids.slice(), overflow: 0 };
  return { visible: sids.slice(0, cap), overflow: sids.length - cap };
}

function shortMessage(message, max = 80) {
  if (!message) return "";
  if (message.length <= max) return message;
  return `${message.slice(0, max - 1)}…`;
}

const showRepairShareTone = computed(() => {
  const baseTone = shareTone(baseline.value.repairShare);
  const candTone = shareTone(candidate.value.repairShare);
  // 选更严重的一档作 header chip 调色
  const rank = { neutral: 0, warning: 1, danger: 2 };
  return rank[candTone] > rank[baseTone] ? candTone : baseTone;
});
</script>

<template>
  <section class="workflow-health-panel">
    <header class="panel-header">
      <div class="panel-headline">
        <p class="section-kicker">Workflow Health</p>
        <h3>工作流健康与质量信号</h3>
      </div>
      <div class="panel-header-tally">
        <div class="tally-pill" :class="directionTone(insights.stateDirection)">
          <span>Compare</span>
          <strong>{{ insights.overallDirectionLabel }}</strong>
        </div>
        <div class="tally-pill" :class="`is-${showRepairShareTone}`">
          <span>Repair share Δ</span>
          <strong>{{ signPp(insights.repairShareDeltaPp) }}</strong>
        </div>
      </div>
    </header>

    <div class="compare-bar">
      <div class="delta-chip" :class="`is-${insights.warningDelta < 0 ? 'success' : insights.warningDelta > 0 ? 'danger' : 'neutral'}`">
        <span class="delta-label">Candidate warnings</span>
        <strong>{{ sign(insights.warningDelta) }}</strong>
        <small v-if="insights.warningDeltaPct != null">（{{ sign(Math.round(insights.warningDeltaPct * 100)) }}%）</small>
      </div>
      <div class="delta-chip" :class="`is-${insights.newCodes.length ? 'warning' : 'neutral'}`">
        <span class="delta-label">新增 warning 类别</span>
        <strong>{{ insights.newCodes.length }}</strong>
        <span v-if="insights.newCodesMeta.length" class="delta-codes">
          <span v-for="entry in insights.newCodesMeta" :key="`new-${entry.code}`" :class="['code-tag', `is-${entry.category}`]">{{ entry.chipText }}</span>
        </span>
      </div>
      <div class="delta-chip" :class="`is-${insights.removedCodes.length ? 'success' : 'neutral'}`">
        <span class="delta-label">消除 warning 类别</span>
        <strong>{{ insights.removedCodes.length }}</strong>
        <span v-if="insights.removedCodesMeta.length" class="delta-codes">
          <span v-for="entry in insights.removedCodesMeta" :key="`rem-${entry.code}`" :class="['code-tag', `is-${entry.category}`]">{{ entry.chipText }}</span>
        </span>
      </div>
    </div>

    <div class="health-grid">
      <!-- Baseline side -->
      <article class="health-pane is-baseline">
        <header class="pane-header">
          <div class="pane-title-group">
            <span class="pane-indicator"></span>
            <strong>Baseline</strong>
          </div>
          <div class="pane-head-chips">
            <span :class="`state-pill is-${baseline.userFacingTone.tone}`">
              {{ dash(baseline.userFacingTone.label, baseline.userFacingState) }}
            </span>
            <span :class="`repair-status is-${baselineRepair.tone}`" :title="baselineRepair.hint">
              repair · {{ baselineRepair.label }}
            </span>
          </div>
        </header>

        <dl class="metric-grid compact">
          <div>
            <dt>Warnings</dt>
            <dd :class="{ 'has-warn': baseline.warningCount > 0 }">{{ baseline.warningCount }}</dd>
          </div>
          <div>
            <dt>Drop</dt>
            <dd :class="{ 'has-warn': baseline.dropCount > 0 }">{{ baseline.dropCount }}</dd>
          </div>
          <div>
            <dt>Repair share</dt>
            <dd :class="`is-${shareTone(baseline.repairShare)}`">
              {{ sharePercent(baseline.repairShare) || "—" }}
            </dd>
          </div>
        </dl>

        <section class="agent-block">
          <div class="block-head">
            <strong>Per-agent token 消耗</strong>
            <small>完整 latency / total tokens 见上方运行概览</small>
          </div>
          <ul class="agent-list">
            <li
              v-for="agent in orderedPerAgent(baseline)"
              :key="`b-agent-${agent.name}`"
              :class="['agent-row', { 'is-repair': agent.name === 'repair', 'is-missing': agent.missing }]"
            >
              <span class="agent-name">
                {{ agentLabel(agent.name) }}
                <span v-if="agent.name === 'repair'" class="agent-tag">修复</span>
              </span>
              <span class="agent-value">
                <template v-if="!agent.missing">
                  <strong>{{ formatTokens(agent.total) }}</strong>
                  <small>入 {{ formatTokens(agent.input) }} / 出 {{ formatTokens(agent.output) }}</small>
                </template>
                <template v-else>
                  <strong>—</strong>
                  <small>本次无该节点</small>
                </template>
              </span>
            </li>
          </ul>
          <p v-if="baselineRepair.hint && baselineRepair.state !== 'not_triggered' && baselineRepair.state !== 'triggered_clean'" class="repair-hint" :class="`is-${baselineRepair.tone}`">
            {{ baselineRepair.hint }}
          </p>
        </section>

        <section v-if="baseline.warningsGrouped.groups.length" class="warning-block">
          <div class="block-head">
            <strong>后置校验与修复信号</strong>
            <small>按 code 聚合</small>
          </div>
          <div v-if="baselineSplit.postprocess.length" class="warning-section">
            <div class="warning-section-head">
              <span class="section-tag postprocess">后置校验结果</span>
              <span class="section-count">{{ baselineSplit.postprocess.reduce((sum, g) => sum + g.count, 0) }} 条</span>
            </div>
            <ul class="warning-groups">
              <li v-for="group in baselineSplit.postprocess" :key="`b-warn-${group.code}`" class="warning-group">
                <div class="warning-group-head">
                  <span class="warning-code-label">{{ group.meta.label }}</span>
                  <span class="warning-count">×{{ group.count }}</span>
                </div>
                <div v-if="group.sentenceIds.length" class="warning-sids">
                  <span class="warning-sids-label">影响：</span>
                  <span v-for="sid in compactSidList(group.sentenceIds).visible" :key="`b-sid-${group.code}-${sid}`" class="sid-pill">{{ sid }}</span>
                  <span v-if="compactSidList(group.sentenceIds).overflow" class="sid-overflow">等 {{ compactSidList(group.sentenceIds).overflow }} 句</span>
                </div>
                <details class="warning-detail" :open="explainByDefault">
                  <summary>业务解释与样例</summary>
                  <p class="warning-explanation">{{ group.meta.explanation }}</p>
                  <ul v-if="group.sampleMessages.length" class="warning-samples">
                    <li v-for="(sample, idx) in group.sampleMessages" :key="`b-sample-${group.code}-${idx}`">
                      <span v-if="sample.sentenceId" class="sid-pill is-mini">{{ sample.sentenceId }}</span>
                      <span>{{ shortMessage(sample.message) }}</span>
                    </li>
                  </ul>
                </details>
              </li>
            </ul>
          </div>
          <div v-if="baselineSplit.repair.length" class="warning-section">
            <div class="warning-section-head">
              <span class="section-tag repair">修复节点消耗</span>
              <span class="section-count">{{ baselineSplit.repair.reduce((sum, g) => sum + g.count, 0) }} 条</span>
            </div>
            <ul class="warning-groups">
              <li v-for="group in baselineSplit.repair" :key="`b-repair-${group.code}`" class="warning-group is-repair">
                <div class="warning-group-head">
                  <span class="warning-code-label">{{ group.meta.label }}</span>
                  <span class="warning-count">×{{ group.count }}</span>
                </div>
                <div v-if="group.sentenceIds.length" class="warning-sids">
                  <span class="warning-sids-label">影响：</span>
                  <span v-for="sid in compactSidList(group.sentenceIds).visible" :key="`b-repair-sid-${group.code}-${sid}`" class="sid-pill">{{ sid }}</span>
                  <span v-if="compactSidList(group.sentenceIds).overflow" class="sid-overflow">等 {{ compactSidList(group.sentenceIds).overflow }} 句</span>
                </div>
                <details class="warning-detail" :open="explainByDefault">
                  <summary>业务解释</summary>
                  <p class="warning-explanation">{{ group.meta.explanation }}</p>
                </details>
              </li>
            </ul>
          </div>
        </section>
        <p v-else class="empty-hint">该侧 workflow 无 warning。</p>
      </article>

      <!-- Candidate side -->
      <article class="health-pane is-candidate">
        <header class="pane-header">
          <div class="pane-title-group">
            <span class="pane-indicator"></span>
            <strong>Candidate</strong>
          </div>
          <div class="pane-head-chips">
            <span :class="`state-pill is-${candidate.userFacingTone.tone}`">
              {{ dash(candidate.userFacingTone.label, candidate.userFacingState) }}
            </span>
            <span :class="`repair-status is-${candidateRepair.tone}`" :title="candidateRepair.hint">
              repair · {{ candidateRepair.label }}
            </span>
          </div>
        </header>

        <dl class="metric-grid compact">
          <div>
            <dt>Warnings</dt>
            <dd :class="{ 'has-warn': candidate.warningCount > 0 }">{{ candidate.warningCount }}</dd>
          </div>
          <div>
            <dt>Drop</dt>
            <dd :class="{ 'has-warn': candidate.dropCount > 0 }">{{ candidate.dropCount }}</dd>
          </div>
          <div>
            <dt>Repair share</dt>
            <dd :class="`is-${shareTone(candidate.repairShare)}`">
              {{ sharePercent(candidate.repairShare) || "—" }}
            </dd>
          </div>
        </dl>

        <section class="agent-block">
          <div class="block-head">
            <strong>Per-agent token 消耗</strong>
            <small>完整 latency / total tokens 见上方运行概览</small>
          </div>
          <ul class="agent-list">
            <li
              v-for="agent in orderedPerAgent(candidate)"
              :key="`c-agent-${agent.name}`"
              :class="['agent-row', { 'is-repair': agent.name === 'repair', 'is-missing': agent.missing }]"
            >
              <span class="agent-name">
                {{ agentLabel(agent.name) }}
                <span v-if="agent.name === 'repair'" class="agent-tag">修复</span>
              </span>
              <span class="agent-value">
                <template v-if="!agent.missing">
                  <strong>{{ formatTokens(agent.total) }}</strong>
                  <small>入 {{ formatTokens(agent.input) }} / 出 {{ formatTokens(agent.output) }}</small>
                </template>
                <template v-else>
                  <strong>—</strong>
                  <small>本次无该节点</small>
                </template>
              </span>
            </li>
          </ul>
          <p v-if="candidateRepair.hint && candidateRepair.state !== 'not_triggered' && candidateRepair.state !== 'triggered_clean'" class="repair-hint" :class="`is-${candidateRepair.tone}`">
            {{ candidateRepair.hint }}
          </p>
        </section>

        <section v-if="candidate.warningsGrouped.groups.length" class="warning-block">
          <div class="block-head">
            <strong>后置校验与修复信号</strong>
            <small>按 code 聚合</small>
          </div>
          <div v-if="candidateSplit.postprocess.length" class="warning-section">
            <div class="warning-section-head">
              <span class="section-tag postprocess">后置校验结果</span>
              <span class="section-count">{{ candidateSplit.postprocess.reduce((sum, g) => sum + g.count, 0) }} 条</span>
            </div>
            <ul class="warning-groups">
              <li v-for="group in candidateSplit.postprocess" :key="`c-warn-${group.code}`" class="warning-group">
                <div class="warning-group-head">
                  <span class="warning-code-label">{{ group.meta.label }}</span>
                  <span class="warning-count">×{{ group.count }}</span>
                </div>
                <div v-if="group.sentenceIds.length" class="warning-sids">
                  <span class="warning-sids-label">影响：</span>
                  <span v-for="sid in compactSidList(group.sentenceIds).visible" :key="`c-sid-${group.code}-${sid}`" class="sid-pill">{{ sid }}</span>
                  <span v-if="compactSidList(group.sentenceIds).overflow" class="sid-overflow">等 {{ compactSidList(group.sentenceIds).overflow }} 句</span>
                </div>
                <details class="warning-detail" :open="explainByDefault">
                  <summary>业务解释与样例</summary>
                  <p class="warning-explanation">{{ group.meta.explanation }}</p>
                  <ul v-if="group.sampleMessages.length" class="warning-samples">
                    <li v-for="(sample, idx) in group.sampleMessages" :key="`c-sample-${group.code}-${idx}`">
                      <span v-if="sample.sentenceId" class="sid-pill is-mini">{{ sample.sentenceId }}</span>
                      <span>{{ shortMessage(sample.message) }}</span>
                    </li>
                  </ul>
                </details>
              </li>
            </ul>
          </div>
          <div v-if="candidateSplit.repair.length" class="warning-section">
            <div class="warning-section-head">
              <span class="section-tag repair">修复节点消耗</span>
              <span class="section-count">{{ candidateSplit.repair.reduce((sum, g) => sum + g.count, 0) }} 条</span>
            </div>
            <ul class="warning-groups">
              <li v-for="group in candidateSplit.repair" :key="`c-repair-${group.code}`" class="warning-group is-repair">
                <div class="warning-group-head">
                  <span class="warning-code-label">{{ group.meta.label }}</span>
                  <span class="warning-count">×{{ group.count }}</span>
                </div>
                <div v-if="group.sentenceIds.length" class="warning-sids">
                  <span class="warning-sids-label">影响：</span>
                  <span v-for="sid in compactSidList(group.sentenceIds).visible" :key="`c-repair-sid-${group.code}-${sid}`" class="sid-pill">{{ sid }}</span>
                  <span v-if="compactSidList(group.sentenceIds).overflow" class="sid-overflow">等 {{ compactSidList(group.sentenceIds).overflow }} 句</span>
                </div>
                <details class="warning-detail" :open="explainByDefault">
                  <summary>业务解释</summary>
                  <p class="warning-explanation">{{ group.meta.explanation }}</p>
                </details>
              </li>
            </ul>
          </div>
        </section>
        <p v-else class="empty-hint">该侧 workflow 无 warning。</p>
      </article>
    </div>

    <p class="panel-foot">
      <span class="foot-tag postprocess">后置校验</span>
      指 grammar / schema / anchor 校验节点触发的问题；不会回写到原文，表现为警告。
      <span class="foot-tag repair">修复节点</span>
      指 repair 节点介入后的额外消耗与失败信号；通常意味着上游 prompt 已不足以保证一致性。
    </p>
  </section>
</template>

<style scoped>
.workflow-health-panel {
  display: grid;
  gap: 12px;
  border: 1px solid var(--theme--border-color);
  border-radius: 12px;
  background: var(--theme--background-subdued);
  padding: 14px 16px;
}

.panel-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.panel-headline h3 {
  margin: 2px 0 0;
  font-size: 16px;
  font-weight: 700;
}

.section-kicker {
  margin: 0;
  color: var(--theme--foreground-subdued);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.panel-header-tally {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}

.tally-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 28px;
  padding: 0 12px;
  border: 1px solid var(--theme--border-color);
  border-radius: 999px;
  background: var(--theme--background);
  color: var(--theme--foreground-subdued);
  font-size: 11px;
  font-weight: 600;
}

.tally-pill strong {
  color: var(--theme--foreground);
  font-size: 12px;
  font-weight: 700;
}

.tally-pill.is-success {
  border-color: color-mix(in srgb, var(--theme--success) 45%, var(--theme--border-color));
  color: var(--theme--success);
}

.tally-pill.is-success strong {
  color: var(--theme--success);
}

.tally-pill.is-warning {
  border-color: color-mix(in srgb, var(--theme--warning) 40%, var(--theme--border-color));
  color: var(--theme--warning);
}

.tally-pill.is-warning strong {
  color: var(--theme--warning);
}

.tally-pill.is-danger {
  border-color: color-mix(in srgb, var(--theme--danger) 45%, var(--theme--border-color));
  color: var(--theme--danger);
}

.tally-pill.is-danger strong {
  color: var(--theme--danger);
}

.tally-pill.is-neutral strong {
  color: var(--theme--foreground-subdued);
}

.compare-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.delta-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  min-height: 28px;
  padding: 0 12px;
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
  background: var(--theme--background);
  font-size: 11px;
  color: var(--theme--foreground-subdued);
}

.delta-label {
  font-weight: 600;
}

.delta-chip strong {
  font-size: 13px;
  font-weight: 800;
  color: var(--theme--foreground);
}

.delta-chip.is-success {
  border-color: color-mix(in srgb, var(--theme--success) 40%, var(--theme--border-color));
}

.delta-chip.is-success strong {
  color: var(--theme--success);
}

.delta-chip.is-warning {
  border-color: color-mix(in srgb, var(--theme--warning) 40%, var(--theme--border-color));
}

.delta-chip.is-warning strong {
  color: var(--theme--warning);
}

.delta-chip.is-danger {
  border-color: color-mix(in srgb, var(--theme--danger) 45%, var(--theme--border-color));
}

.delta-chip.is-danger strong {
  color: var(--theme--danger);
}

.delta-codes {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-left: 4px;
}

.code-tag {
  display: inline-flex;
  align-items: center;
  padding: 0 6px;
  border-radius: 4px;
  font-size: 10px;
  font-weight: 700;
  border: 1px solid var(--theme--border-color);
  background: var(--theme--background-subdued);
  color: var(--theme--foreground-subdued);
}

.code-tag.is-postprocess {
  border-color: color-mix(in srgb, var(--theme--warning) 35%, var(--theme--border-color));
  color: var(--theme--warning);
}

.code-tag.is-repair {
  border-color: color-mix(in srgb, var(--theme--danger) 45%, var(--theme--border-color));
  color: var(--theme--danger);
}

.health-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.health-pane {
  position: relative;
  border: 1px solid var(--theme--border-color);
  border-radius: 10px;
  background: var(--theme--background);
  padding: 12px 14px;
  display: grid;
  gap: 10px;
  overflow: hidden;
}

.health-pane.is-baseline {
  border-top: 3px solid var(--theme--foreground-subdued);
}

.health-pane.is-candidate {
  border-top: 3px solid var(--theme--primary);
}

.pane-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
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

.health-pane.is-candidate .pane-indicator {
  background: var(--theme--primary);
}

.pane-head-chips {
  display: inline-flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
  justify-content: flex-end;
}

.state-pill,
.repair-status {
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  padding: 0 8px;
  border: 1px solid var(--theme--border-color);
  font-size: 10px;
  font-weight: 700;
  border-radius: 999px;
  background: var(--theme--background);
  color: var(--theme--foreground-subdued);
  white-space: nowrap;
}

.state-pill.is-success {
  color: var(--theme--success);
  border-color: color-mix(in srgb, var(--theme--success) 45%, var(--theme--border-color));
}

.state-pill.is-warning {
  color: var(--theme--warning);
  border-color: color-mix(in srgb, var(--theme--warning) 45%, var(--theme--border-color));
  background: color-mix(in srgb, var(--theme--warning) 8%, var(--theme--background));
}

.state-pill.is-danger {
  color: var(--theme--danger);
  border-color: color-mix(in srgb, var(--theme--danger) 45%, var(--theme--border-color));
  background: color-mix(in srgb, var(--theme--danger) 8%, var(--theme--background));
}

.repair-status.is-neutral {
  color: var(--theme--foreground-subdued);
}

.repair-status.is-warning {
  color: var(--theme--warning);
  border-color: color-mix(in srgb, var(--theme--warning) 45%, var(--theme--border-color));
  background: color-mix(in srgb, var(--theme--warning) 8%, var(--theme--background));
}

.repair-status.is-danger {
  color: var(--theme--danger);
  border-color: color-mix(in srgb, var(--theme--danger) 45%, var(--theme--border-color));
  background: color-mix(in srgb, var(--theme--danger) 8%, var(--theme--background));
}

.metric-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 1px;
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
  overflow: hidden;
  margin: 0;
}

.metric-grid.compact {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.metric-grid > div {
  background: var(--theme--background-subdued);
  padding: 8px 10px;
  min-width: 0;
}

.metric-grid dt {
  color: var(--theme--foreground-subdued);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.metric-grid dd {
  margin: 4px 0 0;
  font-size: 14px;
  font-weight: 700;
  color: var(--theme--foreground);
}

.metric-grid dd.has-warn {
  color: var(--theme--warning);
}

.metric-grid dd.is-warning {
  color: var(--theme--warning);
}

.metric-grid dd.is-danger {
  color: var(--theme--danger);
}

.metric-grid dd.is-neutral {
  color: var(--theme--foreground-subdued);
}

.agent-block,
.warning-block {
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
  background: var(--theme--background-subdued);
  padding: 8px 10px;
  display: grid;
  gap: 6px;
}

.block-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
}

.block-head strong {
  font-size: 12px;
  font-weight: 700;
  color: var(--theme--foreground);
}

.block-head small {
  color: var(--theme--foreground-subdued);
  font-size: 11px;
}

.agent-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 4px;
}

.agent-row {
  display: grid;
  grid-template-columns: minmax(64px, auto) minmax(0, 1fr);
  align-items: center;
  gap: 10px;
  padding: 5px 8px;
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  background: var(--theme--background);
}

.agent-row.is-repair {
  border-color: color-mix(in srgb, var(--theme--danger) 50%, var(--theme--border-color));
  background: color-mix(in srgb, var(--theme--danger) 6%, var(--theme--background));
}

.agent-row.is-missing {
  opacity: 0.6;
  border-style: dashed;
}

.agent-name {
  font-size: 12px;
  font-weight: 700;
  color: var(--theme--foreground);
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.agent-tag {
  display: inline-flex;
  align-items: center;
  padding: 0 6px;
  border: 1px solid color-mix(in srgb, var(--theme--danger) 50%, var(--theme--border-color));
  border-radius: 4px;
  background: var(--theme--background);
  color: var(--theme--danger);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.02em;
  text-transform: uppercase;
}

.agent-value {
  display: flex;
  align-items: baseline;
  gap: 6px;
  font-size: 12px;
  color: var(--theme--foreground);
  min-width: 0;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.agent-value strong {
  font-size: 13px;
  font-weight: 800;
  color: var(--theme--foreground);
}

.agent-value small {
  color: var(--theme--foreground-subdued);
  font-size: 10px;
  font-weight: 500;
}

.repair-hint {
  margin: 0;
  font-size: 12px;
  line-height: 1.55;
  color: var(--theme--danger);
  background: color-mix(in srgb, var(--theme--danger) 6%, var(--theme--background));
  border: 1px dashed color-mix(in srgb, var(--theme--danger) 50%, var(--theme--border-color));
  border-radius: 6px;
  padding: 6px 8px;
}

.repair-hint.is-warning {
  color: var(--theme--warning);
  background: color-mix(in srgb, var(--theme--warning) 6%, var(--theme--background));
  border-color: color-mix(in srgb, var(--theme--warning) 45%, var(--theme--border-color));
}

.warning-section {
  display: grid;
  gap: 6px;
}

.warning-section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.section-tag {
  display: inline-flex;
  align-items: center;
  min-height: 20px;
  padding: 0 8px;
  border-radius: 4px;
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  border: 1px solid var(--theme--border-color);
  background: var(--theme--background);
}

.section-tag.postprocess {
  color: var(--theme--foreground);
  border-color: color-mix(in srgb, var(--theme--warning) 45%, var(--theme--border-color));
  background: color-mix(in srgb, var(--theme--warning) 8%, var(--theme--background));
}

.section-tag.repair {
  color: var(--theme--danger);
  border-color: color-mix(in srgb, var(--theme--danger) 50%, var(--theme--border-color));
  background: color-mix(in srgb, var(--theme--danger) 10%, var(--theme--background));
}

.section-count {
  color: var(--theme--foreground-subdued);
  font-size: 11px;
  font-weight: 600;
}

.warning-groups {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 6px;
}

.warning-group {
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  background: var(--theme--background);
  padding: 6px 8px;
  display: grid;
  gap: 4px;
}

.warning-group.is-repair {
  border-color: color-mix(in srgb, var(--theme--danger) 50%, var(--theme--border-color));
  background: color-mix(in srgb, var(--theme--danger) 5%, var(--theme--background));
}

.warning-group-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}

.warning-code-label {
  font-size: 12px;
  font-weight: 700;
  color: var(--theme--foreground);
}

.warning-count {
  font-size: 11px;
  font-weight: 700;
  color: var(--theme--warning);
  background: color-mix(in srgb, var(--theme--warning) 10%, var(--theme--background));
  border: 1px solid color-mix(in srgb, var(--theme--warning) 35%, var(--theme--border-color));
  border-radius: 999px;
  padding: 1px 8px;
}

.warning-group.is-repair .warning-count {
  color: var(--theme--danger);
  background: color-mix(in srgb, var(--theme--danger) 10%, var(--theme--background));
  border-color: color-mix(in srgb, var(--theme--danger) 35%, var(--theme--border-color));
}

.warning-sids {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  color: var(--theme--foreground-subdued);
}

.warning-sids-label {
  font-weight: 600;
}

.sid-pill {
  display: inline-flex;
  align-items: center;
  padding: 1px 6px;
  border: 1px solid var(--theme--border-color);
  border-radius: 4px;
  font-family: var(--theme--fonts--monospace--font-family, monospace);
  font-size: 10px;
  font-weight: 700;
  background: var(--theme--background-subdued);
  color: var(--theme--foreground);
}

.sid-pill.is-mini {
  font-size: 9px;
  padding: 0 4px;
}

.sid-overflow {
  font-size: 10px;
  color: var(--theme--foreground-subdued);
  font-style: italic;
}

.warning-detail {
  font-size: 11px;
  color: var(--theme--foreground-subdued);
}

.warning-detail summary {
  cursor: pointer;
  font-weight: 600;
  user-select: none;
  list-style: none;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.warning-detail summary::-webkit-details-marker {
  display: none;
}

.warning-detail summary::before {
  content: "▸";
  font-size: 9px;
  color: var(--theme--foreground-subdued);
}

.warning-detail[open] summary::before {
  content: "▾";
}

.warning-explanation {
  margin: 4px 0 0;
  font-size: 12px;
  line-height: 1.55;
  color: var(--theme--foreground);
}

.warning-samples {
  list-style: none;
  padding: 4px 0 0;
  margin: 0;
  display: grid;
  gap: 4px;
}

.warning-samples li {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 6px;
  color: var(--theme--foreground);
  font-size: 11px;
  line-height: 1.55;
}

.empty-hint {
  margin: 0;
  font-size: 12px;
  color: var(--theme--foreground-subdued);
  border: 1px dashed var(--theme--border-color);
  border-radius: 6px;
  padding: 6px 8px;
  background: var(--theme--background);
}

.panel-foot {
  margin: 0;
  font-size: 11px;
  color: var(--theme--foreground-subdued);
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  line-height: 1.6;
}

.foot-tag {
  display: inline-flex;
  align-items: center;
  padding: 0 6px;
  border-radius: 4px;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  border: 1px solid var(--theme--border-color);
  background: var(--theme--background);
}

.foot-tag.postprocess {
  color: var(--theme--warning);
  border-color: color-mix(in srgb, var(--theme--warning) 45%, var(--theme--border-color));
  background: color-mix(in srgb, var(--theme--warning) 8%, var(--theme--background));
}

.foot-tag.repair {
  color: var(--theme--danger);
  border-color: color-mix(in srgb, var(--theme--danger) 45%, var(--theme--border-color));
  background: color-mix(in srgb, var(--theme--danger) 8%, var(--theme--background));
}

@media (max-width: 900px) {
  .health-grid {
    grid-template-columns: 1fr;
  }
  .metric-grid,
  .metric-grid.compact {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
