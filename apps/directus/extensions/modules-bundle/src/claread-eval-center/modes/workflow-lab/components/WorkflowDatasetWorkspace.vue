<script setup>
import { computed, ref, watch } from "vue";

const props = defineProps({
  datasets: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  creating: { type: Boolean, default: false },
  addingCase: { type: Boolean, default: false },
  singleRunRequest: { type: Object, default: null },
  singleRunResult: { type: Object, default: null },
});

const emit = defineEmits([
  "refresh",
  "create-dataset",
  "add-single-run-case",
  "go-to-single-run",
  "go-to-dataset-runs",
]);

const CREATE_STORAGE_KEY = "claread-eval-center:workflow-lab:dataset-create-form:v1";
const APPEND_STORAGE_KEY = "claread-eval-center:workflow-lab:dataset-append-form:v1";

const createForm = ref({
  dataset_id: "",
  description: "",
  tags_text: "prompt, learning-workflow",
  create_initial_case: true,
  case_id: "",
  case_tags_text: "",
  difficulty: "",
  target_phenomena_text: "",
  reference_notes: "",
});

const appendForm = ref({
  case_id: "",
  case_tags_text: "",
  difficulty: "",
  target_phenomena_text: "",
  reference_notes: "",
});

const selectedDatasetId = ref("");
const error = ref("");
const appendError = ref("");

const hasSingleRun = computed(() => Boolean(props.singleRunRequest?.text && props.singleRunResult));
const selectedDataset = computed(() => props.datasets.find((item) => item.id === selectedDatasetId.value) || null);
const totalCaseCount = computed(() => props.datasets.reduce((sum, item) => sum + Number(item.case_count || 0), 0));

