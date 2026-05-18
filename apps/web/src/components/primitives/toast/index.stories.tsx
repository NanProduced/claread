import type { Meta } from "@ladle/react"
import { toast } from "sonner"
import { ClareadToaster } from "."

export default {
  title: "Primitives/Toast",
} satisfies Meta

export const Default = () => (
  <div className="space-x-3">
    <ClareadToaster />
    <button
      className="inline-flex min-h-10 items-center justify-center rounded-[var(--cl-radius-control-md)] border border-hairline bg-reader-paper px-4 text-sm font-semibold text-ink"
      onClick={() => toast.success("已保存到生词本", { description: "后续会用于轻量复习。" })}
    >
      Success Toast
    </button>
    <button
      className="inline-flex min-h-10 items-center justify-center rounded-[var(--cl-radius-control-md)] border border-hairline bg-reader-paper px-4 text-sm font-semibold text-ink"
      onClick={() => toast.error("同步失败", { description: "请稍后重试。" })}
    >
      Error Toast
    </button>
  </div>
)
