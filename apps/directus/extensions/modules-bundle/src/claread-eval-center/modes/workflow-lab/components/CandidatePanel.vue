<script setup>
import { computed, ref } from "vue";
import JsonTreeView from "../../../components/JsonTreeView.vue";
import { groupCandidatesByStatus } from "../composables/workflowLabFormatting.js";

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
  "save-draft",
  "publish",
  "unpublish",
  "go-to-single-run",
]);

const AGENTS = [
  { key: "vocabulary", label: "词汇" },
  { key: "grammar", label: "语法" },
  { key: "translation", label: "翻译" },
  { key: "repair", label: "修复" },
];

// 每个 agent 在串行 workflow 里负责什么、它的下游是谁
const AGENT_DESCRIPTIONS = {
  vocabulary: {
    step: "第 1 步 / 4 步",
    role: "在原文中识别生词、短语、文化语境词，输出 vocab highlight / phrase gloss / context gloss。",
    downstream: "下游:Grammar 会消费你的 anchor 标注,决定哪些句子值得做语法拆解。",
  },
  grammar: {
    step: "第 2 步 / 4 步",
    role: "在 vocabulary 标注过的句子上,识别语法结构,输出 grammar note / sentence analysis。",
    downstream: "下游:Translation 会消费你的 analysis,把分析结论带入翻译判断。",
  },
  translation: {
    step: "第 3 步 / 4 步",
    role: "基于 grammar 标记后的句子,产出逐句中文翻译。",
    downstream: "下游:Repair 会消费你的翻译,做质量修复(术语一致、可读性、漏译)。",
  },
  repair: {
    step: "第 4 步 / 4 步",
    role: "对 translation 后的结果做质量修复,主要使用 instructions 和错误上下文,不使用 policy lines。",
    downstream: "下游:workflow runtime 把 repair 后的 render_scene 落盘到 case artifact。",
  },
};

const STEP_DEFS = [
  { key: "name", label: "命名" },
  { key: "baseline", label: "从 baseline 创建" },
  { key: "edit", label: "编辑 agent" },
  { key: "publish", label: "发布到验证入口" },
];

const activeAgent = ref("vocabulary");
const examplesError = ref("");

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
const activeAgentColor = computed(() => {
  const colors = {
    vocabulary: '#e4b000',
    grammar: '#746694',
    translation: '#54a7de',
    repair: '#059669',
  };
  return colors[activeAgent.value] || 'var(--theme--primary)';
});
const baselineLayer = computed(() => baselineFor(activeAgent.value));
const hasBundle = computed(() => AGENTS.some((agent) => layerFor(agent.key).instructions.trim()));
const canSave = computed(() => props.form.variant_id?.trim() && hasBundle.value && !props.saving);
const isPublished = computed(() => props.form.status === "ready_for_eval");
const agentDiffs = computed(() => {
  const out = {};
  for (const agent of AGENTS) {
    const layer = layerFor(agent.key);
    const baseline = baselineFor(agent.key);
    const diffs = [];
    if ((layer.instructions || "") !== (baseline.instructions || "")) diffs.push("instructions");
    if (JSON.stringify(layer.policy_lines || []) !== JSON.stringify(baseline.policy_lines || [])) diffs.push("policy lines");
    if (JSON.stringify(layer.examples || []) !== JSON.stringify(baseline.examples || [])) diffs.push("examples");
    if (diffs.length) out[agent.key] = diffs;
  }
  return out;
});
const changedAgents = computed(() => Object.keys(agentDiffs.value));
const draftGroups = computed(() => groupCandidatesByStatus(props.drafts));
const currentStatusLabel = computed(() => props.form.status === "ready_for_eval"
  ? "已发布到验证入口"
  : props.form.status === "archived"
    ? "已归档"
    : "草稿");
