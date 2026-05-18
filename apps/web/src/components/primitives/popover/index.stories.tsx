import type { Meta } from "@ladle/react"
import { Popover, PopoverContent, PopoverTrigger } from "."

export default {
  title: "Primitives/Popover",
} satisfies Meta

export const Default = () => (
  <Popover>
    <PopoverTrigger className="inline-flex min-h-10 items-center justify-center rounded-[var(--cl-radius-control-md)] border border-hairline bg-reader-paper px-4 text-sm font-semibold text-ink">
      打开浮层
    </PopoverTrigger>
    <PopoverContent>
      <div className="space-y-2">
        <h3 className="text-sm font-semibold text-ink">阅读偏好</h3>
        <p className="text-sm leading-6 text-muted">这里展示轻量设置，不进入阻断式弹窗。</p>
      </div>
    </PopoverContent>
  </Popover>
)
