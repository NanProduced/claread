import { defineModule } from "@directus/extensions-sdk";
import { defineComponent, h } from "vue";

const WorkflowOutputLabView = defineComponent({
  name: "WorkflowOutputLabView",
  setup() {
    return () =>
      h("div", { style: "padding: 24px; display: grid; gap: 12px;" }, [
        h("h1", { style: "margin: 0; font-size: 24px;" }, "Workflow Output Lab"),
        h(
          "p",
          { style: "margin: 0; color: var(--theme--foreground-subdued);" },
          "Bootstrap shell for Reader output quality review, token observation, and failure triage."
        ),
      ]);
  },
});

export default defineModule({
  id: "workflow-output-lab",
  name: "Workflow Output Lab",
  icon: "analytics",
  routes: [
    {
      path: "",
      component: WorkflowOutputLabView,
    },
  ],
});
