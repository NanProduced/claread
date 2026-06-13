<script setup>
import { computed, ref } from "vue";
import ResultBlock from "../../../components/ResultBlock.vue";
import {
  extractAnchorDetail,
  computeAnchorStats,
  filterInlineMarks,
  FILTER_GROUPS,
  FILTER_OPTIONS,
  dropReasonSeverity,
  dropReasonOf,
  dropStageOf,
  dropReasonLabel,
  annotationTypeLabel,
  annotationTypeTone,
  visualToneLabel,
} from "../composables/anchorDebugFormatting.js";
import { dash } from "../composables/workflowLabFormatting.js";

const props = defineProps({
  payload: { type: Object, default: null },
});

const activeFilter = ref("all");

const scene = computed(() => {
  if (!props.payload || typeof props.payload !== "object") return null;
  if (props.payload.render_scene && typeof props.payload.render_scene === "object") {
    return props.payload.render_scene;
  }
  if (Array.isArray(props.payload.inline_marks)) return props.payload;
  if (props.payload.output && Array.isArray(props.payload.output.inline_marks)) {
    return props.payload.output;
  }
  return null;
});

const inlineMarks = computed(() =>
  Array.isArray(scene.value?.inline_marks) ? scene.value.inline_marks : [],
);

const dropLog = computed(() =>
  Array.isArray(props.payload?.drop_log) ? props.payload.drop_log
    : Array.isArray(scene.value?.drop_log) ? scene.value.drop_log : [],
);

const canonicalDropLog = computed(() =>
  Array.isArray(props.payload?.canonical_drop_log) ? props.payload.canonical_drop_log
    : Array.isArray(scene.value?.canonical_drop_log) ? scene.value.canonical_drop_log : [],
);

const warnings = computed(() =>
  Array.isArray(props.payload?.warnings) ? props.payload.warnings
    : Array.isArray(scene.value?.warnings) ? scene.value.warnings : [],
);

const canonicalStats = computed(() =>
  props.payload?.annotation_stats?.canonical_stats
    ?? props.payload?.canonical_stats
    ?? scene.value?.canonical_stats
    ?? null,
);

const stats = computed(() =>
  computeAnchorStats(inlineMarks.value, dropLog.value, canonicalDropLog.value, warnings.value, canonicalStats.value),
);

const filteredItems = computed(() =>
  filterInlineMarks(inlineMarks.value, activeFilter.value, dropLog.value, canonicalDropLog.value, warnings.value),
);

const isDroppedOrWarningView = computed(() =>
  activeFilter.value === "dropped" || activeFilter.value === "warnings",
);

function markDetail(mark) {
  return extractAnchorDetail(mark);
}

function glossarySummary(mark) {
  const g = mark?.glossary;
  if (!g) return "";
  const parts = [];
  if (g.zh || g.gloss) parts.push(g.zh || g.gloss);
  if (g.reason) parts.push(g.reason);
  return parts.join(" / ");
}

function formatRange(r) {
  if (r.start != null && r.end != null) return `[${r.start}, ${r.end})`;
  return "";
}
</script>

