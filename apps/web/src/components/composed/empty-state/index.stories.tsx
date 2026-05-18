import type { Meta } from "@ladle/react";
import { FileText } from "lucide-react";
import { Button } from "@/components/primitives/button";
import { EmptyState } from ".";

export default {
  title: "Composed/EmptyState",
} satisfies Meta;

export const Default = () => (
  <EmptyState
    icon={FileText}
    title="还没有阅读记录"
    description="完成一次真实解析后，这里会成为你的英文阅读档案。"
    action={<Button variant="outline">解析新文章</Button>}
  />
);
