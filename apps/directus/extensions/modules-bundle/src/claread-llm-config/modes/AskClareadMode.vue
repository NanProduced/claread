<script setup>
import { ref, computed, onMounted } from "vue";
import { useRouter } from "vue-router";
import { useApi } from "@directus/extensions-sdk";

const router = useRouter();
const api = useApi();

const askConfig = ref(null);
const askOptions = ref([]);
const loading = ref(true);

// --- Data loading ---

async function loadData() {
  try {
    const [configResp, optionsResp] = await Promise.all([
      api.get("/items/llm_ask_config", { params: { limit: 1 } }).catch(() => ({ data: { data: [] } })),
      api.get("/items/llm_ask_options", { params: { limit: -1, sort: "sort" } }).catch(() => ({ data: { data: [] } })),
    ]);
    askConfig.value = configResp.data.data?.[0] ?? null;
    askOptions.value = optionsResp.data.data ?? [];
  } catch {
    // ignore
  } finally {
    loading.value = false;
  }
}

onMounted(loadData);

// --- Default option logic ---

const defaultOptionState = computed(() => {
  const slug = askConfig.value?.default_option;
  if (!slug) return "unset";
  const opt = askOptions.value.find((o) => o.slug === slug);
  if (!opt) return "missing";
  if (!opt.enabled) return "disabled";
  return "active";
});

const effectiveOption = computed(() => {
  const state = defaultOptionState.value;
  if (state === "active") return askConfig.value.default_option;
  const first = askOptions.value.find((o) => o.enabled);
  return first?.slug ?? null;
});

const enabledCount = computed(() => askOptions.value.filter((o) => o.enabled).length);

// --- Selection summary helpers ---

function summarizeSelection(selection) {
  if (!selection) return null;
  const result = { preset: null, routeCount: 0, routeKeys: [] };
  if (selection.preset) {
    result.preset = selection.preset;
  }
  const routes = selection.routes;
  if (routes && typeof routes === "object") {
    const keys = Object.keys(routes);
    result.routeCount = keys.length;
    result.routeKeys = keys.slice(0, 5);
  }
  return result.routeCount > 0 || result.preset ? result : null;
}

function summarizeRuntimeBudget(budget) {
  if (!budget) return null;
  const parts = [];
  if (budget.max_input_tokens) parts.push(`in ${budget.max_input_tokens}`);
  if (budget.max_output_tokens) parts.push(`out ${budget.max_output_tokens}`);
  return parts.length > 0 ? parts.join(" / ") : null;
}

function isKeyRoute(k) {
  return k === "reader_ask" || k === "reader_ask_planner" || k === "reader_ask_replan";
}

// --- Navigation ---

function openCollection(id) {
  router.push(`/content/${id}`);
}

function openItem(collection, id) {
  router.push(`/content/${collection}/${id}`);
}

// ==========================================================================
// Ask Config Editor
// ==========================================================================

const configDrawerOpen = ref(false);
const configSaving = ref(false);
const configError = ref(null);
const configSuccess = ref(false);

const configForm = ref({
  default_option: "",
  reserved_points: 10,
  tokens_per_point: 1000,
  billing_policy_version: "",
  max_input_tokens: 24000,
  max_output_tokens: 3200,
  prompt_buffer_tokens: 800,
});

const configFormErrors = ref({});

function openConfigEditor() {
  const c = askConfig.value;
  configForm.value = {
    default_option: c?.default_option ?? "",
    reserved_points: c?.billing_defaults?.reserved_points ?? 10,
    tokens_per_point: c?.billing_defaults?.tokens_per_point ?? 1000,
    billing_policy_version: c?.billing_defaults?.billing_policy_version ?? "",
    max_input_tokens: c?.runtime_defaults?.max_input_tokens ?? 24000,
    max_output_tokens: c?.runtime_defaults?.max_output_tokens ?? 3200,
    prompt_buffer_tokens: c?.runtime_defaults?.prompt_buffer_tokens ?? 800,
  };
  configFormErrors.value = {};
  configError.value = null;
  configSuccess.value = false;
  configDrawerOpen.value = true;
}