const hasName = computed(() => Boolean((props.form.variant_id || "").trim()));
const hasEdit = computed(() => Object.keys(agentDiffs.value).length > 0);
const stepState = computed(() => ({
  name: hasName.value,
  baseline: hasBundle.value,
  edit: hasEdit.value,
  publish: isPublished.value,
}));
const currentStepIndex = computed(() => {
  if (!hasName.value) return 0;
  if (!hasBundle.value) return 1;
  if (!hasEdit.value) return 2;
  // 已发布 → 仍把"发布"作为 active 步骤(让最后一步保持高亮),用 done + active 组合
  return STEP_DEFS.length - 1;
});

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
  examplesError.value = "";
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
    examplesError.value = "";
    updateAgent(activeAgent.value, { examples: parsed });
  } catch (error) {
    examplesError.value = error.message || "Examples JSON 无法解析。";
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

function dimensionLabel(agentKey) {
  const diffs = agentDiffs.value[agentKey] || [];
  return diffs.length ? diffs.join(" · ") : "—";
}

function goToSingleRun() {
  if (!isPublished.value) return;
  emit("go-to-single-run", props.form.variant_id);
}

const publishTitle = "发布到验证入口后,本版本会出现在「单篇验证」候选选择器中;双跑完成后会直接物化 compare 记录。";
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
        <section class="draft-group">
          <div class="group-head">
            <strong>已发布</strong>
            <small>{{ draftGroups.published.length }}</small>
          </div>
          <button
            v-for="draft in draftGroups.published"
            :key="draft.id"
            type="button"
            class="draft-item"
            :class="{ active: draft.id === selectedId }"
            @click="emit('select', draft)"
          >
            <strong>{{ draft.variant_id }}</strong>
            <small>{{ draftSubtitle(draft) }}</small>
          </button>
          <p v-if="!loading && draftGroups.published.length === 0" class="empty">暂无已发布 Candidate。</p>
        </section>

        <section class="draft-group">
          <div class="group-head">
            <strong>草稿</strong>
            <small>{{ draftGroups.drafts.length }}</small>
          </div>
          <button
            v-for="draft in draftGroups.drafts"
            :key="draft.id"
            type="button"
            class="draft-item"
            :class="{ active: draft.id === selectedId }"
            @click="emit('select', draft)"
          >
            <strong>{{ draft.variant_id }}</strong>
            <small>{{ draftSubtitle(draft) }}</small>
          </button>
          <p v-if="!loading && draftGroups.drafts.length === 0" class="empty">暂无草稿 Candidate。</p>
        </section>
      </div>

      <button type="button" :disabled="loading" title="刷新 Directus 中的 workflow candidate draft。" @click="emit('refresh')">
        {{ loading ? "刷新中" : "刷新列表" }}
      </button>
    </aside>

    <main class="candidate-editor">
      <div v-if="error" class="notice error" aria-live="assertive">{{ error }}</div>
      <div v-if="message" class="notice success" aria-live="polite">{{ message }}</div>

      <section class="stepper" aria-label="候选版本步骤">
        <ol>
          <li
            v-for="(step, index) in STEP_DEFS"
            :key="step.key"
            :class="{
              done: stepState[step.key],
              active: index === currentStepIndex,
            }"
          >
            <span class="step-index">{{ index + 1 }}</span>
            <span class="step-label">{{ step.label }}</span>
            <span v-if="stepState[step.key]" class="step-check" aria-hidden="true">✓</span>
          </li>
        </ol>
      </section>

      <section class="pipeline" aria-label="workflow 串行结构">
        <header>
          <strong>Workflow 串行结构</strong>
          <small>点击任一节点切换编辑区,4 个 agent 按顺序消费上一阶段的输出,不是 4 个并列视角</small>
        </header>
        <ol>
          <li v-for="(agent, index) in AGENTS" :key="agent.key" class="pipeline-item">
            <button
              type="button"
              :class="['pipeline-step', { active: activeAgent === agent.key, changed: !!agentDiffs[agent.key] }]"
              :aria-pressed="activeAgent === agent.key"
              :aria-label="`切换到 ${AGENT_DESCRIPTIONS[agent.key]?.step || ''} ${agent.label}`"
              @click="activeAgent = agent.key"
            >
              <span class="pipeline-index" aria-hidden="true">{{ index + 1 }}</span>
              <span class="pipeline-label">{{ agent.label }}</span>
              <span v-if="agentDiffs[agent.key]" class="pipeline-diff-badge" :title="`已修改: ${agentDiffs[agent.key].join(', ')}`">改</span>
            </button>
            <span v-if="index < AGENTS.length - 1" class="pipeline-arrow" aria-hidden="true">→</span>
          </li>
        </ol>
      </section>

      <section v-if="isPublished && form.variant_id" class="published-cta" role="region" aria-label="已发布 CTA">
        <div>
          <strong>已发布到验证入口</strong>
          <small>本版本现在会出现在「单篇验证」候选选择器中;双跑完成后会直接进入 compare 结果与证据视图。</small>
        </div>
        <div class="published-cta-actions">
          <button type="button" class="primary-cta" @click="goToSingleRun">去单篇验证</button>
          <button type="button" class="ghost-cta" @click="emit('go-to-single-run', null)">留在候选版本</button>
        </div>
      </section>

      <section class="setup-strip">
        <label>
          <span title="Candidate 的稳定标识，只允许字母、数字、点、下划线和短横线。">Variant ID</span>
          <input :value="form.variant_id" @input="updateMeta('variant_id', $event.target.value)" />
        </label>
        <div class="status-panel">
          <span title="只有已发布到运行入口的 Candidate 才会出现在「单篇验证」候选选择器中。">当前状态</span>
          <strong>{{ currentStatusLabel }}</strong>
        </div>
        <button type="button" :disabled="loading" title="从 Claread 当前 baseline prompt 读取四个 workflow agent 的完整草稿。" @click="emit('create-from-baseline')">
          {{ loading ? "读取中" : "从 baseline 创建" }}
        </button>
      </section>

      <details class="advanced-settings">
        <summary>高级候选设置</summary>
        <div class="advanced-settings-grid">
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
            <span title="只有在想覆盖 baseline examples 时才需要改动。">Few-shot 方案</span>
            <select :value="form.few_shot_mode" @change="updateMeta('few_shot_mode', $event.target.value)">
              <option v-for="option in fewShotOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
            </select>
          </label>
        </div>
      </details>

      <div v-if="!hasBundle" class="empty-state">
        先给候选版本命名，再点“从 baseline 创建”。这样会自动生成四个 workflow 节点，之后你只需要改真正想实验的部分。
      </div>

      <template v-else>
        <section class="editor-layout">
          <div class="agent-editor">
            <header>
              <div>
                <p>{{ activeLayer.agent_name }} · {{ AGENT_DESCRIPTIONS[activeLayer.agent_name]?.step || "" }}</p>
                <h3>{{ activeLayer.label }} Prompt Layer</h3>
              </div>
              <div class="header-actions">
                <button type="button" title="把当前 agent 恢复为创建草稿时的 baseline 内容。" @click="resetAgent()">重置当前节点</button>
                <button type="button" title="把四个 agent 都恢复为 baseline 内容。" @click="resetAll">重置全部</button>
              </div>
            </header>

            <section class="agent-explainer" :style="{ '--agent-tone-color': activeAgentColor }">
              <div>
                <span class="explainer-label">负责</span>
                <p>{{ AGENT_DESCRIPTIONS[activeLayer.agent_name]?.role || "—" }}</p>
              </div>
              <div>
                <span class="explainer-label">下游</span>
                <p>{{ AGENT_DESCRIPTIONS[activeLayer.agent_name]?.downstream || "—" }}</p>
              </div>
            </section>

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
                <input :value="line" placeholder="输入 Policy Line 规则内容..." @input="setPolicyLine(index, $event.target.value)" />
                <button type="button" class="delete-line-btn" title="删除这条 policy line。" @click="removePolicyLine(index)">
                  <span class="delete-icon">×</span>
                </button>
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
                  <button type="button" class="delete-line-btn" title="删除这个 example。" @click="removeExample(index)">
                    <span class="delete-icon">×</span>
                  </button>
                </div>
                <input :value="example.sentence_text" class="sentence-input" placeholder="示例原句" @input="updateExample(index, 'sentence_text', $event.target.value)" />
                <textarea :value="example.output_fragment" class="output-textarea" rows="3" placeholder="输出片段" @input="updateExample(index, 'output_fragment', $event.target.value)" />
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
                <p v-if="examplesError" class="inline-error" aria-live="assertive">{{ examplesError }}</p>
              </details>
            </section>
          </div>

          <aside class="baseline-reference">
            <section class="ref-section current-diff-section">
              <p class="ref-title">当前修改状态</p>
              <div class="ref-card">
                <strong v-if="!changedAgents.length" class="no-diff-hint">全部沿用 baseline</strong>
                <ul v-else class="diff-list">
                  <li v-for="agentKey in changedAgents" :key="agentKey">
                    <span class="diff-agent">{{ AGENTS.find((a) => a.key === agentKey)?.label || agentKey }}</span>
                    <span class="diff-dims">{{ agentDiffs[agentKey].join(" · ") }}</span>
                  </li>
                </ul>
                <small class="ready-candidates-count">{{ readyCandidates.length }} 条已发布候选版本可直接用于验证与回归。</small>
              </div>
            </section>
            <section class="ref-section baseline-meta-section">
              <p class="ref-title">Baseline 参考</p>
              <div class="ref-card">
                <dl class="baseline-meta-grid">
                  <div><dt>Prompt version</dt><dd>{{ form.prompt_version || "-" }}</dd></div>
                  <div><dt>Profile</dt><dd>{{ form.prompt_profile || "-" }}</dd></div>
                  <div><dt>Policy</dt><dd>{{ baselineLayer.policy_focus || "-" }}</dd></div>
                  <div><dt>Examples</dt><dd>{{ baselineLayer.examples?.length || 0 }}</dd></div>
                </dl>
                
                <details open class="ref-details">
                  <summary>Baseline Instructions</summary>
                  <pre class="baseline-pre">{{ baselineLayer.instructions }}</pre>
                </details>
                <details class="ref-details">
                  <summary>Baseline Policy Lines</summary>
                  <ol class="baseline-policy-list">
                    <li v-for="(line, index) in baselineLayer.policy_lines" :key="`baseline-policy-${index}`">{{ line }}</li>
                  </ol>
                </details>
              </div>
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
          <button type="button" :disabled="!canSave" @click="emit('save-draft')">
            {{ saving && form.status !== 'ready_for_eval' ? "保存中" : "保存草稿" }}
          </button>
          <button type="button" :disabled="!canSave" @click="emit('publish')" :title="publishTitle">
            {{ saving && form.status === 'ready_for_eval' ? "发布中" : "发布到验证入口" }}
          </button>
          <button
            v-if="selectedId && form.status === 'ready_for_eval'"
            type="button"
            :disabled="saving"
            @click="emit('unpublish')"
          >
            {{ saving ? "撤回中" : "撤回发布" }}
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
  gap: 12px;
  max-height: 420px;
  overflow: auto;
}
.draft-group {
  display: grid;
  gap: 8px;
}
.group-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
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
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 16px;
  padding: 14px 16px;
  background: var(--theme--background-subdued);
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
  margin-top: 14px;
}
.setup-strip label {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 0;
  flex: 1;
  min-width: 200px;
}
.setup-strip label span {
  font-weight: 700;
  font-size: 12px;
  color: var(--theme--foreground-subdued);
  white-space: nowrap;
}
.setup-strip input {
  flex: 1;
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  padding: 6px 12px;
  font-size: 13px;
  background: var(--theme--background);
  color: var(--theme--foreground);
}
.status-panel {
  display: flex;
  align-items: center;
  gap: 8px;
  background: transparent;
  border: none;
  padding: 0;
  min-height: auto;
}
.status-panel span {
  font-weight: 700;
  font-size: 12px;
  color: var(--theme--foreground-subdued);
}
.status-panel strong {
  font-size: 12px;
  background: color-mix(in srgb, var(--theme--primary) 8%, var(--theme--background));
  color: var(--theme--primary);
  padding: 3px 8px;
  border-radius: 4px;
  border: 1px solid color-mix(in srgb, var(--theme--primary) 20%, var(--theme--border-color));
  white-space: nowrap;
}
.setup-strip button {
  background: var(--theme--primary);
  color: var(--theme--primary-foreground, #fff);
  border: 1px solid var(--theme--primary);
  border-radius: 6px;
  padding: 6px 16px;
  font-size: 13px;
  font-weight: 700;
  transition: all 0.2s ease;
}
.setup-strip button:hover:not(:disabled) {
  opacity: 0.9;
}
.advanced-settings {
  border-top: 1px solid var(--theme--border-color);
  margin-top: 14px;
  padding-top: 12px;
}
.advanced-settings summary {
  cursor: pointer;
  color: var(--theme--foreground-subdued);
  font-size: 13px;
  font-weight: 700;
}
.advanced-settings-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  margin-top: 12px;
}
label {
  display: grid;
  gap: 6px;
}
textarea {
  width: 100%;
  box-sizing: border-box;
}
.empty-state {
  border: 1px dashed var(--theme--border-color);
  border-radius: 6px;
  color: var(--theme--foreground-subdued);
  margin-top: 14px;
  padding: 18px;
}
.pipeline-diff-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 1px 5px;
  border-radius: 999px;
  background: var(--theme--primary);
  color: var(--theme--primary-foreground, #fff);
  font-size: 9px;
  line-height: 1;
  font-weight: 700;
  margin-left: 2px;
}
.editor-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.15fr) minmax(320px, 0.85fr);
  gap: 16px;
  margin-top: 16px;
}
.agent-editor {
  display: grid;
  gap: 16px;
  padding: 16px;
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
  background: var(--theme--background);
}
.baseline-reference {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 0;
  border: none;
  background: transparent;
}
.header-actions,
.editor-actions div {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.stepper {
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
  background: var(--theme--background);
  padding: 12px 14px;
}

.stepper ol {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
}

.stepper li {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border: 1px dashed var(--theme--border-color-subdued, var(--theme--border-color));
  border-radius: 6px;
  background: var(--theme--background-subdued);
  color: var(--theme--foreground-subdued);
  font-size: 12px;
}

.stepper li.done {
  border-color: color-mix(in srgb, var(--theme--success) 45%, var(--theme--border-color));
  background: color-mix(in srgb, var(--theme--success) 6%, var(--theme--background));
  color: var(--theme--success);
}

.stepper li.active {
  border-style: solid;
  border-color: var(--theme--primary);
  background: color-mix(in srgb, var(--theme--primary) 6%, var(--theme--background));
  color: var(--theme--foreground);
  font-weight: 700;
}

/* 已发布完成态:done + active 同时存在,active 视觉优先,保留 done 勾 */
.stepper li.done.active {
  border-color: var(--theme--primary);
  background: color-mix(in srgb, var(--theme--primary) 6%, var(--theme--background));
  color: var(--theme--foreground);
}

.stepper li.done.active .step-index {
  border-color: var(--theme--primary);
  color: var(--theme--primary);
}

.step-index {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 20px;
  height: 20px;
  border-radius: 999px;
  border: 1px solid currentColor;
  font-size: 11px;
  font-weight: 700;
}

.step-check {
  margin-left: auto;
  font-size: 12px;
  font-weight: 700;
}

.pipeline {
  border: 1px solid var(--theme--border-color-subdued, var(--theme--border-color));
  border-radius: 8px;
  background: var(--theme--background-subdued);
  padding: 10px 12px;
  display: grid;
  gap: 8px;
}

.pipeline header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}

