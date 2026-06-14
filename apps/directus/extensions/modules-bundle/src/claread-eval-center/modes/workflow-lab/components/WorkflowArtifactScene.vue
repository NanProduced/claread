<script setup>
import { computed } from "vue";
import ResultBlock from "../../../components/ResultBlock.vue";
import JsonTreeView from "../../../components/JsonTreeView.vue";
import AnchorDebugPanel from "./AnchorDebugPanel.vue";
import {
  dash,
  inlineMarkAnchorText,
  inlineMarkDisplayTitle,
  inlineMarkPrimarySummary,
  inlineMarkSecondarySummary,
  inlineMarkShowsDistinctAnchor,
  normalizeWorkflowScene,
  sceneInlineMarks,
  sceneSentenceEntries,
  sceneTranslations,
  sceneWarnings,
  extractLLMConfigSnapshot,
} from "../composables/workflowLabFormatting.js";

const props = defineProps({
  payload: { type: Object, default: null },
  title: { type: String, default: "结构化输出" },
  emptyText: { type: String, default: "当前没有可展示的 workflow 输出。" },
  showDebug: { type: Boolean, default: false },
  compact: { type: Boolean, default: false },
});

const scene = computed(() => normalizeWorkflowScene(props.payload));
const llmConfig = computed(() => extractLLMConfigSnapshot(props.payload));
const translations = computed(() => sceneTranslations(scene.value));
const inlineMarks = computed(() => sceneInlineMarks(scene.value));
const sentenceEntries = computed(() => sceneSentenceEntries(scene.value));
const warnings = computed(() => sceneWarnings(scene.value));
const dropLog = computed(() => Array.isArray(props.payload?.drop_log) ? props.payload.drop_log : []);

function annotationTypeLabel(mark) {
  switch (mark?.annotation_type) {
    case "vocab_highlight":
      return "词汇";
    case "phrase_gloss":
      return "短语";
    case "context_gloss":
      return "语境";
    case "grammar_note":
      return "语法";
    default:
      return mark?.annotation_type || "标注";
  }
}

function markTitle(mark) {
  return inlineMarkDisplayTitle(mark);
}

function markAnchor(mark) {
  return inlineMarkAnchorText(mark);
}

function markShowAnchor(mark) {
  return inlineMarkShowsDistinctAnchor(mark);
}

function markSummary(mark) {
  return inlineMarkPrimarySummary(mark) || mark?.visual_tone || "未提供说明";
}

function markDetail(mark) {
  return inlineMarkSecondarySummary(mark);
}
</script>

