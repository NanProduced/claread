<script setup>
import JsonTreeView from "../../../components/JsonTreeView.vue";
import ResultBlock from "../../../components/ResultBlock.vue";

defineProps({
  artifact: { type: Object, default: null },
  compareCase: { type: Object, default: null },
  loading: { type: Boolean, default: false },
});

function list(value) {
  return Array.isArray(value) ? value : [];
}
</script>

<template>
  <aside class="inspector">
    <header>
      <p>证据 Inspector</p>
      <h2>{{ artifact?.case_id || compareCase?.case_id || "未选择 case" }}</h2>
    </header>

    <div v-if="loading" class="empty">正在读取证据...</div>
    <div v-else-if="!artifact && !compareCase" class="empty">从 run 或 compare report 中选择一个 case 查看 evidence。</div>
    <template v-else>
      <section v-if="compareCase" class="compare-evidence">
        <strong>{{ compareCase.verdict }}</strong>
        <p v-for="reason in compareCase.reasons || []" :key="reason">{{ reason }}</p>
      </section>

      <template v-if="artifact">
        <dl class="meta">
          <div><dt title="adapter 返回状态。">状态</dt><dd>{{ artifact.adapter_status || "-" }}</dd></div>
          <div><dt title="最终 render_scene 的用户可见状态。">输出状态</dt><dd>{{ artifact.user_facing_state || "-" }}</dd></div>
          <div><dt title="workflow 或投影阶段记录的 warning 数量。">Warnings</dt><dd>{{ list(artifact.warnings).length }}</dd></div>
          <div><dt title="normalize/ground 阶段丢弃的标注数量。">Drop log</dt><dd>{{ list(artifact.drop_log).length }}</dd></div>
        </dl>

        <ResultBlock title="翻译输出" :open="true">
          <JsonTreeView :value="list(artifact.translations).slice(0, 8)" label="translations" />
        </ResultBlock>
        <ResultBlock title="行内标注">
          <JsonTreeView :value="list(artifact.inline_marks).slice(0, 12)" label="inline_marks" />
        </ResultBlock>
        <ResultBlock title="句子条目">
          <JsonTreeView :value="list(artifact.sentence_entries).slice(0, 12)" label="sentence_entries" />
        </ResultBlock>
        <ResultBlock title="Warnings / Drop log">
          <JsonTreeView :value="{ warnings: artifact.warnings || [], drop_log: artifact.drop_log || [] }" label="quality_signals" />
        </ResultBlock>
        <ResultBlock title="完整 artifact">
          <JsonTreeView :value="artifact" label="case_artifact" />
        </ResultBlock>
      </template>
    </template>
  </aside>
</template>

<style scoped>
.inspector {
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  background: var(--theme--background);
  min-height: 520px;
  max-height: calc(100vh - 190px);
  overflow: auto;
  padding: 14px;
}
header p {
  margin: 0;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  font-weight: 700;
}
header h2 {
  margin: 2px 0 12px;
  font-size: 16px;
  overflow-wrap: anywhere;
}
.empty {
  color: var(--theme--foreground-subdued);
}
.compare-evidence {
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  margin-bottom: 12px;
  padding: 10px;
}
.compare-evidence strong {
  display: inline-block;
  border-radius: 999px;
  background: var(--theme--background-subdued);
  padding: 3px 7px;
}
.compare-evidence p {
  margin: 8px 0 0;
}
.meta {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 1px;
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  overflow: hidden;
}
.meta div {
  background: var(--theme--background-subdued);
  padding: 8px;
}
dt {
  color: var(--theme--foreground-subdued);
  font-size: 11px;
  font-weight: 700;
}
dd {
  margin: 3px 0 0;
}
</style>
