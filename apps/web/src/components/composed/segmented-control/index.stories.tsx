import type { Meta } from "@ladle/react";
import { useState } from "react";
import { SegmentedControl } from ".";

const options = [
  { value: "daily_reading", label: "日常阅读" },
  { value: "academic", label: "学术摘要" },
  { value: "exam", label: "备考精读" },
] as const;

export default {
  title: "Composed/SegmentedControl",
} satisfies Meta;

export const Default = () => {
  const [value, setValue] = useState<(typeof options)[number]["value"]>("daily_reading");
  return <SegmentedControl label="透读模式" value={value} onValueChange={setValue} options={options} />;
};
