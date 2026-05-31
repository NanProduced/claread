<script setup>
import { computed, onMounted, ref } from "vue";
import ResultBlock from "../components/ResultBlock.vue";

const draftsEndpoint = "/items/eval_prompt_variant_drafts";
const previewEndpoint = "/eval-center/prompt-variants/manifest-preview";

const loading = ref(false);
const saving = ref(false);
const previewing = ref(false);
const error = ref("");
const message = ref("");
const drafts = ref([]);
const selectedId = ref("");
const preview = ref(null);

const form = ref({
  variant_id: "",
  status: "draft",
  scope: "workflow_eval",
  few_shot_mode: "off",
  notes: "",
  policies_json: "{}",
  examples_json: "{}",
});

const selectedDraft = computed(() => drafts.value.find((item) => item.id === selectedId.value));

onMounted(() => {
  void loadDrafts();
});

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    credentials: "include",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = payload?.errors?.[0]?.message || payload?.message || `Request failed: ${response.status}`;
    throw new Error(detail);
  }
  return payload?.data !== undefined ? payload.data : payload;
}

async function loadDrafts() {
  loading.value = true;
  error.value = "";
  try {
    const data = await fetchJson(`${draftsEndpoint}?sort=-date_updated,-date_created&limit=100`);
    drafts.value = Array.isArray(data) ? data : [];
    if (!selectedId.value && drafts.value.length) selectDraft(drafts.value[0]);
  } catch (err) {
    error.value = err?.message || "Failed to load prompt variant drafts.";
  } finally {
    loading.value = false;
  }
}

function newDraft() {
  selectedId.value = "";
  preview.value = null;
  message.value = "";
  form.value = {
    variant_id: "",
    status: "draft",
    scope: "workflow_eval",
    few_shot_mode: "off",
    notes: "",
    policies_json: "{}",
    examples_json: "{}",
  };
}

function selectDraft(draft) {
  selectedId.value = draft.id;
  preview.value = null;
  message.value = "";
  form.value = {
    variant_id: draft.variant_id || "",
    status: draft.status || "draft",
    scope: draft.scope || "workflow_eval",
    few_shot_mode: draft.few_shot_mode || "off",
    notes: draft.notes || "",
    policies_json: JSON.stringify(draft.policies_json || {}, null, 2),
    examples_json: JSON.stringify(draft.examples_json || {}, null, 2),
  };
}

function parsedJson(text, field) {
  try {
    const value = JSON.parse(text || "{}");
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      throw new Error(`${field} must be a JSON object.`);
    }
    return value;
  } catch (err) {
    throw new Error(`${field}: ${err.message}`);
  }
}

function buildPayload(extra = {}) {
  return {
    variant_id: form.value.variant_id.trim(),
    target: "article_analysis",
    status: form.value.status,
    scope: form.value.scope,
    few_shot_mode: form.value.few_shot_mode,
    notes: form.value.notes,
    policies_json: parsedJson(form.value.policies_json, "policies_json"),
    examples_json: parsedJson(form.value.examples_json, "examples_json"),
    ...extra,
  };
}

async function previewManifest() {
  previewing.value = true;
  error.value = "";
  message.value = "";
  try {
    const data = await fetchJson(previewEndpoint, {
      method: "POST",
      body: JSON.stringify(buildPayload()),
    });
    preview.value = data;
    return data;
  } catch (err) {
    error.value = err?.message || "Failed to preview manifest.";
    return null;
  } finally {
    previewing.value = false;
  }
}

