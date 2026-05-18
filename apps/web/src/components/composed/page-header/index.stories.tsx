import type { Meta } from "@ladle/react";
import { Button } from "@/components/primitives/button";
import { PageHeader } from ".";

export default {
  title: "Composed/PageHeader",
} satisfies Meta;

export const Default = () => (
  <PageHeader
    eyebrow="阅读档案"
    title="阅读记录"
    description="回到读过的文章，继续阅读、找回收藏和批注。第一版先做标题与原文片段搜索。"
    actions={
      <>
        <Button variant="primary">新解读</Button>
        <Button variant="outline">学习资产</Button>
      </>
    }
  />
);
