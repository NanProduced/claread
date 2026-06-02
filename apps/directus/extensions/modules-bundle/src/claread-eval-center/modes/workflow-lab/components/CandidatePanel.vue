<script setup>
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
const emit = defineEmits(["refresh", "new", "select", "update:form", "preview", "save"]);

function update(key, value) {
  emit("update:form", { ...props.form, [key]: value });
}
</script>

<template>
  <section class="candidate-panel">
    <aside class="candidate-list">
      <header>
        <div>
          <p>Candidate</p>
          <h2>Prompt variants</h2>
        </div>
        <button type="button" @click="emit('new')">New</button>
      </header>
      <button
        v-for="draft in drafts"
        :key="draft.id"
        type="button"
        class="draft-item"
        :class="{ active: draft.id === selectedId }"
        @click="emit('select', draft)"
      >
        <strong>{{ draft.variant_id }}</strong>
        <small>{{ draft.status }} / {{ draft.few_shot_mode }}</small>
      </button>
      <p v-if="!loading && drafts.length === 0" class="empty">No candidate drafts.</p>
    </aside>

    <main class="candidate-editor">
      <div v-if="error" class="notice error">{{ error }}</div>
      <div v-if="message" class="notice success">{{ message }}</div>

      <div class="editor-grid">
        <label>
          <span>Variant ID</span>
          <input :value="form.variant_id" @input="update('variant_id', $event.target.value)" />
        </label>
        <label>
          <span>Status</span>
          <select :value="form.status" @change="update('status', $event.target.value)">
            <option value="draft">draft</option>
            <option value="ready_for_eval">ready_for_eval</option>
            <option value="archived">archived</option>
          </select>
        </label>
        <label>
          <span>Few-shot mode</span>
          <select :value="form.few_shot_mode" @change="update('few_shot_mode', $event.target.value)">
            <option value="off">off</option>
            <option value="baseline">baseline</option>
            <option value="variant">variant</option>
            <option value="settings">settings</option>
          </select>
        </label>
        <label>
          <span>Notes</span>
          <input :value="form.notes" @input="update('notes', $event.target.value)" />
        </label>
        <label class="wide">
          <span>Policies JSON</span>
          <textarea :value="form.policies_json" rows="7" spellcheck="false" @input="update('policies_json', $event.target.value)" />
        </label>
        <label class="wide">
          <span>Examples JSON</span>
          <textarea :value="form.examples_json" rows="7" spellcheck="false" @input="update('examples_json', $event.target.value)" />
        </label>
      </div>

      <footer class="editor-actions">
        <div>
          <button type="button" :disabled="previewing" @click="emit('preview')">
            {{ previewing ? "Previewing" : "Preview manifest" }}
          </button>
          <button type="button" :disabled="saving" @click="emit('save')">
            {{ saving ? "Saving" : "Save candidate" }}
          </button>
          <button type="button" :disabled="loading" @click="emit('refresh')">Refresh</button>
        </div>
        <p>{{ readyCandidates.length }} ready candidates available for Workflow runs.</p>
      </footer>

      <section v-if="preview" class="preview">
        <header>
          <strong>Snapshot {{ preview.snapshot_hash }}</strong>
          <code>{{ preview.recommended_manifest_path }}</code>
        </header>
        <JsonTreeView :value="preview.manifest_json" label="candidate_manifest" />
      </section>
    </main>
  </section>
</template>

<style scoped>
.candidate-panel {
  display: grid;
  grid-template-columns: minmax(220px, 0.34fr) minmax(0, 1fr);
  gap: 14px;
}
.candidate-list,
.candidate-editor {
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  background: var(--theme--background);
  padding: 14px;
}
header,
.editor-actions {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}
header p,
small,
label span,
.empty,
.editor-actions p {
  margin: 0;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  font-weight: 700;
}
header h2 {
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
.draft-item {
  display: block;
  width: 100%;
  margin-top: 8px;
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
.editor-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}
label {
  display: grid;
  gap: 6px;
}
.wide {
  grid-column: 1 / -1;
}
textarea {
  resize: vertical;
  font-family: var(--theme--fonts--monospace--font-family, monospace);
  font-size: 12px;
}
.editor-actions {
  margin-top: 12px;
}
.editor-actions div {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
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
@media (max-width: 980px) {
  .candidate-panel,
  .editor-grid {
    grid-template-columns: 1fr;
  }
}
</style>
