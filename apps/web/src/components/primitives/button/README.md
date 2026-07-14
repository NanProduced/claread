# Button

Claread Web 的通用按钮入口，统一功能页中的主操作、次操作和危险操作。

- 场景：页头 CTA、列表行操作、确认与退出、筛选切换中的按钮形态
- 变体：`primary` `secondary` `outline` `quiet` `danger` `ghost`
- 尺寸：`sm` `md` `lg`
- a11y：保留原生 button 语义，disabled 状态使用原生 `disabled`
- 底层：Claread 自写样式基元，不依赖 `components/ui/button`

## Token 消费（语义层）

> 语义层标识来自 `packages/design-tokens/src/web/tokens.css` 与
> `apps/web/src/app/globals.css` 的 `@theme` 别名。下表仅记录本 primitive
> 当前已经能够消费（或下一轮可直接改写为消费）的语义 token。

| 角色 | 当前实现 | 语义 token | 备注 |
| --- | --- | --- | --- |
| CTA 填充 | `--app-primary-gradient` | `action-primary` + `action-primary-foreground` | 视觉层是渐变；语义层提供基色 |
| focus 环 | `primitiveFocusRing` → `focus-ring` | `focus-ring` | shared.ts 已消费 |
| 文本色（outline/quiet） | `--ink` / `--ink-soft` | `text-primary` | 后续收敛可平替 |
| 危险 | `--app-danger-gradient` | `feedback-error` + `feedback-error-foreground` | 视觉层是渐变；语义层提供基色 |
| hairline | `--hairline` / `--app-hairline` | `border-subtle` | 后续收敛可平替 |
