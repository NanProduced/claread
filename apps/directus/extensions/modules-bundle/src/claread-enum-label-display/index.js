import { renderMappedBadge } from "../shared/enum-display.js";

export default {
  id: "claread-enum-label-display",
  name: "Claread Enum Label Display",
  icon: "label",
  description: "将业务枚举值转为中文标签，并保留原始值。",
  types: ["string", "text"],
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
  component: function EnumLabelDisplay({ value, variant, show_raw }) {
    return renderMappedBadge(value, variant ?? "client_source_from_id", show_raw !== false);
  },
};
