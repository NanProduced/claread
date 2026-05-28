import { renderMappedBadge } from "../shared/enum-display.js";

export default {
  id: "claread-event-type-display",
  name: "Claread Event Type Display",
  icon: "event",
  description: "以中文标签展示任务事件类型。",
  types: ["string", "text"],
  component: function EventTypeDisplay({ value }) {
    return renderMappedBadge(value, "event_type", true);
  },
};

