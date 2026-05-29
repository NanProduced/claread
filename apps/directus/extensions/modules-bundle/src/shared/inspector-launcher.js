import { defineComponent, h, onMounted, ref, resolveComponent, watch } from "vue";

import { resolveContext } from "./context.js";
import { shortId } from "./format.js";

function buildInspectorUrl({ collection, value, primaryKey }) {
  const params = new URLSearchParams();

  if (collection === "analysis_results") {
    if (value) params.set("record", String(value));
    if (primaryKey) params.set("result", String(primaryKey));
  } else if (value) {
    params.set("record", String(value));
  } else if (primaryKey) {
    params.set("record", String(primaryKey));
  }

  const query = params.toString();
  return `/admin/claread-render-scene-inspector${query ? `?${query}` : ""}`;
}

export function createLauncherComponent({ mode = "interface" } = {}) {
  return defineComponent({
    props: ["value", "collection", "primaryKey", "target", "buttonLabel", "showContext"],
    setup(props) {
      const context = ref(null);
      const VButton = resolveComponent("v-button");
      const VIcon = resolveComponent("v-icon");

      const load = async () => {
        if (!props.showContext) {
          context.value = null;
          return;
        }

        const target = props.target || "record";
        const value = props.collection === "analysis_results" ? props.value : props.primaryKey || props.value;
        context.value = await resolveContext(value, target);
      };

      onMounted(load);
      watch(() => [props.value, props.collection, props.primaryKey, props.target, props.showContext], load, { deep: true });

      return () => {
        const url = buildInspectorUrl({
          collection: props.collection,
          value: props.value,
          primaryKey: props.primaryKey,
        });
        const label = props.buttonLabel || "Open Inspector";
        const title = context.value?.title || shortId(props.value || props.primaryKey);
        const rawValue = String(props.value || props.primaryKey || "");

        return h(
          "div",
          {
            style: {
              display: "flex",
              flexDirection: mode === "display" ? "column" : "row",
              alignItems: mode === "display" ? "flex-start" : "flex-start",
              justifyContent: "space-between",
              gap: "12px",
              minWidth: "0",
              flexWrap: "wrap",
            },
          },
          [
            h(
              "div",
              {
                style: {
                  display: "flex",
                  flexDirection: "column",
                  gap: "4px",
                  minWidth: "0",
                  flex: "1 1 auto",
                },
              },
              [
                props.showContext
                  ? h(
                      "strong",
                      {
                        style: {
                          fontSize: "13px",
                          lineHeight: "20px",
                          color: "var(--theme--foreground, #172940)",
                        },
                      },
                      title,
                    )
                  : null,
                h(
                  "code",
                  {
                    style: {
                      fontSize: "11px",
                      lineHeight: "16px",
                      color: "var(--theme--foreground-subdued, #6B7280)",
                      wordBreak: "break-all",
                    },
                  },
                  rawValue,
                ),
              ].filter(Boolean),
            ),
            h(
              VButton,
              {
                secondary: true,
                onClick: () => window.location.assign(url),
              },
              {
                default: () => [
                  h(VIcon, { name: "open_in_new", small: true }),
                  h(
                    "span",
                    {
                      style: {
                        marginLeft: "6px",
                      },
                    },
                    label,
                  ),
                ],
              },
            ),
          ],
        );
      };
    },
  });
}