.pipeline header small {
  color: var(--theme--foreground-subdued);
  font-size: 11px;
  font-weight: 400;
}

.pipeline ol {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  font-size: 12px;
}

.pipeline-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.pipeline-step {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border: 1px solid var(--theme--border-color);
  border-radius: 999px;
  background: var(--theme--background);
  color: var(--theme--foreground-subdued);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}

.pipeline-step.changed {
  border-color: color-mix(in srgb, var(--theme--primary) 45%, var(--theme--border-color));
}

.pipeline-step.active {
  border-color: var(--theme--primary);
  background: color-mix(in srgb, var(--theme--primary) 8%, var(--theme--background));
  color: var(--theme--primary);
  font-weight: 700;
}

.pipeline-step:focus-visible {
  outline: 2px solid var(--theme--primary);
  outline-offset: 2px;
}

.pipeline-index {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 18px;
  border-radius: 999px;
  border: 1px solid currentColor;
  font-size: 10px;
  font-weight: 700;
}

.pipeline-arrow {
  margin-left: 2px;
  color: var(--theme--foreground-subdued);
}

.published-cta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  border: 1px solid color-mix(in srgb, var(--theme--success) 45%, var(--theme--border-color));
  border-radius: 8px;
  background: color-mix(in srgb, var(--theme--success) 6%, var(--theme--background));
  padding: 10px 14px;
  position: relative;
}
.published-cta::before {
  content: "";
  position: absolute;
  top: 50%;
  left: 12px;
  transform: translateY(-50%);
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--theme--success);
}
.published-cta {
  padding-left: 28px;
}

