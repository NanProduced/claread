import type { Meta } from "@ladle/react"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "."

const triggerClassName =
  "inline-flex min-h-10 items-center justify-center rounded-[var(--cl-radius-control-md)] border border-hairline bg-ink px-4 text-sm font-semibold text-white"

export default {
  title: "Primitives/Dialog",
} satisfies Meta

export const Default = () => (
  <Dialog>
    <DialogTrigger className={triggerClassName}>打开对话框</DialogTrigger>
    <DialogContent>
      <DialogHeader>
        <DialogTitle>删除阅读记录</DialogTitle>
        <DialogDescription>记录会进入软删除状态。七天内仍可恢复。</DialogDescription>
      </DialogHeader>
      <DialogFooter>
        <button className={triggerClassName}>确认删除</button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
)

export const Danger = () => (
  <Dialog defaultOpen>
    <DialogContent variant="danger" size="sm">
      <DialogHeader>
        <DialogTitle>清空会话</DialogTitle>
        <DialogDescription>这会移除当前上下文中的临时内容，无法撤销。</DialogDescription>
      </DialogHeader>
      <DialogFooter>
        <button className={triggerClassName}>继续</button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
)