function suggestionSuffix() {
  const now = new Date();
  const mm = String(now.getMonth() + 1).padStart(2, "0");
  const dd = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}${mm}${dd}`;
}

function currentSingleRunTags() {
  const readingGoal = props.singleRunRequest?.reading_goal || props.singleRunResult?.render_scene?.request?.reading_goal || "";
  const readingVariant = props.singleRunRequest?.reading_variant || props.singleRunResult?.render_scene?.request?.reading_variant || "";
  return [readingGoal, readingVariant].filter(Boolean).join(", ");
}

function loadStoredForms() {
  try {
    const rawCreate = window.sessionStorage.getItem(CREATE_STORAGE_KEY);
    if (rawCreate) {
      const saved = JSON.parse(rawCreate);
      if (saved && typeof saved === "object") {
        createForm.value = {
          ...createForm.value,
          ...saved,
        };
      }
    }
  } catch {
  }

  try {
    const rawAppend = window.sessionStorage.getItem(APPEND_STORAGE_KEY);
    if (rawAppend) {
      const saved = JSON.parse(rawAppend);
      if (saved && typeof saved === "object") {
        appendForm.value = {
          ...appendForm.value,
          ...saved,
        };
      }
    }
  } catch {
  }
}

loadStoredForms();

watch(
  createForm,
  (value) => {
    window.sessionStorage.setItem(CREATE_STORAGE_KEY, JSON.stringify(value));
  },
  { deep: true },
);

watch(
  appendForm,
  (value) => {
    window.sessionStorage.setItem(APPEND_STORAGE_KEY, JSON.stringify(value));
  },
  { deep: true },
);

watch(
  () => props.datasets,
  (datasets) => {
    if (!selectedDatasetId.value && datasets.length) {
      selectedDatasetId.value = datasets[0].id;
    }
  },
  { immediate: true },
);

watch(
  () => hasSingleRun.value,
  (value) => {
    if (!createForm.value.dataset_id) {
      createForm.value.dataset_id = `article-analysis-${suggestionSuffix()}`;
    }
    createForm.value.create_initial_case = value;
    if (!createForm.value.case_tags_text && value) {
      createForm.value.case_tags_text = currentSingleRunTags();
    }
    if (!appendForm.value.case_tags_text && value) {
      appendForm.value.case_tags_text = currentSingleRunTags();
    }
  },
  { immediate: true },
);

function splitTextList(raw) {
  return String(raw || "")
    .split(/[,，\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function createDataset() {
  error.value = "";
  if (!createForm.value.dataset_id.trim()) {
    error.value = "请先填写 dataset id。";
    return;
  }

  emit("create-dataset", {
    dataset_id: createForm.value.dataset_id.trim(),
    description: createForm.value.description.trim(),
    tags: splitTextList(createForm.value.tags_text),
    ...(hasSingleRun.value && createForm.value.create_initial_case ? {
      initial_case: {
        request: props.singleRunRequest,
        result: props.singleRunResult,
        case_id: createForm.value.case_id.trim() || null,
        tags: splitTextList(createForm.value.case_tags_text),
        difficulty: createForm.value.difficulty.trim() || null,
        target_phenomena: splitTextList(createForm.value.target_phenomena_text),
        reference_notes: createForm.value.reference_notes.trim() || null,
      },
    } : {}),
  });
}

function appendSingleRunCase() {
  appendError.value = "";
  if (!selectedDataset.value) {
    appendError.value = "请先选择一个 dataset。";
    return;
  }
  if (!hasSingleRun.value) {
    appendError.value = "请先完成一次 single run，再把当前文章写入 dataset。";
    return;
  }

  emit("add-single-run-case", {
    dataset_id: selectedDataset.value.id,
    request: props.singleRunRequest,
    result: props.singleRunResult,
    case_id: appendForm.value.case_id.trim() || null,
    tags: splitTextList(appendForm.value.case_tags_text),
    difficulty: appendForm.value.difficulty.trim() || null,
    target_phenomena: splitTextList(appendForm.value.target_phenomena_text),
    reference_notes: appendForm.value.reference_notes.trim() || null,
  });
}
</script>

<template>
  <section class="dataset-workspace">
    <header class="workspace-head">
      <div>
        <p>数据集工作区</p>
        <h2>把 Single Run 沉淀成可复跑的 dataset</h2>
      </div>
      <div class="head-actions">
        <button type="button" class="ghost" @click="emit('refresh')">刷新列表</button>
        <button type="button" class="ghost" :disabled="datasets.length === 0" @click="emit('go-to-dataset-runs')">去数据集验证</button>
      </div>
    </header>

    <p class="workspace-note">
      当前 dataset 仍然是文件制资产，创建后会写到 <code>evals/datasets/&lt;dataset_id&gt;/</code>。
      这里补的是可操作入口，不再要求你先离开页面去手写 <code>dataset.yaml</code>。
    </p>

    <div class="operation-grid">
      <article class="operation-card">
        <strong>创建 dataset</strong>
        <p>会新建 <code>evals/datasets/&lt;dataset_id&gt;/dataset.yaml</code>；如果勾选“创建时一起写入首个 case”，还会同时生成一个 <code>cases/&lt;case_id&gt;.json</code>。</p>
      </article>
      <article class="operation-card">
        <strong>把当前 Single Run 写入这个 dataset</strong>
        <p>不会新建 dataset，只会往当前选中的 dataset 追加一个新的 <code>cases/&lt;case_id&gt;.json</code>。</p>
      </article>
    </div>

    <dl class="facts-grid">
      <div>
        <dt>数据集数量</dt>
        <dd>{{ datasets.length }}</dd>
      </div>
      <div>
        <dt>Case 总数</dt>
        <dd>{{ totalCaseCount }}</dd>
      </div>
      <div>
        <dt>当前 Single Run</dt>
        <dd>{{ hasSingleRun ? "可写入 dataset" : "未准备" }}</dd>
      </div>
    </dl>

    <div class="workspace-layout">
      <aside class="dataset-list">
        <header>
          <div>
            <p>已有 datasets</p>
            <h3>选择一个 dataset</h3>
          </div>
        </header>

        <div v-if="loading" class="empty-state">正在读取 datasets...</div>
        <div v-else-if="!datasets.length" class="empty-state">
          <strong>还没有 dataset</strong>
          <p>先在右侧创建一个 dataset。若你已经完成 single run，建议顺手把这篇文章写成首个 case。</p>
        </div>
        <div v-else class="dataset-buttons">
          <button
            v-for="dataset in datasets"
            :key="dataset.id"
            type="button"
            class="dataset-button"
            :class="{ active: selectedDatasetId === dataset.id }"
            @click="selectedDatasetId = dataset.id"
          >
            <div class="dataset-row">
              <strong>{{ dataset.id }}</strong>
              <span>{{ dataset.case_count || 0 }} cases</span>
            </div>
            <p>{{ dataset.description || "未填写描述。" }}</p>
            <small>{{ dataset.tags?.length ? dataset.tags.join(" / ") : "无 tags" }}</small>
          </button>
        </div>
      </aside>

      <div class="main-stack">
        <section class="dataset-panel">
          <header class="panel-head">
            <div>
              <p>创建 dataset</p>
              <h3>新建一个可批量跑的文章集</h3>
            </div>
            <span class="panel-tag">workspace</span>
          </header>

          <p v-if="error" class="panel-error">{{ error }}</p>

          <div class="form-grid">
            <label>
              <span>dataset id</span>
              <input v-model="createForm.dataset_id" type="text" placeholder="article-analysis-20260604" />
            </label>
            <label>
              <span>tags</span>
              <input v-model="createForm.tags_text" type="text" placeholder="prompt, learning-workflow" />
            </label>
            <label class="span-2">
              <span>描述</span>
              <textarea v-model="createForm.description" rows="3" placeholder="这组 case 用来验证哪类文章、哪种 prompt 迭代。" />
            </label>
          </div>

          <section class="seed-panel" :class="{ disabled: !hasSingleRun }">
            <div class="seed-head">
              <div>
                <strong>首个 case</strong>
                <small>推荐直接使用当前 single run 的文章作为首个 case</small>
              </div>
              <label class="seed-toggle">
                <input v-model="createForm.create_initial_case" type="checkbox" :disabled="!hasSingleRun" />
                <span>创建时一起写入首个 case.json</span>
              </label>
            </div>

            <p v-if="!hasSingleRun" class="seed-empty">
              还没有可用的 single run。先去「单篇验证」跑一篇文章，再回来把它沉淀为 dataset case。
            </p>

            <template v-else-if="createForm.create_initial_case">
              <div class="single-run-facts">
                <div><dt>阅读目标</dt><dd>{{ singleRunRequest?.reading_goal || "—" }}</dd></div>
                <div><dt>阅读场景</dt><dd>{{ singleRunRequest?.reading_variant || "—" }}</dd></div>
                <div><dt>候选版本</dt><dd>{{ singleRunResult?.prompt_identity?.prompt_variant_id || "baseline" }}</dd></div>
              </div>

              <div class="form-grid compact-grid">
                <label>
                  <span>case id</span>
                  <input v-model="createForm.case_id" type="text" placeholder="留空则自动生成" />
                </label>
                <label>
                  <span>难度</span>
                  <input v-model="createForm.difficulty" type="text" placeholder="如 kaoyan / intensive" />
                </label>
                <label>
                  <span>case tags</span>
                  <input v-model="createForm.case_tags_text" type="text" placeholder="exam, kaoyan" />
                </label>
                <label>
                  <span>target phenomena</span>
                  <input v-model="createForm.target_phenomena_text" type="text" placeholder="long_sentence, phrase_gloss" />
                </label>
                <label class="span-2">
                  <span>参考备注</span>
                  <textarea v-model="createForm.reference_notes" rows="3" placeholder="记录这篇文章为何进入 dataset，后续重点观察哪些现象。" />
                </label>
              </div>

              <p class="metadata-note">
                当前 <code>difficulty</code>、<code>case tags</code>、<code>target phenomena</code> 主要是 case metadata；
                真正会影响 grader 阈值的是写进 case.json 里的 <code>expected</code>。
              </p>
            </template>
          </section>

          <footer class="panel-actions">
            <button type="button" :disabled="creating" @click="createDataset">
              {{ creating ? "创建中..." : "创建 dataset 目录 / dataset.yaml" }}
            </button>
          </footer>
        </section>

        <section class="dataset-panel">
          <header class="panel-head">
            <div>
              <p>当前选择</p>
              <h3>{{ selectedDataset?.id || "先选择左侧 dataset" }}</h3>
            </div>
            <span class="panel-tag">append</span>
          </header>

          <template v-if="selectedDataset">
            <dl class="selected-facts">
              <div>
                <dt>Case 数</dt>
                <dd>{{ selectedDataset.case_count || 0 }}</dd>
              </div>
              <div>
                <dt>Target</dt>
                <dd>{{ selectedDataset.target || "article_analysis" }}</dd>
              </div>
              <div>
                <dt>Tags</dt>
                <dd>{{ selectedDataset.tags?.length ? selectedDataset.tags.join(", ") : "—" }}</dd>
              </div>
            </dl>

            <p class="selected-description">{{ selectedDataset.description || "当前 dataset 没有描述。" }}</p>

            <div v-if="!hasSingleRun" class="empty-state compact">
              <strong>当前没有 single run</strong>
              <p>这意味着你还不能往 dataset 里追加 case。先回「单篇验证」跑通一篇文章。</p>
              <button type="button" class="ghost" @click="emit('go-to-single-run')">去单篇验证</button>
            </div>

            <template v-else>
              <p v-if="appendError" class="panel-error">{{ appendError }}</p>

              <div class="form-grid compact-grid">
                <label>
                  <span>case id</span>
                  <input v-model="appendForm.case_id" type="text" placeholder="留空则自动生成" />
                </label>
                <label>
                  <span>难度</span>
                  <input v-model="appendForm.difficulty" type="text" placeholder="如 kaoyan / intensive" />
                </label>
                <label>
                  <span>case tags</span>
                  <input v-model="appendForm.case_tags_text" type="text" placeholder="exam, grammar" />
                </label>
                <label>
                  <span>target phenomena</span>
                  <input v-model="appendForm.target_phenomena_text" type="text" placeholder="context_gloss, sentence_analysis" />
                </label>
                <label class="span-2">
                  <span>参考备注</span>
                  <textarea v-model="appendForm.reference_notes" rows="3" placeholder="补充这篇 case 的目标现象、原因或后续对比重点。" />
                </label>
              </div>

              <p class="metadata-note">
                这里追加的是一个新的 <code>case.json</code>。这几个字段当前主要用于整理和回看；
                如果要真正改变通过/失败阈值，需要改这个 case 的 <code>expected</code>。
              </p>

              <footer class="panel-actions">
                <button type="button" :disabled="addingCase" @click="appendSingleRunCase">
                  {{ addingCase ? "写入中..." : "追加 1 个 case.json 到当前 dataset" }}
                </button>
              </footer>
            </template>
          </template>

          <div v-else class="empty-state compact">左侧选中一个 dataset 后，这里会显示详情和“追加当前 single run 为新 case”的入口。</div>
        </section>
      </div>
    </div>
  </section>
</template>

<style scoped>
.dataset-workspace {
  display: grid;
  gap: 16px;
}

.workspace-head,
.head-actions,
.panel-head,
.panel-actions {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.workspace-head p,
.workspace-note,
.facts-grid dt,
.dataset-button p,
.dataset-button small,
.panel-head p,
label span,
.seed-head small,
.seed-empty,
.selected-description,
.empty-state p {
  margin: 0;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
}

.workspace-head h2,
.panel-head h3 {
  margin: 2px 0 0;
  font-size: 18px;
  line-height: 1.45;
}

.workspace-note {
  border: 1px solid var(--theme--border-color);
  border-radius: 10px;
  background: var(--theme--background-subdued);
  line-height: 1.7;
  padding: 12px 14px;
}

.operation-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.operation-card {
  border: 1px solid var(--theme--border-color);
  border-radius: 10px;
  background: var(--theme--background);
  padding: 12px 14px;
  display: grid;
  gap: 6px;
}

.operation-card strong,
.metadata-note code,
.operation-card code {
  color: var(--theme--foreground);
}

.operation-card p,
.metadata-note {
  margin: 0;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  line-height: 1.65;
}

.workspace-note code {
  font-family: var(--theme--fonts--monospace--font-family, monospace);
  font-size: 11px;
}

.facts-grid,
.single-run-facts,
.selected-facts {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 1px;
  border: 1px solid var(--theme--border-color);
  border-radius: 10px;
  overflow: hidden;
}

.facts-grid div,
.single-run-facts div,
.selected-facts div {
  background: var(--theme--background-subdued);
  padding: 10px 12px;
}

.facts-grid dd,
.single-run-facts dd,
.selected-facts dd {
  margin: 4px 0 0;
  font-size: 14px;
  font-weight: 700;
  overflow-wrap: anywhere;
}

.workspace-layout {
  display: grid;
  grid-template-columns: minmax(320px, 0.38fr) minmax(0, 1fr);
  gap: 16px;
  align-items: start;
}

.dataset-list,
.dataset-panel {
  border: 1px solid var(--theme--border-color);
  border-radius: 12px;
  background: var(--theme--background);
  padding: 16px;
  display: grid;
  gap: 14px;
}

.dataset-buttons {
  display: grid;
  gap: 10px;
}

.dataset-button {
  width: 100%;
  border: 1px solid var(--theme--border-color);
  border-radius: 10px;
  background: var(--theme--background);
  color: var(--theme--foreground);
  cursor: pointer;
  padding: 12px 14px;
  text-align: left;
}

.dataset-button.active {
  border-color: var(--theme--primary);
  background: color-mix(in srgb, var(--theme--primary) 6%, var(--theme--background));
}

.dataset-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
}

.dataset-row span {
  color: var(--theme--foreground-subdued);
  font-size: 11px;
  white-space: nowrap;
}

.panel-tag {
  display: inline-flex;
  align-items: center;
  min-height: 24px;
  border: 1px solid var(--theme--border-color);
  border-radius: 999px;
  padding: 0 10px;
  color: var(--theme--foreground-subdued);
  font-size: 11px;
  font-weight: 700;
}

.main-stack {
  display: grid;
  gap: 16px;
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.compact-grid {
  margin-top: 12px;
}

.span-2 {
  grid-column: 1 / -1;
}

label {
  display: grid;
  gap: 6px;
  min-width: 0;
}

input,
textarea,
button {
  min-height: 38px;
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
  background: var(--theme--background);
  color: var(--theme--foreground);
  font: inherit;
  padding: 8px 10px;
}

textarea {
  resize: vertical;
  line-height: 1.55;
}

button {
  cursor: pointer;
  font-weight: 700;
}

button:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.ghost {
  background: transparent;
}

.seed-panel {
  border: 1px solid var(--theme--border-color);
  border-radius: 10px;
  background: var(--theme--background-subdued);
  padding: 14px;
  display: grid;
  gap: 12px;
}

.seed-panel.disabled {
  opacity: 0.8;
}

.seed-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.seed-head strong,
.empty-state strong {
  font-size: 14px;
}

.seed-toggle {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.seed-toggle input {
  min-height: auto;
}

.seed-empty,
.selected-description {
  line-height: 1.65;
}

.metadata-note {
  border: 1px dashed var(--theme--border-color);
  border-radius: 10px;
  background: var(--theme--background);
  padding: 10px 12px;
}

.panel-error {
  margin: 0;
  border: 1px solid var(--theme--danger);
  border-radius: 10px;
  background: var(--theme--danger-background);
  color: var(--theme--foreground);
  padding: 10px 12px;
  font-size: 12px;
}

.empty-state {
  border: 1px dashed var(--theme--border-color);
  border-radius: 10px;
  background: var(--theme--background-subdued);
  padding: 16px;
  display: grid;
  gap: 8px;
  line-height: 1.65;
}

.empty-state.compact {
  padding: 14px;
}

@media (max-width: 1180px) {
  .workspace-layout {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 820px) {
  .operation-grid,
  .facts-grid,
  .single-run-facts,
  .selected-facts,
  .form-grid {
    grid-template-columns: 1fr;
  }

  .workspace-head,
  .head-actions,
  .panel-head,
  .panel-actions,
  .seed-head {
    display: grid;
  }
}
</style>
