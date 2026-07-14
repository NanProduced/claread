# IconButton

Claread Web 的图标按钮入口，用于列表尾部动作、收藏、删除、跳转和工具触发器。

- 场景：删除记录、跳回原文、收藏、更多操作
- 变体：`outline` `quiet` `danger`
- 尺寸：`sm` `md` `lg`
- a11y：必须传入 `aria-label`
- 底层：Claread 自写样式基元

## Token 消费（语义层）

> 语义层标识来自 `packages/design-tokens/src/web/tokens.css` 与
> `apps/web/src/app/globals.css` 的 `@theme` 别名。`focus-ring` 由
> `primitiveFocusRing` 统一消费；危险变体的基色可下一步通过
> `feedback-error` 收敛。
