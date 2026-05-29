<script setup>
import { computed } from "vue";

const props = defineProps({
  value: {
    type: Object,
    default: null,
  },
});

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function presentValue(value) {
  if (value === undefined || value === null || value === "") return "未记录";
  if (value === true) return "是";
  if (value === false) return "否";
  return String(value);
}

function formatInteger(value) {
  return typeof value === "number" ? value.toLocaleString("zh-CN") : "未记录";
}

function formatMilliseconds(value) {
  if (typeof value !== "number" || Number.isNaN(value)) return "未记录";
  if (value >= 1000) return `${(value / 1000).toFixed(2)} 秒`;
  return `${value.toFixed(1)} ms`;
}

function roundScore(value) {
  if (typeof value !== "number" || Number.isNaN(value)) return "未记录";
  return value.toFixed(4);
}

function translateOutputType(outputType) {
  const map = {
    grammar_note: "语法讲解",
    sentence_analysis: "句子拆析",
  };
  return map[String(outputType || "")] || String(outputType || "未记录");
}

function translateSelectionMode(mode) {
  const map = {
    rag: "已命中",
    rag_fallback: "已回退",
    baseline: "基线",
    manual: "手动",
  };
  return map[String(mode || "")] || String(mode || "未记录");
}

function selectionTone(item) {
  if (item?.selection_mode === "rag") return "success";
  if (item?.fallback_reason) return "warning";
  return "neutral";
}

function translateFallbackReason(reason) {
  const raw = String(reason || "");
  if (!raw) return "";
  if (raw === "empty_candidates") return "未召回候选";
  if (raw === "low_confidence") return "候选分数低于阈值";
  if (raw === "no_input_sentences") return "没有可检索句子";
  if (raw.startsWith("retrieval_error")) return raw.replace("retrieval_error:", "检索异常：").trim();
  return raw;
}

function translateDropStage(stage) {
  const map = {
    confidence_filter: "置信度过滤",
    diversity_dedup: "多样性去重",
    budget_trim: "预算裁剪",
  };
  return map[String(stage || "")] || String(stage || "未记录");
}

function translateDropReason(reason) {
  const map = {
    below_confidence_threshold: "低于阈值",
    duplicate_label: "标签重复",
    duplicate_sentence: "原句重复",
    duplicate_tag_set: "语法标签重复",
    exceeds_injection_budget: "超出注入预算",
  };
  return map[String(reason || "")] || String(reason || "未记录");
}

function compactLabel(item) {
  const parts = [];
  if (item?.label) parts.push(item.label);
  const tags = asArray(item?.grammar_tags).filter(Boolean);
  if (tags.length) parts.push(tags.join(" / "));
  return parts.join(" · ");
}

function candidateMeta(item) {
  const bits = [];
  if (item?.reading_variant) bits.push(item.reading_variant);
  if (item?.output_type) bits.push(translateOutputType(item.output_type));
  return bits.join(" · ");
}

function formatOutputFragment(value) {
  if (!value) return "";
  const raw = String(value);
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return raw;
  }
}

