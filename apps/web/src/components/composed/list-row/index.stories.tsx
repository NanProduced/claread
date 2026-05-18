import type { Meta } from "@ladle/react";
import { ArrowRight } from "lucide-react";
import { Button } from "@/components/primitives/button";
import { ListRow } from ".";

export default {
  title: "Composed/ListRow",
} satisfies Meta;

export const Default = () => (
  <div className="divide-y divide-hairline border-y border-hairline">
    <ListRow
      title="五角大楼解密 UFO 文件引发外星生命讨论"
      description="Speculation regarding the existence of extraterrestrial life is increasing following the release..."
      meta={
        <>
          <span>2026/5/15</span>
          <span>198 words</span>
          <span>0 标注</span>
        </>
      }
      trailing={
        <Button variant="outline" size="sm">
          继续阅读
          <ArrowRight className="h-3.5 w-3.5" />
        </Button>
      }
    />
  </div>
);
