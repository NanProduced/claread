import type { Meta } from "@ladle/react"
import { ScrollArea } from "."

export default {
  title: "Primitives/ScrollArea",
} satisfies Meta

export const Default = () => (
  <ScrollArea className="h-56 w-72 rounded-[var(--cl-radius-surface-sm)] border border-hairline bg-surface p-4">
    <div className="space-y-3">
      {Array.from({ length: 12 }).map((_, index) => (
        <div key={index} className="rounded-[var(--cl-radius-control-sm)] border border-hairline bg-reader-paper p-3 text-sm text-muted">
          历史条目 {index + 1}
        </div>
      ))}
    </div>
  </ScrollArea>
)
