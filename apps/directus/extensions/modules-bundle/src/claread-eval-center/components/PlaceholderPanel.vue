<script setup>
defineProps({
  mode: {
    type: Object,
    required: true,
  },
});
</script>

<template>
  <section class="placeholder-panel">
    <div>
      <p class="eyebrow">{{ mode.kicker }}</p>
      <h2>{{ mode.label }}</h2>
      <p>{{ mode.description }}</p>
    </div>

    <div class="question-list">
      <h3>该模式首屏要回答的问题</h3>
      <ul>
        <li v-for="question in mode.questions" :key="question">{{ question }}</li>
      </ul>
    </div>

    <p class="muted-line">后端能力和持久化结构会分阶段接入。当前先固定 Eval Center 的整体入口与模式边界。</p>
  </section>
</template>

<style scoped>
.placeholder-panel {
  display: grid;
  gap: 18px;
  min-width: 0;
  border: 1px solid var(--theme--border-color);
  border-radius: 8px;
  background: var(--theme--background);
  padding: 20px;
}

.placeholder-panel h2,
.question-list h3 {
  margin: 0;
}

.placeholder-panel p,
.muted-line {
  max-width: 780px;
  color: var(--theme--foreground-subdued);
  line-height: 1.7;
}

.eyebrow {
  margin: 0 0 4px !important;
  color: var(--theme--foreground-subdued);
  font-size: 12px;
  font-weight: 700;
}

.question-list {
  border-top: 1px solid var(--theme--border-color);
  padding-top: 16px;
}

.question-list ul {
  display: grid;
  gap: 8px;
  margin: 10px 0 0;
  padding-left: 18px;
}
</style>
