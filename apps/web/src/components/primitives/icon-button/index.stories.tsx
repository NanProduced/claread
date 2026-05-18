import type { Meta } from "@ladle/react";
import { ArrowRight, Heart, Trash2 } from "lucide-react";
import { IconButton } from ".";

export default {
  title: "Primitives/IconButton",
} satisfies Meta;

export const Default = () => (
  <div className="flex gap-3">
    <IconButton aria-label="回到原文">
      <ArrowRight className="h-4 w-4" />
    </IconButton>
    <IconButton aria-label="收藏" variant="quiet">
      <Heart className="h-4 w-4" />
    </IconButton>
    <IconButton aria-label="删除" variant="danger">
      <Trash2 className="h-4 w-4" />
    </IconButton>
  </div>
);