<template>
  <section class="anchor-debug">
    <!-- Quality summary: composition -->
    <div class="quality-strip">
      <div class="strip-group">
        <div class="strip-group-label">标注构成</div>
        <div class="strip-cells">
          <div class="strip-item">
            <dt>总数</dt><dd>{{ stats.totalInlineMarks }}</dd>
          </div>
          <div class="strip-item">
            <dt>范围锚点</dt><dd>{{ stats.rangeCount }}</dd>
          </div>
          <div class="strip-item">
            <dt>多范围</dt><dd>{{ stats.multiRangeCount }}</dd>
          </div>
          <div class="strip-item">
            <dt>旧版文本</dt><dd>{{ stats.textCount }}</dd>
          </div>
          <div class="strip-item">
            <dt>旧版多文本</dt><dd>{{ stats.multiTextCount }}</dd>
          </div>
        </div>
      </div>
      <div class="strip-group strip-group--signals">
        <div class="strip-group-label">问题信号</div>
        <div class="strip-cells">
          <div class="strip-item strip-item--warn">
            <dt>提醒</dt><dd>{{ stats.warningCount }}</dd>
          </div>
          <div class="strip-item strip-item--warn">
            <dt>丢弃</dt><dd>{{ stats.dropCount }}</dd>
          </div>
          <div class="strip-item strip-item--danger">
            <dt>Canonical 丢弃</dt><dd>{{ stats.canonicalAnchorDropCount }}</dd>
          </div>
        </div>
      </div>
    </div>

    <!-- Filter bar with groups -->
    <div class="filter-bar">
      <div v-for="group in FILTER_GROUPS" :key="group.label" class="filter-group">
        <span class="filter-group-label">{{ group.label }}</span>
        <div class="filter-chips">
          <button
            v-for="opt in group.options"
            :key="opt.key"
            class="filter-chip"
            :class="{ active: activeFilter === opt.key }"
            @click="activeFilter = opt.key"
          >
            {{ opt.label }}
          </button>
        </div>
      </div>
    </div>

    <!-- Drop / Warning list -->
    <div v-if="isDroppedOrWarningView" class="debug-list">
      <article
        v-for="(item, index) in filteredItems"
        :key="`drop-${index}`"
        class="debug-card"
        :class="`severity-${dropReasonSeverity(dropReasonOf(item))}`"
      >
        <div class="card-row">
          <strong>{{ dropReasonLabel(dropReasonOf(item)) }}</strong>
          <span class="severity-badge" :class="`badge-${dropReasonSeverity(dropReasonOf(item))}`">
            {{ dropReasonSeverity(dropReasonOf(item)) === 'danger' ? '严重' : dropReasonSeverity(dropReasonOf(item)) === 'warning' ? '注意' : '信息' }}
          </span>
        </div>
        <dl class="detail-grid">
          <div v-if="item.annotation_type">
            <dt>类型</dt><dd>{{ annotationTypeLabel(item.annotation_type) }}</dd>
          </div>
          <div v-if="item.sentence_id">
            <dt>句子</dt><dd class="mono">{{ item.sentence_id }}</dd>
          </div>
          <div v-if="item.quote_text">
            <dt>原文引用</dt><dd>{{ item.quote_text }}</dd>
          </div>
          <div v-if="item.anchor_text">
            <dt>锚点文本</dt><dd>{{ item.anchor_text }}</dd>
          </div>
          <div v-if="dropReasonOf(item)">
            <dt>丢弃原因</dt><dd class="mono">{{ dropReasonOf(item) }}</dd>
          </div>
          <div v-if="dropStageOf(item)">
            <dt>阶段</dt><dd>{{ dropStageOf(item) }}</dd>
          </div>
          <div v-if="item.code">
            <dt>代码</dt><dd class="mono">{{ item.code }}</dd>
          </div>
          <div v-if="item.message">
            <dt>消息</dt><dd>{{ item.message }}</dd>
          </div>
        </dl>
      </article>
      <p v-if="!filteredItems.length" class="empty-line">当前筛选无结果。</p>
    </div>

    <!-- Inline mark detail list -->
    <div v-else class="debug-list">
      <article
        v-for="(mark, index) in filteredItems"
        :key="`mark-${index}`"
        class="debug-card"
        :class="`tone-${annotationTypeTone(mark.annotation_type)}`"
      >
        <div class="card-row">
          <strong>{{ dash(mark.lookup_text || mark.label || mark.title, '—') }}</strong>
          <span class="type-badge" :class="`badge-${annotationTypeTone(mark.annotation_type)}`">
            {{ annotationTypeLabel(mark.annotation_type) }}
          </span>
        </div>

        <dl class="detail-grid">
          <div>
            <dt>ID</dt><dd class="mono">{{ mark.id || '—' }}</dd>
          </div>
          <div>
            <dt>锚点类型</dt>
            <dd>
              <span class="kind-badge" :class="`kind-${markDetail(mark).kind}`">
                {{ markDetail(mark).kind }}
              </span>
            </dd>
          </div>
          <div>
            <dt>句子</dt><dd class="mono">{{ markDetail(mark).sentenceId || '—' }}</dd>
          </div>
          <div v-if="markDetail(mark).offsetUnit">
            <dt>偏移单位</dt><dd class="mono">{{ markDetail(mark).offsetUnit }}</dd>
          </div>
          <div v-if="mark.visual_tone">
            <dt>视觉色调</dt><dd>{{ visualToneLabel(mark.visual_tone) }}</dd>
          </div>
        </dl>

        <!-- Ranges -->
        <div v-if="markDetail(mark).ranges.length" class="ranges-section">
          <div
            v-for="(r, ri) in markDetail(mark).ranges"
            :key="ri"
            class="range-row"
          >
            <span v-if="markDetail(mark).ranges.length > 1" class="range-index">#{{ ri + 1 }}</span>
            <span v-if="r.role" class="range-role">{{ r.role }}</span>
            <code v-if="formatRange(r)" class="range-offset">{{ formatRange(r) }}</code>
            <span class="range-text">"{{ r.text }}"</span>
            <span v-if="r.sourceQuote" class="range-source">source: "{{ r.sourceQuote }}"</span>
            <span v-if="r.resolutionKind" class="range-resolution">{{ r.resolutionKind }}</span>
            <span v-if="r.occurrence" class="range-occurrence">occ: {{ r.occurrence }}</span>
          </div>
        </div>

        <!-- Glossary -->
        <div v-if="glossarySummary(mark)" class="glossary-line">
          {{ glossarySummary(mark) }}
        </div>
      </article>
      <p v-if="!filteredItems.length" class="empty-line">当前筛选无结果。</p>
    </div>

    <!-- Canonical stats detail -->
    <ResultBlock
      v-if="canonicalStats"
      title="Canonical stats 详情"
      :open="false"
    >
      <dl class="detail-grid">
        <div v-if="canonicalStats.canonical_normalized_counts">
          <dt>Normalized 计数</dt>
          <dd class="mono mono--block">{{ JSON.stringify(canonicalStats.canonical_normalized_counts, null, 2) }}</dd>
        </div>
        <div v-if="canonicalStats.canonical_span_count != null">
          <dt>Span 计数</dt><dd>{{ canonicalStats.canonical_span_count }}</dd>
        </div>
        <div v-if="canonicalStats.canonical_drop_counts_by_reason">
          <dt>按原因丢弃</dt>
          <dd class="mono mono--block">{{ JSON.stringify(canonicalStats.canonical_drop_counts_by_reason, null, 2) }}</dd>
        </div>
        <div v-if="canonicalStats.canonical_drop_counts_by_type">
          <dt>按类型丢弃</dt>
          <dd class="mono mono--block">{{ JSON.stringify(canonicalStats.canonical_drop_counts_by_type, null, 2) }}</dd>
        </div>
        <div v-if="canonicalStats.canonical_anchor_drop_summary">
          <dt>Anchor 丢弃摘要</dt>
          <dd class="mono mono--block">{{ JSON.stringify(canonicalStats.canonical_anchor_drop_summary, null, 2) }}</dd>
        </div>
      </dl>
    </ResultBlock>
  </section>
