<script setup>
import { computed, inject, provide, reactive, ref, watch } from "vue";

defineOptions({ name: "JsonTreeView" });

const props = defineProps({
  value: {
    type: null,
    default: null,
  },
  label: {
    type: [String, Number],
    default: "",
  },
  depth: {
    type: Number,
    default: 0,
  },
  emptyText: {
    type: String,
    default: "无数据",
  },
});

const CONTEXT_KEY = "claread-json-tree-context";
const parentContext = inject(CONTEXT_KEY, null);
const isRoot = computed(() => props.depth === 0);

const localContext = isRoot.value
  ? reactive({
      viewMode: "tree",
      toggleSignal: 0,
      allExpanded: false,
      copied: false,
    })
  : parentContext;

provide(CONTEXT_KEY, localContext);

const isArray = computed(() => Array.isArray(props.value));
const isObject = computed(() => props.value !== null && typeof props.value === "object" && !Array.isArray(props.value));
const isExpandable = computed(() => isArray.value || isObject.value);

const entries = computed(() => {
  if (isArray.value) {
    return props.value.map((item, index) => ({
      key: index,
      value: item,
    }));
  }
  if (isObject.value) {
    return Object.entries(props.value).map(([key, value]) => ({ key, value }));
  }
  return [];
});

const openState = ref(props.depth <= 1);

watch(
  () => localContext?.toggleSignal,
  () => {
    if (isExpandable.value) openState.value = localContext.allExpanded ? true : props.depth === 0;
  },
);

const summaryText = computed(() => {
  if (isArray.value) return `${props.value.length} 项`;
  if (isObject.value) return `${entries.value.length} 个字段`;
  return "";
});

const rawJsonText = computed(() => {
  if (props.value === null || props.value === undefined) return "";
  if (typeof props.value === "string") return props.value;
  return JSON.stringify(props.value, null, 2);
});

function getTypeClass(value) {
  if (value === null) return "type-null";
  if (typeof value === "string") return "type-string";
  if (typeof value === "number") return "type-number";
  if (typeof value === "boolean") return "type-boolean";
  return "type-other";
}

function formatValue(value) {
  if (value === null) return "null";
  if (typeof value === "string") return `"${value}"`;
  return String(value);
}

function toggleNode(event) {
  openState.value = event.target.open;
}

function showTree() {
  localContext.viewMode = "tree";
}

function showRaw() {
  localContext.viewMode = "raw";
}

function toggleExpandAll() {
  localContext.allExpanded = !localContext.allExpanded;
  localContext.toggleSignal += 1;
}

async function copyJson() {
  if (!rawJsonText.value || !navigator?.clipboard?.writeText) return;
  await navigator.clipboard.writeText(rawJsonText.value);
  localContext.copied = true;
  window.setTimeout(() => {
    localContext.copied = false;
  }, 2000);
}
</script>

<template>
  <div class="json-tree-container" :class="{ 'is-root': isRoot }">
    <div v-if="isRoot" class="json-toolbar">
      <div class="toolbar-title">{{ label || "字段树" }}</div>
      <div class="toolbar-actions">
        <div class="action-group">
          <button type="button" class="action-btn" :class="{ active: localContext.viewMode === 'tree' }" @click="showTree">树形</button>
          <button type="button" class="action-btn" :class="{ active: localContext.viewMode === 'raw' }" @click="showRaw">原始数据</button>
        </div>
        <div class="action-divider"></div>
        <button type="button" class="action-btn" :disabled="!isExpandable || localContext.viewMode === 'raw'" @click="toggleExpandAll">
          {{ localContext.allExpanded ? "折叠全部" : "展开全部" }}
        </button>
        <button type="button" class="action-btn" :disabled="!rawJsonText" @click="copyJson">
          {{ localContext.copied ? "已复制" : "复制" }}
        </button>
      </div>
    </div>

    <div class="json-content">
      <div v-if="value === null && depth === 0" class="json-empty">{{ emptyText }}</div>

      <pre v-else-if="isRoot && localContext.viewMode === 'raw'" class="json-raw">{{ rawJsonText }}</pre>

      <div v-else-if="!isExpandable" class="json-leaf">
        <span v-if="label !== undefined && label !== ''" class="json-key" :class="{ 'is-index': typeof label === 'number' }">{{ label }}</span>
        <span v-if="label !== undefined && label !== ''" class="json-colon">:</span>
        <span class="json-value" :class="getTypeClass(value)">{{ formatValue(value) }}</span>
      </div>

      <details v-else class="json-node" :open="openState" @toggle="toggleNode">
        <summary class="json-summary">
          <div class="summary-left">
            <span class="tree-icon">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <polyline points="9 18 15 12 9 6"></polyline>
              </svg>
            </span>
            <span v-if="label !== undefined && label !== ''" class="json-key" :class="{ 'is-index': typeof label === 'number' }">{{ label }}</span>
            <span v-if="label !== undefined && label !== ''" class="json-colon">:</span>
            <span class="node-bracket">{{ isArray ? '[' : '{' }}</span>
          </div>
          <span class="node-meta">{{ summaryText }}</span>
        </summary>
        
        <div class="json-children">
          <JsonTreeView
            v-for="entry in entries"
            :key="`${depth}-${entry.key}`"
            :label="entry.key"
            :value="entry.value"
            :depth="depth + 1"
          />
        </div>
        
        <div class="node-bracket-close">{{ isArray ? ']' : '}' }}</div>
      </details>
    </div>
  </div>
