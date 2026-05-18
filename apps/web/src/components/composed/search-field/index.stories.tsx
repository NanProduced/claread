import type { Meta } from "@ladle/react";
import { useState } from "react";
import { SearchField } from ".";

export default {
  title: "Composed/SearchField",
} satisfies Meta;

export const Default = () => {
  const [value, setValue] = useState("");
  return (
    <SearchField
      label="搜索阅读记录"
      placeholder="搜索标题或原文片段"
      value={value}
      onValueChange={setValue}
      summary="显示 7 / 12 条"
    />
  );
};
