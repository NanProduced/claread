import type { Meta } from "@ladle/react"
import {
  CommandMenuDialog,
  CommandMenuEmpty,
  CommandMenuGroup,
  CommandMenuInput,
  CommandMenuItem,
  CommandMenuList,
  CommandMenuSeparator,
  CommandMenuShortcut,
} from "."

export default {
  title: "Primitives/CommandMenu",
} satisfies Meta

export const WithResults = () => (
  <CommandMenuDialog defaultOpen>
    <CommandMenuInput placeholder="搜索文章、页面或命令" />
    <CommandMenuList>
      <CommandMenuEmpty>没有找到结果。</CommandMenuEmpty>
      <CommandMenuGroup heading="页面">
        <CommandMenuItem>
          新解读
          <CommandMenuShortcut>G R</CommandMenuShortcut>
        </CommandMenuItem>
        <CommandMenuItem>
          阅读记录
          <CommandMenuShortcut>G L</CommandMenuShortcut>
        </CommandMenuItem>
      </CommandMenuGroup>
      <CommandMenuSeparator />
      <CommandMenuGroup heading="动作">
        <CommandMenuItem>继续上次阅读</CommandMenuItem>
      </CommandMenuGroup>
    </CommandMenuList>
  </CommandMenuDialog>
)