<template>
  <section class="artifact-scene">
    <div v-if="!scene" class="empty-state">
      <p>{{ emptyText }}</p>
    </div>
    <template v-else>
      <header class="scene-header">
        <div>
          <p>{{ title }}</p>
          <h3>{{ dash(scene.title || scene.user_facing_state, "结构化结果") }}</h3>
        </div>
        <dl class="scene-meta">
          <div><dt>状态</dt><dd>{{ dash(scene.user_facing_state) }}</dd></div>
          <div><dt>翻译</dt><dd>{{ translations.length }}</dd></div>
          <div><dt>标注</dt><dd>{{ inlineMarks.length }}</dd></div>
          <div><dt>句子条目</dt><dd>{{ sentenceEntries.length }}</dd></div>
        </dl>
      </header>

      <div class="scene-sections" :class="{ compact }">
        <section class="scene-section">
          <div class="section-head">
            <strong>逐句翻译</strong>
            <small>{{ translations.length }} 条</small>
          </div>
          <div v-if="translations.length" class="card-list">
            <article v-for="(item, index) in translations" :key="`translation-${index}`" class="scene-card">
              <strong>{{ dash(item.sentence_id, `句子 ${index + 1}`) }}</strong>
              <p>{{ dash(item.translation_zh || item.translation) }}</p>
            </article>
          </div>
          <p v-else class="empty-line">暂无逐句翻译。</p>
        </section>

        <section class="scene-section">
          <div class="section-head">
            <strong>行内标注</strong>
            <small>{{ inlineMarks.length }} 条</small>
          </div>
          <div v-if="inlineMarks.length" class="card-list">
            <article v-for="(item, index) in inlineMarks" :key="`mark-${index}`" class="scene-card">
              <div class="card-row">
                <strong>{{ dash(markTitle(item)) }}</strong>
                <span>{{ dash(annotationTypeLabel(item)) }}</span>
              </div>
              <p v-if="markShowAnchor(item)" class="anchor-line">原文锚点：{{ markAnchor(item) }}</p>
              <p>{{ dash(markSummary(item), "未提供说明") }}</p>
              <p v-if="markDetail(item)" class="card-detail">{{ markDetail(item) }}</p>
            </article>
          </div>
          <p v-else class="empty-line">暂无行内标注。</p>
        </section>

        <section class="scene-section">
          <div class="section-head">
            <strong>句子条目</strong>
            <small>{{ sentenceEntries.length }} 条</small>
          </div>
          <div v-if="sentenceEntries.length" class="card-list">
            <article v-for="(item, index) in sentenceEntries" :key="`entry-${index}`" class="scene-card">
              <div class="card-row">
                <strong>{{ dash(item.label || item.title, `条目 ${index + 1}`) }}</strong>
                <span>{{ dash(item.entry_type) }}</span>
              </div>
              <p>{{ dash(item.content) }}</p>
            </article>
          </div>
          <p v-else class="empty-line">暂无句子条目。</p>
        </section>
      </div>

      <section class="quality-strip">
        <div>
          <dt>提醒</dt>
          <dd>{{ warnings.length }}</dd>
        </div>
        <div>
          <dt>丢弃记录</dt>
          <dd>{{ dropLog.length }}</dd>
        </div>
      </section>

      <ResultBlock v-if="warnings.length || dropLog.length" title="质量信号" :open="false">
        <div class="quality-panels">
          <div class="quality-panel">
            <strong>提醒</strong>
            <ul v-if="warnings.length">
              <li v-for="(item, index) in warnings" :key="`warning-${index}`">
                {{ item.code || item.level || "warning" }}: {{ item.message || JSON.stringify(item) }}
              </li>
            </ul>
            <p v-else>暂无 warnings。</p>
          </div>
          <div class="quality-panel">
            <strong>丢弃记录</strong>
            <ul v-if="dropLog.length">
              <li v-for="(item, index) in dropLog" :key="`drop-${index}`">
                {{ item.code || item.reason || "drop" }}: {{ item.message || item.anchor_text || JSON.stringify(item) }}
              </li>
            </ul>
            <p v-else>暂无 drop log。</p>
          </div>
        </div>
      </ResultBlock>

      <ResultBlock v-if="llmConfig" title="LLM 配置" :open="false">
        <dl class="llm-config-grid">
          <div><dt>Profile</dt><dd>{{ dash(llmConfig.profile) }}</dd></div>
          <div><dt>Provider</dt><dd>{{ dash(llmConfig.provider) }}</dd></div>
          <div><dt>Adapter</dt><dd>{{ dash(llmConfig.adapter) }}</dd></div>
          <div><dt>Model</dt><dd>{{ dash(llmConfig.model) }}</dd></div>
          <div><dt>tool_choice_required</dt><dd>{{ llmConfig.openai_supports_tool_choice_required === true ? '是' : llmConfig.openai_supports_tool_choice_required === false ? '否' : dash(llmConfig.openai_supports_tool_choice_required) }}</dd></div>
          <div><dt>expected_tool_choice</dt><dd>{{ dash(llmConfig.expected_tool_choice) }}</dd></div>
          <div><dt>json_schema</dt><dd>{{ llmConfig.supports_json_schema_output === true ? '是' : llmConfig.supports_json_schema_output === false ? '否' : dash(llmConfig.supports_json_schema_output) }}</dd></div>
          <div><dt>json_object</dt><dd>{{ llmConfig.supports_json_object_output === true ? '是' : llmConfig.supports_json_object_output === false ? '否' : dash(llmConfig.supports_json_object_output) }}</dd></div>
          <div><dt>structured_output_mode</dt><dd>{{ dash(llmConfig.default_structured_output_mode) }}</dd></div>
          <div><dt>thinking</dt><dd>{{ llmConfig.thinking_enabled ? '开启' : '关闭' }}</dd></div>
        </dl>
      </ResultBlock>

      <ResultBlock title="Anchor Debug" :open="false">
        <AnchorDebugPanel :payload="payload" />
      </ResultBlock>

      <ResultBlock v-if="showDebug" title="完整 JSON" :open="false">
        <JsonTreeView :value="payload || {}" label="workflow_payload" />
      </ResultBlock>
    </template>
  </section>
</template>

<style scoped>
.artifact-scene {
  display: grid;
  gap: 14px;
}

.scene-header {
  display: grid;
  gap: 12px;
}

.scene-header p,
.scene-meta dt,
.empty-line,
.quality-panel p,
.quality-panel li,
.empty-state {
  margin: 0;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
}

.scene-header h3 {
  margin: 2px 0 0;
  font-size: 18px;
}

.scene-meta {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  gap: 1px;
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
  overflow: hidden;
}

.scene-meta div,
.quality-strip div {
  background: var(--theme--background-subdued);
  padding: 10px;
}

.scene-meta dd,
.quality-strip dd {
  margin: 4px 0 0;
  font-size: 14px;
  font-weight: 700;
}

.scene-sections {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}

.scene-sections.compact {
  grid-template-columns: 1fr;
}

.scene-section {
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
  background: var(--theme--background);
  padding: 12px;
  display: grid;
  gap: 10px;
}

.section-head,
.card-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
}

.section-head small,
.card-row span {
  color: var(--theme--foreground-subdued);
  font-size: 11px;
}

.card-list {
  display: grid;
  gap: 8px;
}

.scene-card {
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
  background: var(--theme--background-subdued);
  padding: 10px;
  display: grid;
  gap: 6px;
}

.scene-card p {
  margin: 0;
  line-height: 1.55;
}

.scene-card .anchor-line,
.scene-card .card-detail {
  color: var(--theme--foreground-subdued);
  font-size: 12px;
}

.quality-strip {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 1px;
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
  overflow: hidden;
}

.quality-panels {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.quality-panel {
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
  padding: 12px;
  background: var(--theme--background-subdued);
}

.quality-panel ul {
  margin: 8px 0 0;
  padding-left: 18px;
}

.quality-panel li + li {
  margin-top: 6px;
}

.empty-state {
  border: 1px dashed var(--theme--border-color);
  border-radius: 8px;
  padding: 18px;
  background: var(--theme--background-subdued);
}

.llm-config-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  gap: 1px;
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
  overflow: hidden;
}

.llm-config-grid div {
  background: var(--theme--background-subdued);
  padding: 10px;
}

.llm-config-grid dd {
  margin: 4px 0 0;
  font-size: 14px;
  font-weight: 700;
}

@media (max-width: 980px) {
  .scene-sections,
  .quality-panels {
    grid-template-columns: 1fr;
  }
}
</style>
