import { renderMappedBadge } from "../shared/enum-display.js";

export default {
  id: "claread-enum-label-interface",
  name: "Claread Enum Label Interface",
  icon: "style",
  description: "在详情页用中文 badge 展示业务枚举值。",
  types: ["string", "text"],
  group: "presentation",
  options: [
    {
      field: "variant",
      type: "string",
      name: "Variant",
      meta: {
        width: "full",
        interface: "select-dropdown",
        options: {
          choices: [
            { text: "Client Source", value: "client_source_from_id" },
            { text: "Usage Scope", value: "usage_scope" },
            { text: "Billing Mode", value: "billing_mode" },
            { text: "Capability Code", value: "capability_code" },
            { text: "Event Type", value: "event_type" },
          ],
        },
      },
    },
    {
      field: "show_raw",
      type: "boolean",
      name: "Show Raw Value",
      meta: {
        width: "half",
        interface: "boolean",
      },
      schema: {
        default_value: true,
      },
    },
  ],
  component: function EnumLabelInterface({ value, variant, show_raw }) {
    return renderMappedBadge(value, variant ?? "client_source_from_id", show_raw !== false);
  },
};