const ragGroups = computed(() => {
  const grammar = props.value?.agents?.grammar;
  const source = grammar && typeof grammar === "object" ? grammar : {};
  const preferredOrder = ["grammar_note", "sentence_analysis"];
  const orderedKeys = preferredOrder.filter((key) => source[key]).concat(
    Object.keys(source).filter((key) => !preferredOrder.includes(key)),
  );

  return orderedKeys.map((outputType) => {
    const item = source[outputType] && typeof source[outputType] === "object" ? source[outputType] : {};
    const selectedExamples = asArray(item.selected_examples).map((example, index) => ({
      key: `${outputType}-selected-${example.example_id || index}`,
      id: example.example_id || "未记录",
      label: compactLabel(example),
      annScore: roundScore(example.ann_score),
      rerankScore: roundScore(example.rerank_score),
      sourceSentence: String(example.source_sentence || ""),
      outputFragment: formatOutputFragment(example.output_fragment),
      outputMissing: !String(example.source_sentence || "").trim() && !String(example.output_fragment || "").trim(),
      meta: candidateMeta(example),
    }));

    const annHits = asArray(item.ann_hits).map((candidate, index) => ({
      key: `${outputType}-ann-${candidate.example_id || index}`,
      id: candidate.example_id || "未记录",
      annScore: roundScore(candidate.ann_score),
      rerankScore: roundScore(candidate.rerank_score),
      label: compactLabel(candidate),
      meta: candidateMeta(candidate),
    }));

    const rerankHits = asArray(item.rerank_hits).map((candidate, index) => ({
      key: `${outputType}-rerank-${candidate.example_id || index}`,
      id: candidate.example_id || "未记录",
      annScore: roundScore(candidate.ann_score),
      rerankScore: roundScore(candidate.rerank_score),
      label: compactLabel(candidate),
      meta: candidateMeta(candidate),
    }));

    const droppedExamples = asArray(item.dropped_examples).map((candidate, index) => ({
      key: `${outputType}-drop-${candidate.example_id || index}`,
      id: candidate.example_id || "未记录",
      annScore: roundScore(candidate.ann_score),
      rerankScore: roundScore(candidate.rerank_score),
      label: compactLabel(candidate),
      meta: candidateMeta(candidate),
      stage: translateDropStage(candidate.drop_stage),
      reason: translateDropReason(candidate.drop_reason),
    }));

    return {
      key: outputType,
      label: translateOutputType(outputType),
      tone: selectionTone(item),
      selectionLabel: translateSelectionMode(item.selection_mode),
      fallbackReason: translateFallbackReason(item.fallback_reason),
      querySentenceId: item.query_sentence_id || "",
      querySentenceText: item.query_sentence_text || "",
      queryText: item.query_text || "",
      candidateSentenceIds: asArray(item.candidate_sentence_ids).filter(Boolean),
      selectedExamples,
      annHits,
      rerankHits,
      droppedExamples,
      facts: [
        { label: "命中样例", value: formatInteger(item.example_count) },
        { label: "ANN 命中", value: formatInteger(item.ann_hit_count) },
        { label: "Rerank 命中", value: formatInteger(item.rerank_hit_count) },
        { label: "淘汰项", value: formatInteger(droppedExamples.length) },
        { label: "置信度阈值", value: item.confidence_threshold != null ? String(item.confidence_threshold) : "未记录" },
        { label: "Embedding", value: formatMilliseconds(item.embedding_latency_ms) },
        { label: "ANN", value: formatMilliseconds(item.ann_latency_ms) },
        { label: "Rerank", value: formatMilliseconds(item.rerank_latency_ms) },
      ],
      hasSparseExamples: selectedExamples.some((example) => example.outputMissing),
    };
  });
});

const overviewFacts = computed(() => {
  const groups = ragGroups.value;
  return [
    { label: "输出类型", value: formatInteger(groups.length) },
    { label: "命中样例", value: formatInteger(groups.reduce((sum, item) => sum + item.selectedExamples.length, 0)) },
    { label: "ANN 候选", value: formatInteger(groups.reduce((sum, item) => sum + item.annHits.length, 0)) },
    { label: "Rerank 候选", value: formatInteger(groups.reduce((sum, item) => sum + item.rerankHits.length, 0)) },
    { label: "淘汰项", value: formatInteger(groups.reduce((sum, item) => sum + item.droppedExamples.length, 0)) },
  ];
});

const hasSparseExamples = computed(() => ragGroups.value.some((group) => group.hasSparseExamples));
</script>

