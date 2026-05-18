import type { Meta } from "@ladle/react";
import { StatCard } from ".";

export default {
  title: "Composed/StatCard",
} satisfies Meta;

export const Default = () => (
  <StatCard
    title="档案状态"
    items={[
      { label: "总记录", value: 7 },
      { label: "同步", value: "已同步" },
    ]}
  />
);
