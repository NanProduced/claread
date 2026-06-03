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
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
  color: var(--theme--foreground);
  background: var(--theme--background);
}

.status-pill.is-large {
  min-height: 30px;
  padding: 0 12px;
}

.status-pill.is-success {
  color: var(--theme--success);
  border-color: color-mix(in srgb, var(--theme--success) 45%, var(--theme--border-color));
}

.status-pill.is-warning {
  color: var(--theme--warning);
  border-color: color-mix(in srgb, var(--theme--warning) 45%, var(--theme--border-color));
}

.status-pill.is-danger {
  color: var(--theme--danger);
  border-color: color-mix(in srgb, var(--theme--danger) 45%, var(--theme--border-color));
}

.status-pill.is-attention {
  color: var(--theme--warning);
  border-color: color-mix(in srgb, var(--theme--warning) 45%, var(--theme--border-color));
}

.status-pill.is-active {
  color: var(--theme--primary);
  border-color: color-mix(in srgb, var(--theme--primary) 45%, var(--theme--border-color));
}

.status-pill.is-neutral {
  color: var(--theme--foreground-subdued);
}
</style>
