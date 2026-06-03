<script setup>
import { computed, ref } from "vue";
import JsonTreeView from "../../../components/JsonTreeView.vue";

const props = defineProps({
  drafts: { type: Array, default: () => [] },
  readyCandidates: { type: Array, default: () => [] },
  selectedId: { type: String, default: "" },
  form: { type: Object, required: true },
  preview: { type: Object, default: null },
  loading: { type: Boolean, default: false },
  saving: { type: Boolean, default: false },
  previewing: { type: Boolean, default: false },
  error: { type: String, default: "" },
  message: { type: String, default: "" },
});
const emit = defineEmits([
  "refresh",
  "new",
  "select",
  "update:form",
  "create-from-baseline",
  "preview",
  "save",
]);

const AGENTS = [
  { key: "vocabulary", label: "词汇" },
  { key: "grammar", label: "语法" },
  { key: "translation", label: "翻译" },
  { key: "repair", label: "修复" },
];
const activeAgent = ref("vocabulary");

const goalOptions = [
  { value: "daily_reading", label: "日常阅读" },
  { value: "exam", label: "考试阅读" },
];
const variantOptions = computed(() => props.form.reading_goal === "exam"
  ? [
    { value: "gaokao", label: "高考" },
    { value: "cet", label: "CET" },
    { value: "kaoyan", label: "考研" },
    { value: "tem", label: "TEM" },
    { value: "ielts_toefl", label: "IELTS / TOEFL" },
  ]
  : [
    { value: "beginner_reading", label: "入门阅读" },
    { value: "intermediate_reading", label: "进阶阅读" },
    { value: "intensive_reading", label: "精读" },
  ]);
const fewShotOptions = [
  { value: "baseline", label: "沿用 baseline examples" },
  { value: "off", label: "关闭 few-shot" },
  { value: "variant", label: "使用 Candidate examples" },
  { value: "settings", label: "沿用运行时设置" },
];

const activeLayer = computed(() => layerFor(activeAgent.value));
const baselineLayer = computed(() => baselineFor(activeAgent.value));
const hasBundle = computed(() => AGENTS.some((agent) => layerFor(agent.key).instructions.trim()));
const canSave = computed(() => props.form.variant_id?.trim() && hasBundle.value && !props.saving);
const changedAgents = computed(() => AGENTS.filter((agent) => isChanged(agent.key)).map((agent) => agent.label));

function layerFor(agentName) {
  return props.form.agents?.[agentName] || {
    agent_name: agentName,
    label: AGENTS.find((agent) => agent.key === agentName)?.label || agentName,
    instructions: "",
    policy_name: agentName === "repair" ? null : agentName,
    policy_focus: null,
    policy_variant: null,
    policy_lines: [],
    examples: [],
    prompt_template: "",
  };
}

function baselineFor(agentName) {
  return props.form.baseline_agents?.[agentName] || layerFor(agentName);
}

function updateForm(patch) {
  emit("update:form", { ...props.form, ...patch });
}

function updateMeta(key, value) {
  const patch = { [key]: value };
  if (key === "reading_goal") {
    patch.reading_variant = value === "exam" ? "gaokao" : "intermediate_reading";
  }
  updateForm(patch);
}

function updateAgent(agentName, patch) {
  updateForm({
    agents: {
      ...(props.form.agents || {}),
      [agentName]: {
        ...layerFor(agentName),
        ...patch,
      },
    },
  });
}

function setPolicyLine(index, value) {
  const lines = [...(activeLayer.value.policy_lines || [])];
  lines[index] = value;
  updateAgent(activeAgent.value, { policy_lines: lines });
}

function addPolicyLine() {
  updateAgent(activeAgent.value, {
    policy_lines: [...(activeLayer.value.policy_lines || []), ""],
  });
}

function removePolicyLine(index) {
  const lines = [...(activeLayer.value.policy_lines || [])];
  lines.splice(index, 1);
  updateAgent(activeAgent.value, { policy_lines: lines });
}

function addExample() {
  const defaultType = activeAgent.value === "translation"
    ? "translation"
    : activeAgent.value === "vocabulary"
      ? "vocab"
      : "grammar";
  updateAgent(activeAgent.value, {
    examples: [
      ...(activeLayer.value.examples || []),
      { example_type: defaultType, sentence_text: "", output_fragment: "" },
    ],
  });
}

function updateExample(index, key, value) {
  const examples = [...(activeLayer.value.examples || [])];
  examples[index] = { ...(examples[index] || {}), [key]: value };
  updateAgent(activeAgent.value, { examples });
}

function removeExample(index) {
  const examples = [...(activeLayer.value.examples || [])];
  examples.splice(index, 1);
  updateAgent(activeAgent.value, { examples });
}

