import type { Meta } from "@ladle/react";
import { SelectField } from ".";

const items = [
  { label: "Warm Paper (默认)", value: "warm", description: "暖纸底色，适合长时阅读。" },
  { label: "Clean White", value: "white" },
  { label: "Sage Green", value: "sage" },
];

export default {
  title: "Composed/SelectField",
} satisfies Meta;

export const Default = () => (
  <SelectField
    label="背景纸色"
    description="选择 Reader 模式的默认底色。"
    items={items}
    defaultValue="warm"
  />
);
