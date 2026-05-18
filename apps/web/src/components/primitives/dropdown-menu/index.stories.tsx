import type { Meta } from "@ladle/react"
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from "."

export default {
  title: "Primitives/DropdownMenu",
} satisfies Meta

export const Default = () => (
  <DropdownMenu>
    <DropdownMenuTrigger className="inline-flex min-h-10 items-center justify-center rounded-[var(--cl-radius-control-md)] border border-hairline bg-reader-paper px-4 text-sm font-semibold text-ink">
      更多操作
    </DropdownMenuTrigger>
    <DropdownMenuContent>
      <DropdownMenuLabel>记录操作</DropdownMenuLabel>
      <DropdownMenuItem>继续阅读</DropdownMenuItem>
      <DropdownMenuItem>加入精选</DropdownMenuItem>
      <DropdownMenuSeparator />
      <DropdownMenuItem className="text-error-red">删除记录</DropdownMenuItem>
    </DropdownMenuContent>
  </DropdownMenu>
)
