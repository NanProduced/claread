import { defineModule } from "@directus/extensions-sdk";
import { defineComponent, h } from "vue";

const RagWorkbenchView = defineComponent({
  name: "RagWorkbenchView",
  setup() {
    return () =>
      h("div", { style: "padding: 24px; display: grid; gap: 12px;" }, [
        h("h1", { style: "margin: 0; font-size: 24px;" }, "RAG Workbench"),
        h(
          "p",
          { style: "margin: 0; color: var(--theme--foreground-subdued);" },
          "Bootstrap shell for drafting, reviewing, and promoting Claread RAG examples."
        ),
      ]);
  },
});

export default defineModule({
  id: "rag-workbench",
  name: "RAG Workbench",
  icon: "dataset",
  routes: [
    {
      path: "",
      component: RagWorkbenchView,
    },
  ],
});