async function saveDraft() {
  saving.value = true;
  error.value = "";
  message.value = "";
  try {
    const manifestPreview = await previewManifest();
    if (!manifestPreview) return;
    const payload = buildPayload({
      manifest_json: manifestPreview.manifest_json,
      snapshot_hash: manifestPreview.snapshot_hash,
    });
    if (selectedId.value) {
      await fetchJson(`${draftsEndpoint}/${encodeURIComponent(selectedId.value)}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      });
    } else {
      const created = await fetchJson(draftsEndpoint, {
        method: "POST",
        body: JSON.stringify(payload),
      });
      selectedId.value = created.id;
    }
    message.value = "Draft saved.";
    await loadDrafts();
  } catch (err) {
    error.value = err?.message || "Failed to save draft.";
  } finally {
    saving.value = false;
  }
}

function copyToClipboard(text) {
  navigator.clipboard.writeText(text || "");
}
</script>

<template>
  <section class="prompt-variants">
    <div class="section-heading">
      <h2>Prompt Variant Drafts</h2>
      <p>Manage eval-only prompt variant drafts and immutable manifest previews. This page does not edit production prompt YAML.</p>
    </div>

    <div v-if="error" class="notice error">{{ error }}</div>
    <div v-if="message" class="notice success">{{ message }}</div>

    <div class="layout">
      <aside class="draft-list">
        <div class="list-header">
          <strong>Drafts</strong>
          <button type="button" @click="newDraft">New</button>
        </div>
        <button
          v-for="draft in drafts"
          :key="draft.id"
          class="draft-item"
          :class="{ active: draft.id === selectedId }"
          type="button"
          @click="selectDraft(draft)"
        >
          <span>{{ draft.variant_id }}</span>
          <small>{{ draft.status }} / {{ draft.few_shot_mode }}</small>
        </button>
        <div v-if="loading" class="empty">Loading...</div>
        <div v-else-if="!drafts.length" class="empty">No drafts yet.</div>
      </aside>

      <main class="editor">
        <div class="control-grid">
          <label>
            <span>Variant ID</span>
            <input v-model="form.variant_id" placeholder="minimal-diverse-v1" />
          </label>
          <label>
            <span>Status</span>
            <select v-model="form.status">
              <option value="draft">draft</option>
              <option value="ready_for_eval">ready_for_eval</option>
              <option value="archived">archived</option>
            </select>
          </label>
          <label>
            <span>Scope</span>
            <select v-model="form.scope">
              <option value="workflow_eval">workflow_eval</option>
              <option value="node_probe">node_probe</option>
            </select>
          </label>
          <label>
            <span>Few-shot Mode</span>
            <select v-model="form.few_shot_mode">
              <option value="off">off</option>
              <option value="baseline">baseline</option>
              <option value="variant">variant</option>
              <option value="settings">settings</option>
            </select>
          </label>
        </div>

        <label class="stacked">
          <span>Notes</span>
          <textarea v-model="form.notes" rows="3" />
        </label>

        <div class="json-grid">
          <label class="stacked">
            <span>Policies JSON</span>
            <textarea v-model="form.policies_json" rows="12" spellcheck="false" />
          </label>
          <label class="stacked">
            <span>Examples JSON</span>
            <textarea v-model="form.examples_json" rows="12" spellcheck="false" />
          </label>
        </div>

        <div class="actions">
          <button type="button" :disabled="previewing || saving" @click="previewManifest">
            {{ previewing ? "Previewing..." : "Preview Manifest" }}
          </button>
          <button class="primary" type="button" :disabled="saving" @click="saveDraft">
            {{ saving ? "Saving..." : "Save Draft" }}
          </button>
        </div>

        <ResultBlock v-if="preview" title="Manifest Preview" :open="true">
          <div class="preview-meta">
            <span>Snapshot</span>
            <code>{{ preview.snapshot_hash }}</code>
            <button type="button" @click="copyToClipboard(preview.yaml_content)">Copy YAML</button>
          </div>
          <div class="preview-meta">
            <span>Recommended path</span>
            <code>{{ preview.recommended_manifest_path }}</code>
          </div>
          <pre>{{ preview.yaml_content }}</pre>
        </ResultBlock>
      </main>
    </div>
  </section>
</template>

<style scoped>
.prompt-variants {
  max-width: 1120px;
}

.section-heading h2 {
  margin: 0 0 4px;
  font-size: 18px;
}

.section-heading p,
.empty {
  color: var(--theme--foreground-subdued);
  font-size: 13px;
}

.notice {
  padding: 10px 12px;
  border-radius: 4px;
  margin: 12px 0;
  font-size: 13px;
}

.notice.error {
  background: var(--theme--danger-background);
}

.notice.success {
  background: var(--theme--success-background);
}

.layout {
  display: grid;
  grid-template-columns: 260px minmax(0, 1fr);
  gap: 20px;
  margin-top: 16px;
}

.draft-list {
  border-right: 1px solid var(--theme--border-color);
  padding-right: 14px;
}

.list-header,
.actions,
.preview-meta {
  display: flex;
  align-items: center;
  gap: 10px;
}

.list-header {
  justify-content: space-between;
  margin-bottom: 10px;
}

button {
  border: 1px solid var(--theme--border-color);
  border-radius: 4px;
  background: var(--theme--background);
  color: var(--theme--foreground);
  cursor: pointer;
  font: inherit;
  font-size: 12px;
  padding: 7px 10px;
}

button.primary {
  border-color: var(--theme--primary);
  background: var(--theme--primary);
  color: var(--theme--background);
}

button:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.draft-item {
  display: flex;
  width: 100%;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  margin-bottom: 8px;
  text-align: left;
}

.draft-item.active {
  border-color: var(--theme--primary);
}

.draft-item span {
  font-weight: 700;
}

.draft-item small {
  color: var(--theme--foreground-subdued);
}

.control-grid,
.json-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.stacked,
.control-grid label {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.stacked {
  margin-top: 14px;
}

label span {
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  font-weight: 700;
}

input,
select,
textarea {
  border: 1px solid var(--theme--border-color);
  border-radius: 4px;
  background: var(--theme--background);
  color: var(--theme--foreground);
  font: inherit;
  font-size: 13px;
  padding: 8px 10px;
}

textarea {
  resize: vertical;
}

.actions {
  margin: 16px 0;
}

.preview-meta {
  margin-bottom: 8px;
}

.preview-meta span {
  min-width: 120px;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  font-weight: 700;
}

pre {
  overflow: auto;
  border: 1px solid var(--theme--border-color);
  border-radius: 4px;
  background: var(--theme--background-subdued);
  padding: 12px;
  white-space: pre-wrap;
}

@media (max-width: 900px) {
  .layout,
  .control-grid,
  .json-grid {
    grid-template-columns: 1fr;
  }

  .draft-list {
    border-right: 0;
    border-bottom: 1px solid var(--theme--border-color);
    padding: 0 0 14px;
  }
}
</style>
