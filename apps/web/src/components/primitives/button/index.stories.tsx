import type { Meta } from "@ladle/react";
import { ArrowRight, Trash2 } from "lucide-react";
import { Button } from ".";

export default {
  title: "Primitives/Button",
} satisfies Meta;

export const Variants = () => (
  <div className="flex flex-wrap gap-3">
    <Button variant="primary">开始透读</Button>
    <Button variant="secondary">回到原文</Button>
    <Button variant="outline">学习资产</Button>
    <Button variant="quiet">全部</Button>
    <Button variant="danger">退出登录</Button>
  </div>
);

export const WithIcon = () => (
  <div className="flex flex-wrap gap-3">
    <Button variant="primary">
      继续阅读
      <ArrowRight className="h-4 w-4" />
    </Button>
    <Button variant="danger" size="sm">
      <Trash2 className="h-4 w-4" />
      删除
    </Button>
  </div>
);