function updateExamplesRaw(value) {
  try {
    const parsed = JSON.parse(value || "[]");
    if (!Array.isArray(parsed)) throw new Error("Examples must be an array.");
    updateAgent(activeAgent.value, { examples: parsed });
  } catch (error) {
    window.alert(error.message || "Examples JSON 无法解析。");
  }
}

function resetAgent(agentName = activeAgent.value) {
  updateAgent(agentName, baselineFor(agentName));
}

function resetAll() {
  updateForm({ agents: { ...(props.form.baseline_agents || {}) } });
}

function isChanged(agentName) {
  return JSON.stringify(layerFor(agentName)) !== JSON.stringify(baselineFor(agentName));
}

function draftSubtitle(draft) {
  const manifest = draft.manifest_json || {};
  if (manifest.schema_version === "workflow-prompt-bundle-v1") {
    return `${draft.status} / ${manifest.reading_goal || "-"} / ${manifest.reading_variant || "-"}`;
  }
  return `${draft.status} / legacy`;
}
</script>

<template>
  <section class="candidate-panel">
    <aside class="candidate-list">
      <header>
        <div>
          <p>Candidate</p>
          <h2>Workflow prompt bundle</h2>
        </div>
        <button type="button" title="清空当前编辑区，准备创建新的 Candidate。" @click="emit('new')">新建</button>
      </header>

      <div class="draft-scroll">
        <button
          v-for="draft in drafts"
          :key="draft.id"
          type="button"
          class="draft-item"
          :class="{ active: draft.id === selectedId }"
          @click="emit('select', draft)"
        >
          <strong>{{ draft.variant_id }}</strong>
          <small>{{ draftSubtitle(draft) }}</small>
        </button>
        <p v-if="!loading && drafts.length === 0" class="empty">暂无 Candidate draft。</p>
      </div>

      <button type="button" :disabled="loading" title="刷新 Directus 中的 workflow candidate draft。" @click="emit('refresh')">
        {{ loading ? "刷新中" : "刷新列表" }}
      </button>
    </aside>

    <main class="candidate-editor">
      <div v-if="error" class="notice error">{{ error }}</div>
      <div v-if="message" class="notice success">{{ message }}</div>

      <section class="setup-strip">
        <label>
          <span title="Candidate 的稳定标识，只允许字母、数字、点、下划线和短横线。">Variant ID</span>
          <input :value="form.variant_id" @input="updateMeta('variant_id', $event.target.value)" />
        </label>
        <label>
          <span title="Workflow Lab 当前只允许 learning topology。">阅读目标</span>
          <select :value="form.reading_goal" @change="updateMeta('reading_goal', $event.target.value)">
            <option v-for="option in goalOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
          </select>
        </label>
        <label>
          <span title="用于解析 baseline prompt policy focus 与 examples variant。">阅读场景</span>
          <select :value="form.reading_variant" @change="updateMeta('reading_variant', $event.target.value)">
            <option v-for="option in variantOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
          </select>
        </label>
        <label>
          <span title="Candidate run 选择该草稿时会锁定 rag_mode=off。">Few-shot</span>
          <select :value="form.few_shot_mode" @change="updateMeta('few_shot_mode', $event.target.value)">
            <option v-for="option in fewShotOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
          </select>
        </label>
        <label>
          <span title="只有 ready_for_eval 会出现在运行入口。">状态</span>
          <select :value="form.status" @change="updateMeta('status', $event.target.value)">
            <option value="draft">draft</option>
            <option value="ready_for_eval">ready_for_eval</option>
            <option value="archived">archived</option>
          </select>
        </label>
        <button type="button" :disabled="loading" title="从 Claread 当前 baseline prompt 读取四个 workflow agent 的完整草稿。" @click="emit('create-from-baseline')">
          {{ loading ? "读取中" : "从 baseline 创建" }}
        </button>
      </section>

      <div v-if="!hasBundle" class="empty-state">
        先填写 Variant ID，并点击“从 baseline 创建”。Workflow Candidate 必须包含词汇、语法、翻译和修复四个 prompt layer。
      </div>

      <template v-else>
        <section class="agent-tabs" aria-label="Workflow prompt agents">
          <button
            v-for="agent in AGENTS"
            :key="agent.key"
            type="button"
            :class="{ active: activeAgent === agent.key, changed: isChanged(agent.key) }"
            @click="activeAgent = agent.key"
          >
            {{ agent.label }}
          </button>
        </section>

        <section class="editor-layout">
          <div class="agent-editor">
            <header>
              <div>
                <p>{{ activeLayer.agent_name }}</p>
                <h3>{{ activeLayer.label }} Prompt Layer</h3>
              </div>
              <div class="header-actions">
                <button type="button" title="把当前 agent 恢复为创建草稿时的 baseline 内容。" @click="resetAgent()">重置当前节点</button>
                <button type="button" title="把四个 agent 都恢复为 baseline 内容。" @click="resetAll">重置全部</button>
              </div>
            </header>

            <label>
              <span title="Agent system instructions。Workflow runtime 会通过 eval-only override 应用这里的内容。">Agent Instructions</span>
              <textarea
                :value="activeLayer.instructions"
                rows="9"
                spellcheck="false"
                @input="updateAgent(activeAgent, { instructions: $event.target.value })"
              />
            </label>

            <section class="line-editor">
              <div class="field-header">
                <span title="Runtime prompt 的 policy section。repair 当前没有 policy。">Policy Lines</span>
                <button type="button" :disabled="!activeLayer.policy_name" @click="addPolicyLine">新增行</button>
              </div>
              <p v-if="!activeLayer.policy_name" class="muted">repair agent 当前只使用 instructions 和错误上下文，不使用 policy lines。</p>
              <div v-for="(line, index) in activeLayer.policy_lines" :key="`policy-${index}`" class="line-row">
                <input :value="line" @input="setPolicyLine(index, $event.target.value)" />
                <button type="button" title="删除这条 policy line。" @click="removePolicyLine(index)">删除</button>
              </div>
            </section>

            <section class="example-editor">
              <div class="field-header">
                <span title="仅当 Few-shot 选择 Candidate examples 时，workflow runtime 才会使用这些 examples。">Examples</span>
                <button type="button" @click="addExample">新增 Example</button>
              </div>
              <div v-for="(example, index) in activeLayer.examples" :key="`example-${index}`" class="example-row">
                <div class="example-head">
                  <input :value="example.example_type" placeholder="example_type" @input="updateExample(index, 'example_type', $event.target.value)" />
                  <button type="button" title="删除这个 example。" @click="removeExample(index)">删除</button>
                </div>
                <input :value="example.sentence_text" placeholder="示例原句" @input="updateExample(index, 'sentence_text', $event.target.value)" />
                <textarea :value="example.output_fragment" rows="3" placeholder="输出片段" @input="updateExample(index, 'output_fragment', $event.target.value)" />
              </div>
              <p v-if="activeLayer.examples.length === 0" class="muted">当前 agent 没有 Candidate examples。</p>
              <details class="raw-json">
                <summary>Raw examples JSON</summary>
                <textarea
                  :value="JSON.stringify(activeLayer.examples || [], null, 2)"
                  rows="8"
                  spellcheck="false"
                  @change="updateExamplesRaw($event.target.value)"
                />
              </details>
            </section>
          </div>

          <aside class="baseline-reference">
            <section>
              <p>变更摘要</p>
              <strong>{{ changedAgents.length ? changedAgents.join(" / ") : "全部沿用 baseline" }}</strong>
              <small>{{ readyCandidates.length }} 条 ready candidate 可用于 Workflow run。</small>
            </section>
            <section>
              <p>Baseline {{ baselineLayer.label }}</p>
              <dl>
                <div><dt>Prompt version</dt><dd>{{ form.prompt_version || "-" }}</dd></div>
                <div><dt>Profile</dt><dd>{{ form.prompt_profile || "-" }}</dd></div>
                <div><dt>Policy</dt><dd>{{ baselineLayer.policy_focus || "-" }}</dd></div>
                <div><dt>Examples</dt><dd>{{ baselineLayer.examples?.length || 0 }}</dd></div>
              </dl>
              <details open>
                <summary>Baseline Instructions</summary>
                <pre>{{ baselineLayer.instructions }}</pre>
              </details>
              <details>
                <summary>Baseline Policy Lines</summary>
                <ol>
                  <li v-for="(line, index) in baselineLayer.policy_lines" :key="`baseline-policy-${index}`">{{ line }}</li>
                </ol>
              </details>
            </section>
          </aside>
        </section>
      </template>

      <footer class="editor-actions">
        <label>
          <span>备注</span>
          <input :value="form.notes" @input="updateMeta('notes', $event.target.value)" />
        </label>
        <div>
          <button type="button" :disabled="previewing || !canSave" @click="emit('preview')">
            {{ previewing ? "预览中" : "预览 Snapshot" }}
          </button>
          <button type="button" :disabled="!canSave" @click="emit('save')">
            {{ saving ? "保存中" : "保存 Candidate" }}
          </button>
        </div>
      </footer>

      <section v-if="preview" class="preview">
        <header>
          <strong>Snapshot {{ preview.snapshot_hash }}</strong>
          <code>{{ preview.recommended_manifest_path }}</code>
        </header>
        <JsonTreeView :value="preview.prompt_bundle_summary || preview.manifest_json" label="candidate_snapshot" />
      </section>
    </main>
  </section>