</template>

<style scoped>
.anchor-debug {
  /* Tone backgrounds: mix 12% tint with page background for light/dark adapt */
  --claread-vocab-bg: color-mix(in srgb, #155CFF 12%, var(--theme--background));
  --claread-vocab-fg: color-mix(in srgb, #155CFF 70%, var(--theme--foreground));
  --claread-phrase-bg: color-mix(in srgb, #8B6FBF 12%, var(--theme--background));
  --claread-phrase-fg: color-mix(in srgb, #8B6FBF 70%, var(--theme--foreground));
  --claread-context-bg: color-mix(in srgb, #4C91C2 12%, var(--theme--background));
  --claread-context-fg: color-mix(in srgb, #4C91C2 70%, var(--theme--foreground));
  --claread-grammar-bg: color-mix(in srgb, #746694 12%, var(--theme--background));
  --claread-grammar-fg: color-mix(in srgb, #746694 70%, var(--theme--foreground));
  --claread-analysis-bg: color-mix(in srgb, #3C8C68 12%, var(--theme--background));
  --claread-analysis-fg: color-mix(in srgb, #3C8C68 70%, var(--theme--foreground));
  --claread-danger-bg: color-mix(in srgb, #BE123C 12%, var(--theme--background));
  --claread-danger-fg: color-mix(in srgb, #BE123C 70%, var(--theme--foreground));
  --claread-danger-border: color-mix(in srgb, #BE123C 50%, var(--theme--foreground));
  --claread-warning-bg: color-mix(in srgb, #9A5B00 12%, var(--theme--background));
  --claread-warning-fg: color-mix(in srgb, #9A5B00 70%, var(--theme--foreground));
  --claread-warning-border: color-mix(in srgb, #9A5B00 50%, var(--theme--foreground));
  --claread-multi-range-bg: color-mix(in srgb, #3730A3 12%, var(--theme--background));
  --claread-multi-range-fg: color-mix(in srgb, #3730A3 70%, var(--theme--foreground));
  --claread-unknown-bg: color-mix(in srgb, #991B1B 12%, var(--theme--background));
  --claread-unknown-fg: color-mix(in srgb, #991B1B 70%, var(--theme--foreground));

  display: grid;
  gap: 12px;
}

/* ── Quality strip ──────────────────────────────────────── */

.quality-strip {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 12px;
}

.strip-group {
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
  overflow: hidden;
}

.strip-group-label {
  display: block;
  padding: 4px 10px;
  font-size: 11px;
  font-weight: 600;
  color: var(--theme--foreground-subdued);
  background: var(--theme--background-subdued);
  border-bottom: 1px solid var(--theme--border-color);
}

.strip-cells {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(80px, 1fr));
}

.strip-item {
  padding: 8px 10px;
}

.strip-item dt {
  color: var(--theme--foreground-subdued);
  font-size: 11px;
  margin: 0;
}

.strip-item dd {
  margin: 2px 0 0;
  font-size: 14px;
  font-weight: 700;
}

.strip-group--signals .strip-item--warn dd {
  color: var(--claread-warning-fg);
}

.strip-group--signals .strip-item--danger dd {
  color: var(--claread-danger-fg);
}

/* ── Filter bar ─────────────────────────────────────────── */

.filter-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
}

.filter-group {
  display: flex;
  align-items: center;
  gap: 4px;
}

.filter-group-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--theme--foreground-subdued);
  margin-right: 2px;
}

.filter-chips {
  display: flex;
  gap: 4px;
}

.filter-chip {
  border: 1px solid var(--theme--border-color);
  border-radius: 999px;
  padding: 3px 10px;
  font-size: 12px;
  background: var(--theme--background);
  color: var(--theme--foreground-subdued);
  cursor: pointer;
  transition: background 0.2s, color 0.2s, border-color 0.2s;
}

.filter-chip:hover {
  background: var(--theme--background-subdued);
}

.filter-chip.active {
  background: var(--theme--primary);
  color: var(--theme--background);
  border-color: var(--theme--primary);
}

/* ── Debug list ─────────────────────────────────────────── */

.debug-list {
  display: grid;
  gap: 8px;
}

.debug-card {
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
  background: var(--theme--background);
  padding: 10px 12px;
  display: grid;
  gap: 8px;
}

.debug-card.severity-danger {
  border-color: var(--claread-danger-border);
  background: var(--claread-danger-bg);
}

.debug-card.severity-warning {
  border-color: var(--claread-warning-border);
  background: var(--claread-warning-bg);
}

.debug-card.tone-vocab {
  background: var(--claread-vocab-bg);
}

.debug-card.tone-phrase {
  background: var(--claread-phrase-bg);
}

.debug-card.tone-context {
  background: var(--claread-context-bg);
}

.debug-card.tone-grammar {
  background: var(--claread-grammar-bg);
}

/* ── Card row ───────────────────────────────────────────── */

.card-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.card-row strong {
  font-size: 13px;
}

/* ── Badges ─────────────────────────────────────────────── */

.type-badge,
.severity-badge,
.kind-badge {
  font-size: 11px;
  font-weight: 600;
  padding: 1px 7px;
  border-radius: 999px;
}

/* Claread design system tokens */
.badge-vocab { background: var(--claread-vocab-bg); color: var(--claread-vocab-fg); }
.badge-phrase { background: var(--claread-phrase-bg); color: var(--claread-phrase-fg); }
.badge-context { background: var(--claread-context-bg); color: var(--claread-context-fg); }
.badge-grammar { background: var(--claread-grammar-bg); color: var(--claread-grammar-fg); }
.badge-analysis { background: var(--claread-analysis-bg); color: var(--claread-analysis-fg); }
.badge-default { background: var(--theme--background-subdued); color: var(--theme--foreground-subdued); }

.badge-danger { background: var(--claread-danger-bg); color: var(--claread-danger-fg); }
.badge-warning { background: var(--claread-warning-bg); color: var(--claread-warning-fg); }
.badge-neutral { background: var(--theme--background-subdued); color: var(--theme--foreground-subdued); }

.kind-range { background: var(--claread-vocab-bg); color: var(--claread-vocab-fg); }
.kind-multi_range { background: var(--claread-multi-range-bg); color: var(--claread-multi-range-fg); }
.kind-text { background: var(--theme--background-subdued); color: var(--theme--foreground-subdued); }
.kind-multi_text { background: var(--theme--background-subdued); color: var(--theme--foreground-subdued); }
.kind-unknown { background: var(--claread-unknown-bg); color: var(--claread-unknown-fg); }

/* ── Detail grid ────────────────────────────────────────── */

.detail-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 4px 12px;
  margin: 0;
}

.detail-grid div {
  display: flex;
  gap: 6px;
  align-items: baseline;
}

.detail-grid dt {
  color: var(--theme--foreground-subdued);
  font-size: 11px;
  min-width: 80px;
  flex-shrink: 0;
}

.detail-grid dd {
  margin: 0;
  font-size: 12px;
}

.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 11px;
}

.mono--block {
  white-space: pre-wrap;
  word-break: break-all;
}

/* ── Ranges section ─────────────────────────────────────── */

.ranges-section {
  display: grid;
  gap: 4px;
}

.range-row {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 6px;
  font-size: 12px;
}

.range-index {
  color: var(--theme--foreground-subdued);
  font-size: 11px;
  font-weight: 600;
}

.range-role {
  font-size: 11px;
  font-weight: 600;
  color: var(--theme--foreground-subdued);
}

.range-offset {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 11px;
  background: var(--theme--background-subdued);
  padding: 1px 5px;
  border-radius: 4px;
}

.range-text {
  color: var(--theme--foreground);
  overflow-wrap: anywhere;
}

.range-source {
  color: var(--theme--foreground-subdued);
  font-size: 11px;
  overflow-wrap: anywhere;
}

.range-resolution {
  font-size: 11px;
  color: var(--theme--foreground-subdued);
}

.range-occurrence {
  font-size: 11px;
  color: var(--theme--foreground-subdued);
}

/* ── Glossary ───────────────────────────────────────────── */

.glossary-line {
  color: var(--theme--foreground-subdued);
  font-size: 12px;
}

/* ── Empty ──────────────────────────────────────────────── */

.empty-line {
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  margin: 0;
}
</style>