function validateConfigForm() {
  const errors = {};
  const f = configForm.value;
  if (f.default_option && !askOptions.value.some((o) => o.slug === f.default_option)) {
    errors.default_option = "该 slug 不在 Ask Options 列表中";
  }
  if (!f.tokens_per_point && f.tokens_per_point !== 0) {
    errors.tokens_per_point = "必填";
  } else if (Number(f.tokens_per_point) <= 0) {
    errors.tokens_per_point = "必须为正数";
  }
  if (!f.billing_policy_version?.trim()) {
    errors.billing_policy_version = "必填";
  }
  if (!f.max_input_tokens && f.max_input_tokens !== 0) {
    errors.max_input_tokens = "必填";
  } else if (Number(f.max_input_tokens) <= 0) {
    errors.max_input_tokens = "必须为正数";
  }
  if (!f.max_output_tokens && f.max_output_tokens !== 0) {
    errors.max_output_tokens = "必填";
  } else if (Number(f.max_output_tokens) <= 0) {
    errors.max_output_tokens = "必须为正数";
  }
  if (f.reserved_points === "" || f.reserved_points === null || f.reserved_points === undefined) {
    errors.reserved_points = "必填";
  } else if (Number(f.reserved_points) < 0) {
    errors.reserved_points = "不能为负数";
  }
  if (f.prompt_buffer_tokens === "" || f.prompt_buffer_tokens === null || f.prompt_buffer_tokens === undefined) {
    errors.prompt_buffer_tokens = "必填";
  } else if (Number(f.prompt_buffer_tokens) < 0) {
    errors.prompt_buffer_tokens = "不能为负数";
  }
  configFormErrors.value = errors;
  return Object.keys(errors).length === 0;
}

function numOrNull(v) {
  if (v === "" || v === null || v === undefined) return null;
  const n = Number(v);
  return Number.isNaN(n) ? null : n;
}

function strOrNull(v) {
  return v?.trim() || null;
}

async function saveConfig() {
  if (!validateConfigForm()) return;
  configSaving.value = true;
  configError.value = null;
  configSuccess.value = false;
  const f = configForm.value;
  const existingBilling = askConfig.value?.billing_defaults ?? {};
  const existingRuntime = askConfig.value?.runtime_defaults ?? {};
  const data = {
    default_option: strOrNull(f.default_option),
    billing_defaults: {
      ...existingBilling,
      reserved_points: numOrNull(f.reserved_points),
      tokens_per_point: numOrNull(f.tokens_per_point),
      billing_policy_version: strOrNull(f.billing_policy_version),
    },
    runtime_defaults: {
      ...existingRuntime,
      max_input_tokens: numOrNull(f.max_input_tokens),
      max_output_tokens: numOrNull(f.max_output_tokens),
      prompt_buffer_tokens: numOrNull(f.prompt_buffer_tokens),
    },
  };
  try {
    if (askConfig.value?.id) {
      await api.patch(`/items/llm_ask_config/${askConfig.value.id}`, data);
    } else {
      await api.post("/items/llm_ask_config", data);
    }
    configSuccess.value = true;
    await loadData();
    setTimeout(() => {
      configDrawerOpen.value = false;
      configSuccess.value = false;
    }, 800);
  } catch (e) {
    configError.value = e?.response?.data?.errors?.[0]?.message || e?.message || "保存失败";
  } finally {
    configSaving.value = false;
  }
}

// ==========================================================================
// Ask Option Editor
// ==========================================================================

const optionDrawerOpen = ref(false);
const optionSaving = ref(false);
const optionError = ref(null);
const optionSuccess = ref(false);
const optionEditingId = ref(null);

const optionForm = ref({
  label: "",
  slug: "",
  enabled: true,
  description: "",
  price_multiplier: 1,
  runtime_budget_max_input: "",
  runtime_budget_max_output: "",
  runtime_budget_prompt_buffer: "",
  selection_json: "",
});

const optionFormErrors = ref({});
const selectionJsonExpanded = ref(false);

const selectionRouteCount = computed(() => {
  try {
    const parsed = JSON.parse(optionForm.value.selection_json);
    return Object.keys(parsed?.routes || {}).length;
  } catch {
    return null;
  }
});

function openOptionEditor(opt) {
  optionEditingId.value = opt?.id ?? null;
  optionForm.value = {
    label: opt?.label ?? "",
    slug: opt?.slug ?? "",
    enabled: opt?.enabled ?? true,
    description: opt?.description ?? "",
    price_multiplier: opt?.price_multiplier ?? 1,
    runtime_budget_max_input: opt?.runtime_budget?.max_input_tokens ?? "",
    runtime_budget_max_output: opt?.runtime_budget?.max_output_tokens ?? "",
    runtime_budget_prompt_buffer: opt?.runtime_budget?.prompt_buffer_tokens ?? "",
    selection_json: opt?.selection ? JSON.stringify(opt.selection, null, 2) : "",
  };
  optionFormErrors.value = {};
  optionError.value = null;
  optionSuccess.value = false;
  selectionJsonExpanded.value = false;
  optionDrawerOpen.value = true;
}