</template>

<style scoped>
.candidate-panel {
  display: grid;
  grid-template-columns: minmax(230px, 0.24fr) minmax(0, 1fr);
  gap: 14px;
}
.candidate-list,
.candidate-editor,
.baseline-reference,
.agent-editor {
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  background: var(--theme--background);
}
.candidate-list,
.candidate-editor {
  padding: 14px;
}
.candidate-list {
  display: grid;
  align-content: start;
  gap: 12px;
}
header,
.editor-actions,
.field-header,
.example-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}
header p,
small,
label span,
.empty,
.muted,
.baseline-reference p,
dt {
  margin: 0;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  font-weight: 700;
}
header h2,
header h3 {
  margin: 2px 0 0;
  font-size: 16px;
}
button,
input,
select,
textarea {
  min-height: 34px;
  border: 1px solid var(--theme--border-color);
  border-radius: 4px;
  background: var(--theme--background);
  color: var(--theme--foreground);
  font: inherit;
  padding: 6px 8px;
}
button {
  cursor: pointer;
  font-weight: 700;
}
button:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}
textarea {
  resize: vertical;
  font-family: var(--theme--fonts--monospace--font-family, monospace);
  font-size: 12px;
  line-height: 1.45;
}
.draft-scroll {
  display: grid;
  gap: 8px;
  max-height: 420px;
  overflow: auto;
}
.draft-item {
  display: block;
  width: 100%;
  text-align: left;
}
.draft-item.active {
  border-color: var(--theme--primary);
  background: var(--theme--background-subdued);
}
.draft-item strong,
.draft-item small {
  display: block;
  overflow-wrap: anywhere;
}
.notice {
  border-radius: 6px;
  margin-bottom: 12px;
  padding: 9px 10px;
}
.notice.error {
  background: var(--theme--danger-background);
}
.notice.success {
  background: var(--theme--success-background);
}
.setup-strip {
  display: grid;
  grid-template-columns: minmax(180px, 1.2fr) repeat(4, minmax(130px, 0.8fr)) auto;
  gap: 10px;
  align-items: end;
}
label {
  display: grid;
  gap: 6px;
}
.empty-state {
  border: 1px dashed var(--theme--border-color);
  border-radius: 6px;
  color: var(--theme--foreground-subdued);
  margin-top: 14px;
  padding: 18px;
}
.agent-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 14px;
}
.agent-tabs button.active {
  border-color: var(--theme--primary);
}
.agent-tabs button.changed::after {
  content: " *";
  color: var(--theme--primary);
}
.editor-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(260px, 0.32fr);
  gap: 14px;
  margin-top: 12px;
}
.agent-editor,
.baseline-reference {
  display: grid;
  gap: 14px;
  padding: 14px;
}
.header-actions,
.editor-actions div {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.line-editor,
.example-editor {
  display: grid;
  gap: 10px;
}
.line-row,
.example-row {
  display: grid;
  gap: 8px;
}
.line-row {
  grid-template-columns: minmax(0, 1fr) auto;
}
.example-row {
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  padding: 10px;
}
.example-head {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
}
.raw-json {
  border-top: 1px solid var(--theme--border-color);
  padding-top: 10px;
}
.baseline-reference {
  align-content: start;
}
.baseline-reference section {
  display: grid;
  gap: 8px;
}
.baseline-reference dl {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}
.baseline-reference dd {
  margin: 2px 0 0;
  overflow-wrap: anywhere;
}
pre {
  max-height: 260px;
  overflow: auto;
  white-space: pre-wrap;
  font-family: var(--theme--fonts--monospace--font-family, monospace);
  font-size: 12px;
}
ol {
  margin: 0;
  padding-left: 18px;
}
.editor-actions {
  align-items: end;
  border-top: 1px solid var(--theme--border-color);
  margin-top: 14px;
  padding-top: 14px;
}
.editor-actions label {
  min-width: min(460px, 100%);
}
.preview {
  border-top: 1px solid var(--theme--border-color);
  margin-top: 14px;
  padding-top: 14px;
}
.preview header {
  margin-bottom: 10px;
}
code {
  color: var(--theme--foreground-subdued);
  font-size: 12px;
}
@media (max-width: 1180px) {
  .candidate-panel,
  .editor-layout,
  .setup-strip {
    grid-template-columns: 1fr;
  }
}
</style>
