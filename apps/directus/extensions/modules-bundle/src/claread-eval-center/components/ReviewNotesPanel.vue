<script setup>
import { computed, onMounted, ref, watch } from "vue";

const props = defineProps({
  title: { type: String, default: "Human Review Notes" },
  targetType: { type: String, required: true },
  targetId: { type: String, required: true },
  runId: { type: String, default: "" },
  caseId: { type: String, default: "" },
  promptVariantId: { type: String, default: "" },
  scopeNote: { type: String, default: "" },
});

const notesEndpoint = "/items/eval_review_notes";
const loading = ref(false);
const saving = ref(false);
const error = ref("");
const notes = ref([]);
const verdict = ref("needs_review");
const note = ref("");
const promoteCandidate = ref(false);

const canSave = computed(() => props.targetType && props.targetId && note.value.trim() && !saving.value);

onMounted(() => {
  void loadNotes();
});

watch(
  () => [props.targetType, props.targetId],
  () => {
    note.value = "";
    promoteCandidate.value = false;
    void loadNotes();
  },
);

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
    const message = payload?.errors?.[0]?.message || payload?.message || `Request failed: ${response.status}`;
    throw new Error(message);
  }
  return payload?.data !== undefined ? payload.data : payload;
}

function notesQuery() {
  const params = new URLSearchParams({
    "filter[target_type][_eq]": props.targetType,
    "filter[target_id][_eq]": props.targetId,
    sort: "-date_created",
    limit: "20",
  });
  return `${notesEndpoint}?${params.toString()}`;
}

async function loadNotes() {
  if (!props.targetType || !props.targetId) return;
  loading.value = true;
  error.value = "";
  try {
    const data = await fetchJson(notesQuery());
    notes.value = Array.isArray(data) ? data : [];
  } catch (err) {
    error.value = err?.message || "Failed to load review notes.";
  } finally {
    loading.value = false;
  }
}

async function saveNote() {
  if (!canSave.value) return;
  saving.value = true;
  error.value = "";
  try {
    await fetchJson(notesEndpoint, {
      method: "POST",
      body: JSON.stringify({
        target_type: props.targetType,
        target_id: props.targetId,
        run_id: props.runId || null,
        case_id: props.caseId || null,
        prompt_variant_id: props.promptVariantId || null,
        verdict: verdict.value || null,
        note: note.value.trim(),
        promote_candidate: promoteCandidate.value,
        tags: [],
      }),
    });
    note.value = "";
    promoteCandidate.value = false;
    await loadNotes();
  } catch (err) {
    error.value = err?.message || "Failed to save review note.";
  } finally {
    saving.value = false;
  }
}

function dash(value) {
  return value === null || value === undefined || value === "" ? "-" : value;
}
</script>

<template>
  <section class="review-notes">
    <div class="review-heading">
      <div>
        <h3>{{ title }}</h3>
        <small>{{ targetType }} / {{ targetId }}</small>
      </div>
      <button type="button" :disabled="loading" @click="loadNotes">
        {{ loading ? "Loading" : "Refresh" }}
      </button>
    </div>

    <p v-if="error" class="review-error">{{ error }}</p>
    <p v-if="scopeNote" class="scope-note">{{ scopeNote }}</p>

    <div class="review-form">
      <div class="form-row">
        <label class="form-field select-field">
          <span>Verdict 判定</span>
          <select v-model="verdict">
            <option value="good">good</option>
            <option value="bad">bad</option>
            <option value="mixed">mixed</option>
            <option value="needs_review">needs_review</option>
            <option value="win">win</option>
            <option value="loss">loss</option>
            <option value="tie">tie</option>
            <option value="blocked">blocked</option>
          </select>
        </label>
        <label class="form-field checkbox-field">
          <input v-model="promoteCandidate" type="checkbox" />
          <span>Promote Candidate (推荐候选版本)</span>
        </label>
      </div>
      <label class="form-field note-field">
        <span>Review Note 评审记录</span>
        <textarea v-model="note" rows="3" placeholder="Record human review evidence for promotion decisions (记录人工评测证据与推荐决策)..." />
      </label>
      <div class="form-actions">
        <button type="button" class="primary-button" :disabled="!canSave" @click="saveNote">
          {{ saving ? "Saving..." : "Save Note" }}
        </button>
      </div>
    </div>

    <div class="note-list">
      <article v-for="item in notes" :key="item.id" class="note-item">
        <header>
          <strong>{{ dash(item.verdict) }}</strong>
          <span>{{ dash(item.date_created) }}</span>
        </header>
        <p>{{ item.note }}</p>
        <small v-if="item.promote_candidate">promotion candidate</small>
      </article>
      <p v-if="!loading && notes.length === 0" class="empty-note">No review notes yet.</p>
    </div>
  </section>