.published-cta strong {
  color: var(--theme--success);
}

.published-cta small {
  display: block;
  margin-top: 4px;
  color: var(--theme--foreground-subdued);
  font-size: 11px;
  font-weight: 400;
}

.published-cta-actions {
  display: flex;
  gap: 8px;
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

.agent-diff-tag {
  display: inline-flex;
  align-items: center;
  margin-left: 6px;
  padding: 0 6px;
  border: 1px solid color-mix(in srgb, var(--theme--primary) 45%, var(--theme--border-color));
  border-radius: 999px;
  background: var(--theme--primary);
  color: var(--theme--primary-foreground, #fff);
  font-size: 10px;
  font-weight: 700;
}

.agent-explainer {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  border: 1px solid var(--theme--border-color);
  border-left: 3px solid var(--agent-tone-color, var(--theme--primary));
  border-radius: 6px;
  background: var(--theme--background-subdued);
  padding: 12px 16px;
}

.agent-explainer p {
  margin: 6px 0 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--theme--foreground);
}

.explainer-label {
  display: inline-flex;
  align-items: center;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  color: var(--theme--foreground-subdued);
  padding: 2px 8px;
  border: 1px solid var(--theme--border-color);
  border-radius: 4px;
  background: var(--theme--background);
}

.diff-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 8px;
}

