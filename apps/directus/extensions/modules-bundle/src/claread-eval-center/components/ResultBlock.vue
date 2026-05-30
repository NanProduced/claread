<script setup>
defineProps({
  title: {
    type: String,
    required: true,
  },
  open: {
    type: Boolean,
    default: false,
  },
});
</script>

<template>
  <details class="result-block" :open="open">
    <summary>{{ title }}</summary>
    <slot />
  </details>
</template>

<style scoped>
.result-block {
  border-top: 1px solid var(--theme--border-color);
  padding: 14px 0;
}

.result-block summary {
  cursor: pointer;
  font-weight: 700;
}
</style>
