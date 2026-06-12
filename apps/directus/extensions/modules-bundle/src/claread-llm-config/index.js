import LlmConfigModule from "./module.vue";

export default {
  id: "claread-llm-config",
  name: "LLM Config",
  icon: "settings",
  routes: [
    {
      path: "",
      component: LlmConfigModule,
    },
  ],
};