</template>

<style scoped>
.review-notes {
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  margin: 12px 0;
  padding: 12px;
}

.review-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
}

.review-heading h3 {
  margin: 0;
  font-size: 14px;
}

.review-heading small,
.empty-note,
.note-item span,
.note-item small,
.scope-note {
  color: var(--theme--foreground-subdued);
  font-size: 12px;
}

.scope-note {
  margin: 10px 0 0;
  border: 1px dashed var(--theme--border-color-subdued, var(--theme--border-color));
  border-radius: 6px;
  padding: 8px 10px;
  line-height: 1.55;
}

.review-form {
  display: flex;
  flex-direction: column;
  gap: 14px;
  margin-top: 16px;
  padding: 16px;
  background: var(--theme--background-subdued, #f8f9fa);
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
}

.review-form .form-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}

.review-form .form-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.review-form .select-field {
  flex: 1;
  min-width: 180px;
}

.review-form .checkbox-field {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  user-select: none;
  font-weight: 600;
  color: var(--theme--foreground);
  margin-top: 20px;
}

.review-form .checkbox-field input[type="checkbox"] {
  width: 16px;
  height: 16px;
  cursor: pointer;
  accent-color: var(--theme--primary);
}

.review-form .note-field {
  display: flex;
  flex-direction: column;
  width: 100%;
}

.review-form .form-field span {
  font-weight: 700;
  font-size: 12px;
  color: var(--theme--foreground-subdued);
}

.review-form select,
.review-form textarea {
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  background: var(--theme--background);
  color: var(--theme--foreground);
  font: inherit;
  font-size: 12px;
  padding: 8px 12px;
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
  width: 100%;
  box-sizing: border-box;
}

.review-form select:focus,
.review-form textarea:focus {
  border-color: var(--theme--primary);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--theme--primary) 12%, transparent);
  outline: none;
}

.review-form textarea {
  min-height: 80px;
  resize: vertical;
}

.review-form .form-actions {
  display: flex;
  justify-content: flex-end;
}

.primary-button,
.review-heading button {
  min-height: 34px;
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  background: var(--theme--background);
  color: var(--theme--foreground);
  font: inherit;
  font-size: 12px;
  padding: 6px 12px;
  font-weight: 700;
  cursor: pointer;
  transition: all 0.2s ease;
}

.primary-button {
  background: var(--theme--primary);
  color: var(--theme--primary-foreground, #fff);
  border-color: var(--theme--primary);
}

.primary-button:hover:not(:disabled) {
  opacity: 0.9;
}

.primary-button:disabled,
.review-heading button:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.review-error {
  color: var(--theme--danger);
  font-size: 12px;
}

.note-list {
  display: grid;
  gap: 8px;
  margin-top: 12px;
}

.note-item {
  border: 1px solid var(--theme--border-color);
  border-radius: 6px;
  padding: 8px 10px;
  background: var(--theme--background);
}
.note-item::before {
  content: "";
  display: inline-block;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--theme--primary);
  margin-right: 6px;
  vertical-align: middle;
}

.note-item header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.note-item p {
  margin: 4px 0;
  white-space: pre-wrap;
}

@media (max-width: 900px) {
  .review-form {
    grid-template-columns: 1fr;
  }
}
</style>