<template>
  <div class="rag-shell">
    <div class="rag-overview-grid">
      <div v-for="fact in overviewFacts" :key="fact.label" class="rag-overview-item">
        <span>{{ fact.label }}</span>
        <strong>{{ fact.value }}</strong>
      </div>
    </div>

    <div v-if="hasSparseExamples" class="rag-note rag-note-warning">
      当前快照缺少部分命中样例正文或标签。若需完整回看原始 example，请使用包含 output fields 的新数据重新跑一条任务。
    </div>

    <section v-for="group in ragGroups" :key="group.key" class="rag-track">
      <div class="rag-track-head">
        <div class="rag-track-copy">
          <h3>{{ group.label }}</h3>
          <p v-if="group.fallbackReason">{{ group.fallbackReason }}</p>
          <p v-else-if="group.querySentenceText">{{ group.querySentenceText }}</p>
          <p v-else>当前没有可展示的检索句。</p>
        </div>
        <div class="rag-track-badges">
          <span class="rag-badge" :class="`rag-tone-${group.tone}`">{{ group.selectionLabel }}</span>
          <span class="rag-badge rag-tone-neutral">{{ group.selectedExamples.length }} 条命中</span>
        </div>
      </div>

      <div class="rag-track-facts">
        <div v-for="fact in group.facts" :key="`${group.key}-${fact.label}`" class="rag-track-fact">
          <span>{{ fact.label }}</span>
          <strong>{{ fact.value }}</strong>
        </div>
      </div>

      <div class="rag-track-layout">
        <div class="rag-main-column">
          <section class="rag-panel">
            <div class="rag-panel-head">
              <h4>检索请求</h4>
              <code v-if="group.querySentenceId" class="rag-code">{{ group.querySentenceId }}</code>
            </div>

            <div class="rag-query-block" v-if="group.querySentenceText">
              <div class="rag-mini-label">查询句</div>
              <p class="rag-query-text">{{ group.querySentenceText }}</p>
            </div>

            <div v-if="group.candidateSentenceIds.length" class="rag-query-block">
              <div class="rag-mini-label">候选句</div>
              <div class="rag-chip-row">
                <code v-for="sentenceId in group.candidateSentenceIds" :key="`${group.key}-${sentenceId}`" class="rag-chip-code">
                  {{ sentenceId }}
                </code>
              </div>
            </div>

            <details v-if="group.queryText" class="rag-query-details">
              <summary>查看 query_text</summary>
              <pre>{{ group.queryText }}</pre>
            </details>
          </section>

          <section class="rag-panel">
            <div class="rag-panel-head">
              <h4>命中样例</h4>
              <span class="rag-panel-count">{{ group.selectedExamples.length }} 条</span>
            </div>

            <div v-if="group.selectedExamples.length" class="rag-example-list">
              <article v-for="example in group.selectedExamples" :key="example.key" class="rag-example-item">
                <div class="rag-example-head">
                  <div class="rag-example-title">
                    <code class="rag-code">{{ example.id }}</code>
                    <span v-if="example.label" class="rag-example-label">{{ example.label }}</span>
                    <span v-if="example.meta" class="rag-example-meta">{{ example.meta }}</span>
                  </div>
                  <div class="rag-score-row">
                    <span>ANN {{ example.annScore }}</span>
                    <span>Rerank {{ example.rerankScore }}</span>
                  </div>
                </div>

                <div v-if="example.sourceSentence" class="rag-example-block">
                  <div class="rag-mini-label">原句样例</div>
                  <p class="rag-example-quote">{{ example.sourceSentence }}</p>
                </div>

                <div v-if="example.outputFragment" class="rag-example-block">
                  <div class="rag-mini-label">输出片段</div>
                  <pre>{{ example.outputFragment }}</pre>
                </div>

                <div v-if="example.outputMissing" class="rag-note rag-note-muted">
                  当前快照未附带样例正文，只记录了命中 ID 和分数。
                </div>
              </article>
            </div>
            <div v-else class="rag-empty">本次没有命中样例。</div>
          </section>
        </div>

        <div class="rag-side-column">
          <section class="rag-panel">
            <div class="rag-panel-head">
              <h4>ANN 召回</h4>
              <span class="rag-panel-count">{{ group.annHits.length }} 条</span>
            </div>

            <table v-if="group.annHits.length" class="rag-table">
              <thead>
                <tr>
                  <th>样例</th>
                  <th>ANN</th>
                  <th>标签</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="candidate in group.annHits" :key="candidate.key">
                  <td><code class="rag-code">{{ candidate.id }}</code></td>
                  <td>{{ candidate.annScore }}</td>
                  <td class="rag-table-detail">
                    <div v-if="candidate.label">{{ candidate.label }}</div>
                    <div v-if="candidate.meta" class="rag-table-muted">{{ candidate.meta }}</div>
                  </td>
                </tr>
              </tbody>
            </table>
            <div v-else class="rag-empty">没有 ANN 候选。</div>
          </section>

          <section class="rag-panel">
            <div class="rag-panel-head">
              <h4>Rerank 结果</h4>
              <span class="rag-panel-count">{{ group.rerankHits.length }} 条</span>
            </div>

            <table v-if="group.rerankHits.length" class="rag-table">
              <thead>
                <tr>
                  <th>样例</th>
                  <th>Rerank</th>
                  <th>标签</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="candidate in group.rerankHits" :key="candidate.key">
                  <td><code class="rag-code">{{ candidate.id }}</code></td>
                  <td>{{ candidate.rerankScore }}</td>
                  <td class="rag-table-detail">
                    <div v-if="candidate.label">{{ candidate.label }}</div>
                    <div v-if="candidate.meta" class="rag-table-muted">{{ candidate.meta }}</div>
                  </td>
                </tr>
              </tbody>
            </table>
            <div v-else class="rag-empty">没有进入 Rerank 的候选。</div>
          </section>

          <section v-if="group.droppedExamples.length" class="rag-panel">
            <div class="rag-panel-head">
              <h4>淘汰项</h4>
              <span class="rag-panel-count">{{ group.droppedExamples.length }} 条</span>
            </div>

            <ul class="rag-drop-list">
              <li v-for="candidate in group.droppedExamples" :key="candidate.key" class="rag-drop-item">
                <div class="rag-drop-head">
                  <code class="rag-code">{{ candidate.id }}</code>
                  <div class="rag-drop-badges">
                    <span class="rag-badge rag-tone-warning">{{ candidate.stage }}</span>
                    <span class="rag-badge rag-tone-neutral">{{ candidate.reason }}</span>
                  </div>
                </div>
                <div v-if="candidate.label" class="rag-drop-label">{{ candidate.label }}</div>
                <div class="rag-drop-meta">
                  <span v-if="candidate.annScore !== '未记录'">ANN {{ candidate.annScore }}</span>
                  <span v-if="candidate.rerankScore !== '未记录'">Rerank {{ candidate.rerankScore }}</span>
                  <span v-if="candidate.meta">{{ candidate.meta }}</span>
                </div>
              </li>
            </ul>
          </section>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.rag-shell,
