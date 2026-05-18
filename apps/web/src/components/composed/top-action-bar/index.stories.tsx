import type { Meta } from "@ladle/react";
import { Button } from "@/components/primitives/button";
import { TopActionBar } from ".";

export default {
  title: "Composed/TopActionBar",
} satisfies Meta;

export const Default = () => (
  <TopActionBar>
    <Button variant="primary">新解读</Button>
    <Button variant="outline">学习资产</Button>
  </TopActionBar>
);