</template>

<style scoped>
.json-tree-container {
  font-family: var(--theme--fonts--sans, system-ui, -apple-system, sans-serif);
  font-size: 14px;
  line-height: 1.5;
  color: var(--theme--foreground, #333);
  min-width: 0;
}

.json-tree-container.is-root {
  border: 1px solid var(--theme--border-color, #e0e0e0);
  border-radius: 8px;
  background: var(--theme--background, #ffffff);
  box-shadow: 0 2px 8px rgba(0,0,0,0.02);
  overflow: hidden;
}

/* Toolbar */
.json-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 16px;
  background: var(--theme--background-page, #f8f9fa);
  border-bottom: 1px solid var(--theme--border-color, #e0e0e0);
}

.toolbar-title {
  font-weight: 600;
  font-size: 13px;
  color: var(--theme--foreground);
}

.toolbar-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.action-group {
  display: flex;
  background: var(--theme--background, #fff);
  border: 1px solid var(--theme--border-color-subdued, #d0d0d0);
  border-radius: 6px;
  overflow: hidden;
}

.action-btn {
  font-family: inherit;
  font-size: 12px;
  font-weight: 500;
  color: var(--theme--foreground-subdued, #666);
  background: transparent;
  border: none;
  padding: 4px 10px;
  cursor: pointer;
  transition: all 0.15s ease;
}

.action-group .action-btn {
  border-right: 1px solid var(--theme--border-color-subdued, #d0d0d0);
}

.action-group .action-btn:last-child {
  border-right: none;
}

.action-btn:hover:not(:disabled) {
  color: var(--theme--foreground);
  background: var(--theme--background-accent, rgba(0,0,0,0.04));
}

.action-btn.active {
  color: var(--theme--primary, #007bff);
  background: color-mix(in srgb, var(--theme--primary, #007bff) 10%, transparent);
  font-weight: 600;
}

.action-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.action-divider {
  width: 1px;
  height: 16px;
  background: var(--theme--border-color-subdued, #e0e0e0);
  margin: 0 4px;
}

/* Content Area */
.json-content {
  padding: 12px 16px;
  font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace;
  font-size: 13px;
}

.json-raw {
  margin: 0;
  padding: 0;
  max-height: 500px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
  color: var(--theme--foreground);
}

.json-empty {
  padding: 16px;
  text-align: center;
  color: var(--theme--foreground-subdued);
  font-family: var(--theme--fonts--sans, system-ui, -apple-system, sans-serif);
}

/* Tree Nodes */
.json-node {
  margin-top: 2px;
}

.json-summary {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  padding: 4px 6px;
  margin-left: -6px; /* Offset the padding for hover effect */
  border-radius: 4px;
  cursor: pointer;
  user-select: none;
  transition: background-color 0.15s;
  list-style: none;
}

.json-summary::-webkit-details-marker {
  display: none;
}

.json-summary:hover {
  background-color: var(--theme--background-accent, rgba(0,0,0,0.03));
}

.summary-left {
  display: flex;
  align-items: baseline;
  gap: 6px;
}

.tree-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  color: var(--theme--foreground-subdued);
  transition: transform 0.2s ease;
  flex-shrink: 0;
  align-self: center;
}

.json-node[open] > .json-summary .tree-icon {
  transform: rotate(90deg);
}

/* Keys and Values */
.json-key {
  color: var(--theme--primary, #206bc4);
  font-weight: 600;
}

.json-key.is-index {
  color: var(--theme--foreground-subdued, #888);
  font-weight: 500;
}

.json-colon {
  color: var(--theme--foreground-subdued);
  margin-right: 4px;
  margin-left: -4px;
}

.node-bracket,
.node-bracket-close {
  color: var(--theme--foreground-subdued);
  font-weight: 600;
}

.node-meta {
  font-family: var(--theme--fonts--sans, system-ui, -apple-system, sans-serif);
  font-size: 11px;
  color: var(--theme--foreground-subdued);
  opacity: 0.8;
  padding-left: 12px;
  white-space: nowrap;
}

.json-children {
  padding-left: 15px; /* Indentation */
  margin-left: 7px; /* Align with center of icon */
  border-left: 1px solid color-mix(in srgb, var(--theme--border-color, #e0e0e0) 60%, transparent);
  display: grid;
  gap: 2px;
}

.json-node[open] > .node-bracket-close {
  display: block;
  padding-left: 22px;
  margin-top: 2px;
}

.json-node:not([open]) > .node-bracket-close {
  display: none;
}

/* Leaf Nodes */
.json-leaf {
  display: flex;
  align-items: baseline;
  gap: 6px;
  padding: 4px 6px;
  margin-left: 16px; /* 22px (icon width + gap) - 6px padding */
}

/* Value Types */
.type-string {
  color: var(--theme--success, #2fb344);
  white-space: pre-wrap;
  word-break: break-word;
}

.type-number {
  color: var(--theme--warning, #f76707);
}

.type-boolean {
  color: var(--theme--danger, #d63939);
  font-weight: 600;
}

.type-null {
  color: var(--theme--foreground-subdued);
  font-style: italic;
}

.type-other {
  color: var(--theme--foreground);
}

@media (max-width: 600px) {
  .json-toolbar {
    flex-direction: column;
    align-items: stretch;
    gap: 12px;
  }
  .toolbar-actions {
    flex-wrap: wrap;
    justify-content: flex-start;
  }
  .action-divider {
    display: none;
  }
}
</style>