.rag-main-column,
.rag-side-column,
.rag-example-list,
.rag-track,
.rag-panel {
  display: flex;
  flex-direction: column;
}

.rag-shell,
.rag-main-column,
.rag-side-column {
  gap: 16px;
}

.rag-track {
  gap: 18px;
  padding: 18px;
  border: 1px solid var(--theme--border-color-subdued, #e3e7ee);
  border-radius: 12px;
  background: var(--theme--background-normal, #ffffff);
}

.rag-overview-grid,
.rag-track-facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 10px;
}

.rag-overview-item,
.rag-track-fact {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 12px 14px;
  border: 1px solid var(--theme--border-color-subdued, #e3e7ee);
  border-radius: 10px;
  background: var(--theme--background-subdued, #fafbfc);
}

.rag-overview-item span,
.rag-track-fact span,
.rag-mini-label,
.rag-table th,
.rag-note {
  color: #4b5563;
}

.rag-overview-item span,
.rag-track-fact span,
.rag-mini-label,
.rag-table th {
  font-size: 0.75rem;
  line-height: 1.5;
  font-weight: 700;
}

.rag-overview-item strong,
.rag-track-fact strong,
.rag-panel-count,
.rag-badge,
.rag-code {
  font-variant-numeric: tabular-nums;
}

.rag-overview-item strong,
.rag-track-fact strong {
  color: var(--theme--foreground, #172940);
  font-size: 1rem;
  line-height: 1.25;
  font-weight: 700;
}

.rag-note {
  padding: 12px 14px;
  border-radius: 10px;
  border: 1px solid var(--theme--border-color-subdued, #e3e7ee);
  background: var(--theme--background-subdued, #fafbfc);
  font-size: 0.8125rem;
  line-height: 1.6;
}

.rag-note-warning {
  border-color: #ffd9a8;
  background: #fff7e8;
  color: #9a5b00;
}

.rag-note-muted {
  margin-top: 4px;
}

.rag-track-head,
.rag-panel-head,
.rag-example-head,
.rag-drop-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}

.rag-track-head,
.rag-panel-head {
  align-items: flex-start;
}

.rag-track-copy h3,
.rag-panel h4 {
  margin: 0;
  color: var(--theme--foreground, #172940);
}

.rag-track-copy h3 {
  font-size: 1rem;
  line-height: 1.4;
  font-weight: 700;
}

.rag-track-copy p {
  margin: 6px 0 0;
  color: var(--theme--foreground, #172940);
  font-size: 0.875rem;
  line-height: 1.65;
}

.rag-panel {
  gap: 14px;
  padding: 16px;
  border: 1px solid var(--theme--border-color-subdued, #e3e7ee);
  border-radius: 12px;
  background: var(--theme--background-subdued, #fafbfc);
}

.rag-panel h4 {
  font-size: 0.9375rem;
  line-height: 1.45;
  font-weight: 700;
}

.rag-badge {
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  padding: 0 10px;
  border-radius: 999px;
  border: 1px solid var(--theme--border-color, #d9dee7);
  font-size: 0.75rem;
  line-height: 1.45;
  font-weight: 700;
  white-space: nowrap;
}

.rag-tone-success {
  background: #ecfdf3;
  border-color: #b7ebcf;
  color: #11795b;
}

.rag-tone-warning {
  background: #fff7e8;
  border-color: #ffd9a8;
  color: #9a5b00;
}

.rag-tone-neutral {
  background: var(--theme--background-normal, #ffffff);
  border-color: var(--theme--border-color, #d9dee7);
  color: #4b5563;
}

.rag-track-badges,
.rag-drop-badges,
.rag-chip-row,
.rag-score-row,
.rag-drop-meta {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.rag-track-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.25fr) minmax(320px, 0.95fr);
  gap: 16px;
}

.rag-query-block,
.rag-example-block {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.rag-query-text,
.rag-example-quote {
  margin: 0;
  color: var(--theme--foreground, #172940);
  font-size: 0.9375rem;
  line-height: 1.7;
}

.rag-query-details,
.rag-query-details summary {
  margin: 0;
}

.rag-query-details summary {
  cursor: pointer;
  list-style: none;
  color: #245cb8;
  font-size: 0.8125rem;
  line-height: 1.5;
  font-weight: 700;
}

.rag-query-details summary::-webkit-details-marker {
  display: none;
}

.rag-query-details pre,
.rag-example-block pre {
  margin: 0;
  padding: 12px 14px;
  border: 1px solid var(--theme--border-color-subdued, #e3e7ee);
  border-radius: 10px;
  background: var(--theme--background-normal, #ffffff);
  color: var(--theme--foreground, #172940);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
  font-size: 0.8125rem;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

.rag-code,
.rag-chip-code {
  display: inline-flex;
  align-items: center;
  min-height: 24px;
  padding: 0 8px;
  border-radius: 8px;
  background: var(--theme--background-normal, #ffffff);
  color: var(--theme--foreground, #172940);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
  font-size: 0.75rem;
  line-height: 1.5;
  word-break: break-all;
}

.rag-example-list {
  gap: 12px;
}

.rag-example-item,
.rag-drop-item {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px;
  border: 1px solid var(--theme--border-color-subdued, #e3e7ee);
  border-radius: 10px;
  background: var(--theme--background-normal, #ffffff);
}

.rag-example-title {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.rag-example-label,
.rag-example-meta,
.rag-drop-label,
.rag-drop-meta,
.rag-table-muted {
  color: #4b5563;
}

.rag-example-label,
.rag-drop-label {
  font-size: 0.875rem;
  line-height: 1.55;
  font-weight: 700;
}

.rag-example-meta,
.rag-drop-meta,
.rag-table-muted,
.rag-empty {
  font-size: 0.8125rem;
  line-height: 1.6;
}

.rag-score-row {
  justify-content: flex-end;
  color: #4b5563;
  font-size: 0.75rem;
  line-height: 1.5;
  font-weight: 700;
}

.rag-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.875rem;
  line-height: 1.55;
}

.rag-table th,
.rag-table td {
  padding: 10px 12px;
  text-align: left;
  vertical-align: top;
  border-bottom: 1px solid var(--theme--border-color-subdued, #e3e7ee);
}

.rag-table tbody tr:last-child td {
  border-bottom: none;
}

.rag-table-detail {
  min-width: 0;
}

.rag-drop-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin: 0;
  padding: 0;
  list-style: none;
}

@media (max-width: 1280px) {
  .rag-track-layout {
    grid-template-columns: 1fr;
  }
}
</style>