function validateOptionForm() {
  const errors = {};
  const f = optionForm.value;
  if (!f.slug.trim()) errors.slug = "slug 不能为空";
  if (!f.label.trim()) errors.label = "label 不能为空";
  if (Number(f.price_multiplier) <= 0) errors.price_multiplier = "必须大于 0";
  if (f.runtime_budget_max_input !== "" && f.runtime_budget_max_input !== null && Number(f.runtime_budget_max_input) < 1) {
    errors.runtime_budget_max_input = "必须为正数";
  }
  if (f.runtime_budget_max_output !== "" && f.runtime_budget_max_output !== null && Number(f.runtime_budget_max_output) < 1) {
    errors.runtime_budget_max_output = "必须为正数";
  }
  if (f.runtime_budget_prompt_buffer !== "" && f.runtime_budget_prompt_buffer !== null && Number(f.runtime_budget_prompt_buffer) < 0) {
    errors.runtime_budget_prompt_buffer = "不能为负数";
  }
  if (f.selection_json.trim()) {
    try {
      JSON.parse(f.selection_json);
    } catch {
      errors.selection_json = "JSON 格式无效";
    }
  }
  optionFormErrors.value = errors;
  return Object.keys(errors).length === 0;
}

async function saveOption() {
  if (!validateOptionForm()) return;
  optionSaving.value = true;
  optionError.value = null;
  optionSuccess.value = false;
  const f = optionForm.value;
  const runtime_budget = {};
  const mi = numOrNull(f.runtime_budget_max_input);
  const mo = numOrNull(f.runtime_budget_max_output);
  const pb = numOrNull(f.runtime_budget_prompt_buffer);
  if (mi !== null) runtime_budget.max_input_tokens = mi;
  if (mo !== null) runtime_budget.max_output_tokens = mo;
  if (pb !== null) runtime_budget.prompt_buffer_tokens = pb;
  let selection = null;
  if (f.selection_json.trim()) {
    selection = JSON.parse(f.selection_json);
  }
  const data = {
    slug: f.slug,
    label: f.label,
    enabled: f.enabled,
    description: f.description || "",
    price_multiplier: Number(f.price_multiplier),
    runtime_budget: Object.keys(runtime_budget).length > 0 ? runtime_budget : null,
    selection,
  };
  try {
    if (optionEditingId.value) {
      await api.patch(`/items/llm_ask_options/${optionEditingId.value}`, data);
    } else {
      await api.post("/items/llm_ask_options", data);
    }
    optionSuccess.value = true;
    await loadData();
    setTimeout(() => {
      optionDrawerOpen.value = false;
      optionSuccess.value = false;
    }, 800);
  } catch (e) {
    optionError.value = e?.response?.data?.errors?.[0]?.message || e?.message || "保存失败";
  } finally {
    optionSaving.value = false;
  }
}
</script>