.diff-list li {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 12px;
}

.diff-agent {
  font-weight: 700;
  font-size: 13px;
  color: var(--theme--foreground);
}

.diff-dims {
  color: var(--theme--foreground-subdued);
  font-size: 12px;
}

.line-editor,
.example-editor {
  display: grid;
  gap: 12px;
  margin-top: 8px;
}

.line-row {
  position: relative;
  display: flex;
  align-items: center;
  gap: 8px;
}

.line-row input {
  flex: 1;
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  padding: 8px 12px;
  font-size: 13px;
  background: var(--theme--background);
  color: var(--theme--foreground);
  transition: all 0.2s ease;
}

.line-row input:focus {
  border-color: var(--theme--primary);
  outline: none;
}

.delete-line-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  background: var(--theme--background);
  color: var(--theme--foreground-subdued);
  cursor: pointer;
  font-size: 16px;
  font-weight: normal;
  padding: 0;
  transition: all 0.2s ease;
}

.delete-line-btn:hover {
  border-color: var(--theme--danger);
  color: var(--theme--danger);
  background: color-mix(in srgb, var(--theme--danger) 6%, var(--theme--background));
}

.example-row {
  display: flex;
  flex-direction: column;
  gap: 10px;
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
  padding: 16px;
  background: var(--theme--background-subdued);
}

