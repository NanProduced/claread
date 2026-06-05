<script setup lang="ts">
import { computed } from "vue";
import type { StatusTone } from "../composables/useEvalFormatting";

const props = withDefaults(
  defineProps<{
    label: string;
    tone?: StatusTone;
    size?: "default" | "large";
  }>(),
  {
    tone: "neutral",
    size: "default",
  },
);

const toneClass = computed(() => `is-${props.tone}`);
const sizeClass = computed(() => (props.size === "large" ? "is-large" : null));
</script>

<template>
  <span class="status-pill" :class="[toneClass, sizeClass]">{{ label }}</span>
</template>

<style scoped>
.status-pill {
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  padding: 0 8px;
  border: 1px solid var(--theme--border-color);
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
  color: var(--theme--foreground);
  background: var(--theme--background);
  border-radius: 4px;
}

.status-pill.is-large {
  min-height: 30px;
  padding: 0 12px;
  border-radius: 6px;
}

.status-pill.is-success {
  color: var(--theme--success, #10b981);
  border-color: color-mix(in srgb, var(--theme--success, #10b981) 30%, var(--theme--border-color));
  background: color-mix(in srgb, var(--theme--success, #10b981) 6%, var(--theme--background));
}

.status-pill.is-warning {
  color: #b45309;
  border-color: color-mix(in srgb, var(--theme--warning, #f59e0b) 30%, var(--theme--border-color));
  background: color-mix(in srgb, var(--theme--warning, #f59e0b) 6%, var(--theme--background));
}

.status-pill.is-danger {
  color: var(--theme--danger, #dc2626);
  border-color: color-mix(in srgb, var(--theme--danger, #dc2626) 30%, var(--theme--border-color));
  background: color-mix(in srgb, var(--theme--danger, #dc2626) 6%, var(--theme--background));
}

.status-pill.is-attention {
  color: #b45309;
  border-color: color-mix(in srgb, var(--theme--warning, #f59e0b) 30%, var(--theme--border-color));
  background: color-mix(in srgb, var(--theme--warning, #f59e0b) 6%, var(--theme--background));
}

.status-pill.is-active {
  color: var(--theme--primary, #2563eb);
  border-color: color-mix(in srgb, var(--theme--primary, #2563eb) 30%, var(--theme--border-color));
  background: color-mix(in srgb, var(--theme--primary, #2563eb) 6%, var(--theme--background));
}

.status-pill.is-neutral {
  color: var(--theme--foreground-subdued);
  background: var(--theme--background-page, #f9fafb);
}
</style>
