import type { Meta } from "@ladle/react";
import { useState } from "react";
import { FilterBar } from ".";

const items = [
  { value: "all", label: "全部" },
  { value: "favorite", label: "收藏" },
  { value: "highlight", label: "高亮" },
  { value: "note", label: "笔记" },
];

export default {
  title: "Composed/FilterBar",
} satisfies Meta;

export const Default = () => {
  const [value, setValue] = useState("all");
  return <FilterBar items={items} activeValue={value} onValueChange={setValue} />;
};