.example-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.example-head input {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  background: var(--theme--background);
  border: 1px solid var(--theme--border-color);
  border-radius: 4px;
  padding: 4px 8px;
  color: var(--theme--foreground-subdued);
  max-width: 140px;
}

.example-row .sentence-input {
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  padding: 8px 12px;
  font-size: 13px;
  font-weight: 600;
  color: var(--theme--foreground);
  background: var(--theme--background);
}

.example-row .output-textarea {
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  padding: 10px 12px;
  font-size: 12px;
  line-height: 1.5;
  background: var(--theme--background);
  font-family: var(--theme--fonts--monospace--font-family, monospace);
  resize: vertical;
}

.raw-json {
  border-top: 1px solid var(--theme--border-color);
  padding-top: 12px;
  margin-top: 8px;
}

.inline-error {
  margin: 8px 0 0;
  color: var(--theme--danger);
  font-size: 12px;
  line-height: 1.5;
}

.ref-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.ref-title {
  margin: 0;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.ref-card {
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
  background: var(--theme--background);
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.no-diff-hint {
  font-size: 13px;
  color: var(--theme--foreground-subdued);
}

.ready-candidates-count {
  font-size: 11px;
  color: var(--theme--foreground-subdued);
}

.baseline-meta-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin: 0 0 12px;
}

.baseline-meta-grid div {
  padding: 6px 10px;
  background: var(--theme--background-subdued);
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
}

.baseline-meta-grid dt {
  font-size: 9px;
  font-weight: 700;
  text-transform: uppercase;
  color: var(--theme--foreground-subdued);
  letter-spacing: 0.05em;
}

.baseline-meta-grid dd {
  margin: 2px 0 0;
  font-size: 12px;
  font-weight: 600;
  color: var(--theme--foreground);
}

.ref-details {
  border-top: 1px solid var(--theme--border-color);
  padding-top: 10px;
  margin-top: 4px;
}

.ref-details summary {
  cursor: pointer;
  font-size: 12px;
  font-weight: 700;
  color: var(--theme--foreground-subdued);
  user-select: none;
}

.baseline-pre {
  max-height: 220px;
  overflow: auto;
  white-space: pre-wrap;
  font-family: var(--theme--fonts--monospace--font-family, monospace);
  font-size: 11px;
  line-height: 1.5;
  background: var(--theme--background-subdued);
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  padding: 10px;
  margin-top: 6px;
}

.baseline-policy-list {
  margin: 6px 0 0;
  padding-left: 16px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--theme--foreground);
}

.editor-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  border-top: 1px solid var(--theme--border-color);
  margin-top: 24px;
  padding-top: 20px;
}

.editor-actions label {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: 1;
  max-width: 50%;
  margin: 0;
}

.editor-actions label span {
  font-weight: 700;
  font-size: 13px;
  color: var(--theme--foreground-subdued);
  white-space: nowrap;
}

.editor-actions input {
  flex: 1;
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  padding: 8px 12px;
  font-size: 13px;
}

.editor-actions div {
  display: flex;
  align-items: center;
  gap: 8px;
}

.editor-actions button {
  padding: 8px 16px;
  font-size: 13px;
  border-radius: 6px;
  font-weight: 700;
  transition: all 0.2s ease;
}

.preview {
  border-top: 1px solid var(--theme--border-color);
  margin-top: 16px;
  padding-top: 16px;
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
  .setup-strip,
  .advanced-settings-grid {
    grid-template-columns: 1fr;
  }
}
</style>
