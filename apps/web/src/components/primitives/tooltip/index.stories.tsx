import type { Meta } from "@ladle/react"
import { Tooltip, TooltipContent, TooltipTrigger } from "."

export default {
  title: "Primitives/Tooltip",
} satisfies Meta

export const Default = () => (
  <Tooltip>
    <TooltipTrigger className="inline-flex min-h-10 items-center justify-center rounded-[var(--cl-radius-control-md)] border border-hairline bg-reader-paper px-4 text-sm font-semibold text-ink">
      悬停查看
    </TooltipTrigger>
    <TooltipContent>Claread 的 tooltip 保持低权重，不抢阅读注意力。</TooltipContent>
  </Tooltip>
)