<template>
  <div class="ask-mode">
    <!-- Ask Config: current effective config -->
    <section>
      <div class="section-head">
        <h3>当前生效配置</h3>
        <div class="section-head-actions">
          <button class="text-btn" type="button" @click="openConfigEditor">编辑</button>
          <button class="text-btn muted" type="button" @click="openCollection('llm_ask_config')">原始 collection</button>
        </div>
      </div>
      <div class="section-bar"></div>

      <div v-if="loading" class="hint">加载中…</div>
      <div v-else-if="!askConfig" class="callout callout-warning">
        <p><strong>Ask Config 单例尚未创建</strong></p>
        <p>Ask Config 存储 Ask Claread 的顶层默认配置：default_option、billing_defaults、runtime_defaults。通常由 import 脚本自动创建，也可手动添加。</p>
        <button class="text-btn" type="button" @click="openConfigEditor">创建 Ask Config</button>
      </div>
      <template v-else>
        <!-- Default option warnings -->
        <div v-if="defaultOptionState === 'missing'" class="callout callout-danger">
          默认 option "<strong>{{ askConfig.default_option }}</strong>" 不在当前 Ask Options 列表中，请检查是否已被删除或 slug 不匹配。
        </div>
        <div v-if="defaultOptionState === 'disabled'" class="callout callout-warning">
          默认 option "<strong>{{ askConfig.default_option }}</strong>" 已被禁用，后端会回退到第一个 enabled option。实际生效：<strong>{{ effectiveOption || '无' }}</strong>
        </div>
        <div v-if="defaultOptionState === 'unset' && effectiveOption" class="callout callout-info">
          未设置 default_option，后端回退到第一个 enabled option。实际生效：<strong>{{ effectiveOption }}</strong>
        </div>

        <div class="config-groups">
          <div class="config-group">
            <div class="config-group-label">BILLING</div>
            <dl class="config-list">
              <div class="config-row" :class="{ 'config-row-active': defaultOptionState === 'active', 'config-row-warn': defaultOptionState !== 'active' }">
                <dt>Default Option</dt>
                <dd>
                  {{ askConfig.default_option || "未设置" }}
                  <span v-if="defaultOptionState !== 'active' && effectiveOption" class="effective-hint">实际生效：{{ effectiveOption }}</span>
                </dd>
              </div>
              <div class="config-row">
                <dt>Billing Policy</dt>
                <dd>{{ askConfig.billing_defaults?.billing_policy_version || "—" }}</dd>
              </div>
              <div class="config-row">
                <dt>Reserved Points</dt>
                <dd>{{ askConfig.billing_defaults?.reserved_points ?? "—" }}</dd>
              </div>
              <div class="config-row">
                <dt>Tokens / Point</dt>
                <dd>{{ askConfig.billing_defaults?.tokens_per_point ?? "—" }}</dd>
              </div>
            </dl>
          </div>
          <div class="config-group">
            <div class="config-group-label">RUNTIME</div>
            <dl class="config-list">
              <div class="config-row">
                <dt>Max Input Tokens</dt>
                <dd>{{ askConfig.runtime_defaults?.max_input_tokens ?? "—" }}</dd>
              </div>
              <div class="config-row">
                <dt>Max Output Tokens</dt>
                <dd>{{ askConfig.runtime_defaults?.max_output_tokens ?? "—" }}</dd>
              </div>
              <div class="config-row">
                <dt>Prompt Buffer Tokens</dt>
                <dd>{{ askConfig.runtime_defaults?.prompt_buffer_tokens ?? "—" }}</dd>
              </div>
            </dl>
          </div>
        </div>
      </template>
    </section>

    <!-- Ask Options list -->
    <section>
      <div class="section-head">
        <h3>Ask Options <span class="count-label">{{ enabledCount }} enabled / {{ askOptions.length }} total</span></h3>
        <button class="text-btn muted" type="button" @click="openCollection('llm_ask_options')">管理全部</button>
      </div>
      <div class="section-bar"></div>

      <div v-if="loading" class="hint">加载中…</div>
      <div v-else-if="askOptions.length === 0" class="hint">
        暂无 Ask Option。请先运行 import 或在
        <button class="text-btn" type="button" @click="openCollection('llm_ask_options')">Ask Options</button>
        中创建。
      </div>
      <div v-else class="option-list">
        <div
          v-for="opt in askOptions"
          :key="opt.id"
          class="option-row"
          :class="{ 'is-effective': effectiveOption === opt.slug, 'is-disabled': !opt.enabled }"
          tabindex="0"
          @keydown.enter="openOptionEditor(opt)"
        >
          <div class="option-main">
            <div class="option-identity">
              <strong class="option-label">{{ opt.label }}</strong>
              <code class="option-slug">{{ opt.slug }}</code>
              <span v-if="effectiveOption === opt.slug" class="badge badge-primary">DEFAULT</span>
              <span v-if="!opt.enabled" class="badge badge-muted">DISABLED</span>
              <span v-if="opt.price_multiplier !== undefined && opt.price_multiplier !== 1" class="badge badge-plain">{{ Number(opt.price_multiplier) }}x</span>
            </div>
            <div class="option-meta">
              <span v-if="summarizeSelection(opt.selection)" class="meta-item">
                <span class="meta-key">路由</span>
                <template v-if="summarizeSelection(opt.selection).preset">
                  Preset: <strong>{{ summarizeSelection(opt.selection).preset }}</strong> ·
                </template>
                {{ summarizeSelection(opt.selection).routeCount }} route{{ summarizeSelection(opt.selection).routeCount !== 1 ? 's' : '' }}
                <template v-if="summarizeSelection(opt.selection).routeKeys.length">
                  (<span v-for="(k, i) in summarizeSelection(opt.selection).routeKeys" :key="k">
                    <span :class="{ 'key-route': isKeyRoute(k) }">{{ k }}</span><span v-if="i < summarizeSelection(opt.selection).routeKeys.length - 1">, </span>
                  </span>
                  <span v-if="summarizeSelection(opt.selection).routeCount > 5">…</span>)
                </template>
              </span>
              <span v-if="summarizeRuntimeBudget(opt.runtime_budget)" class="meta-item">
                <span class="meta-key">Budget</span>
                {{ summarizeRuntimeBudget(opt.runtime_budget) }}
              </span>
              <span v-if="opt.description" class="meta-item meta-desc">{{ opt.description }}</span>
            </div>
          </div>
          <div class="option-actions">
            <button class="text-btn" type="button" @click="openOptionEditor(opt)">编辑</button>
            <button class="text-btn muted" type="button" @click="openItem('llm_ask_options', opt.id)">原始</button>
          </div>
        </div>
      </div>
    </section>

    <!-- =================================================================== -->
    <!-- Ask Config Editor Drawer -->
    <!-- =================================================================== -->
    <teleport to="#main-content">
      <Transition name="drawer-slide">
        <div v-if="configDrawerOpen" class="drawer-overlay" @click.self="configDrawerOpen = false">
          <div class="drawer">
            <div class="drawer-head">
              <h2>编辑 Ask Config</h2>
              <button class="icon-btn" type="button" @click="configDrawerOpen = false">
                <v-icon name="close" />
              </button>
            </div>

            <div class="drawer-body">
              <div v-if="configError" class="callout callout-danger">{{ configError }}</div>
              <div v-if="configSuccess" class="callout callout-success">保存成功</div>

              <div class="field">
                <label class="field-label">Default Option</label>
                <select v-model="configForm.default_option" class="field-input">
                  <option value="">（未设置）</option>
                  <option v-for="opt in askOptions" :key="opt.slug" :value="opt.slug">
                    {{ opt.label }} ({{ opt.slug }}){{ opt.enabled ? '' : ' — DISABLED' }}
                  </option>
                </select>
                <span v-if="configFormErrors.default_option" class="field-error">{{ configFormErrors.default_option }}</span>
              </div>

              <h4 class="field-group-title">Billing Defaults</h4>

              <div class="field">
                <label class="field-label">Billing Policy Version</label>
                <input v-model="configForm.billing_policy_version" type="text" class="field-input" />
                <span v-if="configFormErrors.billing_policy_version" class="field-error">{{ configFormErrors.billing_policy_version }}</span>
              </div>

              <div class="field-row">
                <div class="field">
                  <label class="field-label">Reserved Points</label>
                  <input v-model.number="configForm.reserved_points" type="number" min="0" class="field-input" />
                  <span v-if="configFormErrors.reserved_points" class="field-error">{{ configFormErrors.reserved_points }}</span>
                </div>
                <div class="field">
                  <label class="field-label">Tokens / Point</label>
                  <input v-model.number="configForm.tokens_per_point" type="number" min="1" class="field-input" />
                  <span v-if="configFormErrors.tokens_per_point" class="field-error">{{ configFormErrors.tokens_per_point }}</span>
                </div>
              </div>

              <h4 class="field-group-title">Runtime Defaults</h4>

              <div class="field-row">
                <div class="field">
                  <label class="field-label">Max Input Tokens</label>
                  <input v-model.number="configForm.max_input_tokens" type="number" min="1" class="field-input" />
                  <span v-if="configFormErrors.max_input_tokens" class="field-error">{{ configFormErrors.max_input_tokens }}</span>
                </div>
                <div class="field">
                  <label class="field-label">Max Output Tokens</label>
                  <input v-model.number="configForm.max_output_tokens" type="number" min="1" class="field-input" />
                  <span v-if="configFormErrors.max_output_tokens" class="field-error">{{ configFormErrors.max_output_tokens }}</span>
                </div>
              </div>

              <div class="field">
                <label class="field-label">Prompt Buffer Tokens</label>
                <input v-model.number="configForm.prompt_buffer_tokens" type="number" min="0" class="field-input" />
                <span v-if="configFormErrors.prompt_buffer_tokens" class="field-error">{{ configFormErrors.prompt_buffer_tokens }}</span>
              </div>
            </div>

            <div class="drawer-foot">
              <button class="btn btn-ghost" type="button" @click="configDrawerOpen = false" :disabled="configSaving">取消</button>
              <button class="btn btn-primary" type="button" @click="saveConfig" :disabled="configSaving">
                {{ configSaving ? '保存中…' : '保存' }}
              </button>
            </div>
          </div>
        </div>
      </Transition>
    </teleport>

    <!-- =================================================================== -->
    <!-- Ask Option Editor Drawer -->
    <!-- =================================================================== -->
    <teleport to="#main-content">
      <Transition name="drawer-slide">
        <div v-if="optionDrawerOpen" class="drawer-overlay" @click.self="optionDrawerOpen = false">
          <div class="drawer">
            <div class="drawer-head">
              <h2>{{ optionEditingId ? '编辑 Ask Option' : '新建 Ask Option' }}</h2>
              <button class="icon-btn" type="button" @click="optionDrawerOpen = false">
                <v-icon name="close" />
              </button>
            </div>

            <div class="drawer-body">
              <div v-if="optionError" class="callout callout-danger">{{ optionError }}</div>
              <div v-if="optionSuccess" class="callout callout-success">保存成功</div>

              <div class="field-row">
                <div class="field">
                  <label class="field-label">Label <span class="req">*</span></label>
                  <input v-model="optionForm.label" type="text" class="field-input" />
                  <span v-if="optionFormErrors.label" class="field-error">{{ optionFormErrors.label }}</span>
                </div>
                <div class="field">
                  <label class="field-label">Slug <span class="req">*</span></label>
                  <input v-model="optionForm.slug" type="text" class="field-input" :disabled="!!optionEditingId" />
                  <span v-if="optionFormErrors.slug" class="field-error">{{ optionFormErrors.slug }}</span>
                </div>
              </div>

              <div class="field">
                <label class="field-label">Enabled</label>
                <label class="toggle">
                  <input v-model="optionForm.enabled" type="checkbox" />
                  <span>{{ optionForm.enabled ? '已启用' : '已禁用' }}</span>
                </label>
              </div>

              <div class="field">
                <label class="field-label">Description</label>
                <textarea v-model="optionForm.description" class="field-textarea" rows="2"></textarea>
              </div>

              <div class="field">
                <label class="field-label">Price Multiplier</label>
                <input v-model.number="optionForm.price_multiplier" type="number" min="0.01" step="0.1" class="field-input field-input-sm" />
                <span v-if="optionFormErrors.price_multiplier" class="field-error">{{ optionFormErrors.price_multiplier }}</span>
              </div>

              <h4 class="field-group-title">Runtime Budget</h4>

              <div class="field-row">
                <div class="field">
                  <label class="field-label">Max Input Tokens</label>
                  <input v-model="optionForm.runtime_budget_max_input" type="number" min="1" class="field-input" placeholder="—" />
                  <span v-if="optionFormErrors.runtime_budget_max_input" class="field-error">{{ optionFormErrors.runtime_budget_max_input }}</span>
                </div>
                <div class="field">
                  <label class="field-label">Max Output Tokens</label>
                  <input v-model="optionForm.runtime_budget_max_output" type="number" min="1" class="field-input" placeholder="—" />
                  <span v-if="optionFormErrors.runtime_budget_max_output" class="field-error">{{ optionFormErrors.runtime_budget_max_output }}</span>
                </div>
              </div>

              <div class="field">
                <label class="field-label">Prompt Buffer Tokens</label>
                <input v-model="optionForm.runtime_budget_prompt_buffer" type="number" min="0" class="field-input field-input-sm" placeholder="—" />
                <span v-if="optionFormErrors.runtime_budget_prompt_buffer" class="field-error">{{ optionFormErrors.runtime_budget_prompt_buffer }}</span>
              </div>

              <h4 class="field-group-title">Selection（路由配置）</h4>

              <button class="json-toggle-btn" type="button" @click="selectionJsonExpanded = !selectionJsonExpanded">
                {{ selectionJsonExpanded ? '收起高级 JSON' : '展开高级 JSON 编辑' }}
              </button>

              <div v-if="selectionJsonExpanded" class="field">
                <textarea v-model="optionForm.selection_json" class="field-textarea field-textarea-code" rows="8" spellcheck="false"></textarea>
                <span v-if="optionFormErrors.selection_json" class="field-error">{{ optionFormErrors.selection_json }}</span>
              </div>
              <p v-else class="hint">
                <template v-if="optionForm.selection_json.trim()">
                  <template v-if="selectionRouteCount !== null">{{ selectionRouteCount }} route(s)</template>
                  <template v-else>无效 JSON</template>
                </template>
                <template v-else>无路由</template>
              </p>
            </div>

            <div class="drawer-foot">
              <button class="btn btn-ghost" type="button" @click="optionDrawerOpen = false" :disabled="optionSaving">取消</button>
              <button class="btn btn-primary" type="button" @click="saveOption" :disabled="optionSaving">
                {{ optionSaving ? '保存中…' : '保存' }}
              </button>
            </div>
          </div>
        </div>
      </Transition>
    </teleport>
  </div>
