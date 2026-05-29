import { createLauncherComponent } from "../shared/inspector-launcher.js";

export default {
  id: "claread-inspector-launcher-interface",
  name: "Claread Inspector Launcher Interface",
  icon: "open_in_new",
  description: "在详情页内提供 Render Scene Inspector 跳转入口。",
  types: ["uuid", "string", "text"],
  options: [
    {
      field: "buttonLabel",
      type: "string",
      name: "Button Label",
      meta: {
        width: "full",
        interface: "input",
      },
    },
    {
      field: "showContext",
      type: "boolean",
      name: "Show Context",
      meta: {
        width: "half",
        interface: "boolean",
      },
      schema: {
        default_value: false,
      },
    },
    {
      field: "target",
      type: "string",
      name: "Context Target",
      meta: {
        width: "half",
        interface: "select-dropdown",
        options: {
          choices: [
            { text: "Analysis Record", value: "record" },
          ],
        },
      },
      schema: {
        default_value: "record",
      },
    },
  ],
  component: createLauncherComponent({ mode: "interface" }),
};
