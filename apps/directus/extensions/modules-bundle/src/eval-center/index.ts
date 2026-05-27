import { defineModule } from "@directus/extensions-sdk";
import { defineComponent, h } from "vue";

const EvalCenterView = defineComponent({
  name: "EvalCenterView",
  setup() {
    return () =>
      h("div", { style: "padding: 24px; display: grid; gap: 12px;" }, [
        h("h1", { style: "margin: 0; font-size: 24px;" }, "Eval Center"),
        h(
          "p",
          { style: "margin: 0; color: var(--theme--foreground-subdued);" },
          "Bootstrap shell for dataset governance, rubric management, and experiment review."
        ),
      ]);
  },
});

export default defineModule({
  id: "eval-center",
  name: "Eval Center",
  icon: "fact_check",
  routes: [
    {
      path: "",
      component: EvalCenterView,
    },
  ],
});