</template>

<style scoped>
.ask-mode {
  display: flex;
  flex-direction: column;
  gap: 40px;
}

/* --- Section header --- */

.section-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
}

.section-head h3 {
  margin: 0;
  font-size: 15px;
  font-weight: 700;
}

.section-bar {
  height: 2px;
  background: var(--theme--primary);
  border-radius: 1px;
  margin-bottom: 16px;
}

.section-head-actions {
  display: flex;
  gap: 12px;
}

.count-label {
  font-weight: 400;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  margin-left: 8px;
}

/* --- Text buttons --- */

.text-btn {
  border: 0;
  background: transparent;
  color: var(--theme--primary);
  cursor: pointer;
  font: inherit;
  font-size: 12px;
  padding: 0;
}

.text-btn:hover {
  text-decoration: underline;
}

.text-btn.muted {
  color: var(--theme--foreground-subdued);
}

.text-btn:focus-visible {
  outline: 2px solid var(--theme--primary);
  outline-offset: 2px;
  border-radius: 2px;
}

/* --- Callouts --- */

.callout {
  padding: 12px 16px;
  border-radius: 4px;
  font-size: 13px;
  line-height: 1.5;
  margin-bottom: 12px;
}

.callout p {
  margin: 0 0 4px;
}

.callout-warning {
  background: color-mix(in srgb, var(--theme--warning, #f59e0b) 8%, var(--theme--background));
  color: var(--theme--foreground);
}

.callout-danger {
  background: color-mix(in srgb, var(--theme--danger, #e53935) 8%, var(--theme--background));
  color: var(--theme--foreground);
}

.callout-info {
  background: color-mix(in srgb, var(--theme--primary) 6%, var(--theme--background));
  color: var(--theme--foreground);
}

.callout-success {
  background: color-mix(in srgb, var(--theme--success, #10b981) 8%, var(--theme--background));
  color: var(--theme--foreground);
}

.callout strong {
  color: inherit;
}

.hint {
  color: var(--theme--foreground-subdued);
  font-size: 13px;
}

/* --- Config groups --- */

.config-groups {
  display: flex;
  flex-direction: column;
}

.config-group {
  border-top: 1px solid var(--theme--border-color);
  padding-top: 8px;
}

.config-group:first-child {
  border-top: none;
  padding-top: 0;
}

.config-group-label {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: var(--theme--foreground-subdued);
  margin-bottom: 4px;
}

/* --- Config list (definition list) --- */

.config-list {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 4px 16px;
  margin: 0;
}

.config-row {
  display: contents;
}

.config-row dt {
  font-size: 12px;
  color: var(--theme--foreground-subdued);
  padding: 4px 0;
  white-space: nowrap;
}

.config-row dd {
  font-size: 13px;
  font-weight: 500;
  padding: 4px 0;
  margin: 0;
}

.config-row-active dt,
.config-row-active dd {
  color: var(--theme--primary);
}

.config-row-warn dt,
.config-row-warn dd {
  color: var(--theme--warning, #f59e0b);
}

.effective-hint {
  display: block;
  font-size: 11px;
  font-weight: 600;
  color: var(--theme--warning, #f59e0b);
}

/* --- Option list --- */

.option-list {
  display: flex;
  flex-direction: column;
  border: 1px solid var(--theme--border-color);
  border-radius: 4px;
  overflow: hidden;
}

.option-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--theme--border-color);
  transition: background-color 160ms ease;
  cursor: default;
}

.option-row:last-child {
  border-bottom: 0;
}

.option-row.is-effective {
  background: color-mix(in srgb, var(--theme--primary) 6%, var(--theme--background));
}

.option-row.is-disabled {
  opacity: 0.55;
}

.option-row:hover {
  background: var(--theme--background-subdued);
}

.option-row.is-effective:hover {
  background: color-mix(in srgb, var(--theme--primary) 8%, var(--theme--background));
}

.option-row:focus-visible {
  outline: 2px solid var(--theme--primary);
  outline-offset: -2px;
  border-radius: 2px;
}

.option-main {
  min-width: 0;
  flex: 1;
}

.option-identity {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.option-label {
  font-size: 13px;
}

.option-slug {
  font-size: 11px;
  color: var(--theme--foreground-subdued);
  background: var(--theme--background-subdued);
  padding: 2px 8px;
  border-radius: 3px;
}

/* --- Badges --- */

.badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 3px;
  font-size: 11px;
  font-weight: 700;
  line-height: 1.5;
}

.badge-primary {
  background: var(--theme--primary);
  color: var(--theme--primary-background);
  box-shadow: 0 1px 4px color-mix(in srgb, var(--theme--primary) 30%, transparent);
}

.badge-muted {
  background: var(--theme--background-subdued);
  color: var(--theme--foreground-subdued);
}

.badge-plain {
  background: transparent;
  color: var(--theme--foreground-subdued);
  border: 1px solid var(--theme--border-color);
}

/* --- Option meta --- */

.option-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 16px;
  margin-top: 4px;
  font-size: 12px;
  color: var(--theme--foreground-subdued);
  line-height: 1.4;
}

.meta-item {
  display: inline;
}

.meta-key {
  font-weight: 600;
  font-size: 11px;
  margin-right: 4px;
}

.meta-desc {
  color: var(--theme--foreground-subdued);
}

.key-route {
  font-weight: 600;
  color: var(--theme--primary);
}

.option-actions {
  flex: 0 0 auto;
  display: flex;
  gap: 8px;
  align-items: center;
}

/* ========================================================================= */
/* Drawer                                                                    */
/* ========================================================================= */

/* --- Drawer slide transition --- */

.drawer-slide-enter-active {
  transition: opacity 200ms ease;
}

.drawer-slide-enter-active .drawer {
  transition: transform 200ms cubic-bezier(0.25, 1, 0.5, 1);
}

.drawer-slide-leave-active {
  transition: opacity 200ms ease;
}

.drawer-slide-leave-active .drawer {
  transition: transform 200ms cubic-bezier(0.25, 1, 0.5, 1);
}

.drawer-slide-enter-from,
.drawer-slide-leave-to {
  opacity: 0;
}

.drawer-slide-enter-from .drawer,
.drawer-slide-leave-to .drawer {
  transform: translateX(100%);
}

.drawer-overlay {
  position: fixed;
  inset: 0;
  z-index: 500;
  background: color-mix(in srgb, var(--theme--foreground) 40%, transparent);
  display: flex;
  justify-content: flex-end;
}

.drawer {
  width: 480px;
  max-width: 90vw;
  background: var(--theme--background);
  display: flex;
  flex-direction: column;
  box-shadow: -4px 0 24px rgba(0, 0, 0, 0.12);
}

.drawer-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 24px;
  border-bottom: 1px solid var(--theme--border-color);
}

.drawer-head h2 {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
}

.icon-btn {
  border: 0;
  background: transparent;
  color: var(--theme--foreground-subdued);
  cursor: pointer;
  padding: 4px;
  border-radius: 4px;
  display: flex;
}

.icon-btn:hover {
  color: var(--theme--foreground);
  background: var(--theme--background-subdued);
}

.drawer-body {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.drawer-foot {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 12px 24px;
  border-top: 1px solid var(--theme--border-color);
}

/* --- Buttons --- */

.btn {
  border: 1px solid var(--theme--border-color);
  border-radius: 4px;
  padding: 8px 16px;
  cursor: pointer;
  font: inherit;
  font-size: 13px;
  font-weight: 600;
  transition: opacity 160ms ease, background 160ms ease;
}

.btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.btn:focus-visible {
  outline: 2px solid var(--theme--primary);
  outline-offset: 2px;
}

.btn-ghost {
  background: var(--theme--background);
  color: var(--theme--foreground);
}

.btn-ghost:hover {
  background: var(--theme--background-subdued);
}

.btn-ghost:active {
  opacity: 0.8;
}

.btn-primary {
  background: var(--theme--primary);
  color: var(--theme--primary-background);
  border-color: var(--theme--primary);
}

.btn-primary:hover {
  opacity: 0.9;
}

.btn-primary:active {
  opacity: 0.8;
}

/* ========================================================================= */
/* Form fields                                                               */
/* ========================================================================= */

.field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.field-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--theme--foreground-subdued);
}

.req {
  color: var(--theme--danger, #e53935);
}

.field-input,
.field-textarea {
  border: 1px solid var(--theme--border-color);
  border-radius: 4px;
  padding: 8px 12px;
  font: inherit;
  font-size: 13px;
  background: var(--theme--background);
  color: var(--theme--foreground);
  width: 100%;
}

.field-input:focus,
.field-textarea:focus {
  outline: none;
  border-color: var(--theme--primary);
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--theme--primary) 20%, transparent);
}

.field-input:disabled {
  opacity: 0.6;
  background: var(--theme--background-subdued);
}

.field-input-sm {
  max-width: 200px;
}

.field-textarea {
  resize: vertical;
}

.field-textarea-code {
  font-family: monospace;
  font-size: 12px;
  line-height: 1.5;
}

.field-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.field-group-title {
  margin: 4px 0 0;
  font-size: 12px;
  font-weight: 700;
  color: var(--theme--foreground);
  border-bottom: 1px solid var(--theme--border-color);
  padding-bottom: 4px;
}

.field-error {
  color: var(--theme--danger, #e53935);
  font-size: 11px;
}

.toggle {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  font-size: 13px;
}

.toggle input[type="checkbox"] {
  width: 16px;
  height: 16px;
  accent-color: var(--theme--primary);
}

.json-toggle-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: 1px solid var(--theme--border-color);
  border-radius: 4px;
  background: transparent;
  padding: 4px 12px;
  color: var(--theme--foreground-subdued);
  cursor: pointer;
  font: inherit;
  font-size: 12px;
  transition: color 160ms ease, border-color 160ms ease;
}

.json-toggle-btn:hover {
  color: var(--theme--primary);
  border-color: var(--theme--primary);
}

.json-toggle-btn:focus-visible {
  outline: 2px solid var(--theme--primary);
  outline-offset: 2px;
}

@media (max-width: 600px) {
  .drawer {
    width: 100vw;
    max-width: 100vw;
  }
  .field-row {
    grid-template-columns: 1fr;
  }
  .option-row {
    flex-direction: column;
    gap: 8px;
  }
  .option-actions {
    align-self: flex-end;
  }
}
</style>
