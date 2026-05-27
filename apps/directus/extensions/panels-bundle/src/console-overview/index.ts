import { definePanel } from "@directus/extensions-sdk";
import { defineComponent, h } from "vue";

const ConsoleOverviewPanel = defineComponent({
  name: "ConsoleOverviewPanel",
  setup() {
    return () =>
      h("div", { style: "padding: 16px; display: grid; gap: 8px;" }, [
        h("strong", { style: "font-size: 16px;" }, "Claread Console Overview"),
        h(
          "span",
          { style: "color: var(--theme--foreground-subdued);" },
          "Bootstrap overview panel. Claread operating metrics and workflow summaries land here later."
        ),
      ]);
  },
});

export default definePanel({
  id: "console-overview",
  name: "Console Overview",
  icon: "dashboard",
  description: "Bootstrap placeholder panel for Claread Console.",
  component: ConsoleOverviewPanel,
  minWidth: 12,
  minHeight: 8,
});
