import type { Meta } from "@ladle/react";
import { Search } from "lucide-react";
import { InfoCard } from ".";

export default {
  title: "Composed/InfoCard",
} satisfies Meta;

export const Default = () => (
  <InfoCard
    title="搜索范围"
    icon={Search}
    description="当前只在已加载记录的标题和原文片段中查找。后续语义搜索归入后端能力评审。"
  />
);
