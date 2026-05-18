import type { Meta } from "@ladle/react"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger } from "."

export default {
  title: "Primitives/Sheet",
} satisfies Meta

export const RightPanel = () => (
  <Sheet>
    <SheetTrigger className="inline-flex min-h-10 items-center justify-center rounded-[var(--cl-radius-control-md)] border border-hairline bg-reader-paper px-4 text-sm font-semibold text-ink">
      打开面板
    </SheetTrigger>
    <SheetContent side="right">
      <SheetHeader>
        <SheetTitle>阅读设置</SheetTitle>
        <SheetDescription>适合桌面侧边工具层，不使用 SaaS 风格抽屉。</SheetDescription>
      </SheetHeader>
    </SheetContent>
  </Sheet>
)

export const LeftPanel = () => (
  <Sheet defaultOpen>
    <SheetContent side="left">
      <SheetHeader>
        <SheetTitle>词典详情</SheetTitle>
        <SheetDescription>左侧是详情层，不是应用侧栏。</SheetDescription>
      </SheetHeader>
    </SheetContent>
  </Sheet>
)
