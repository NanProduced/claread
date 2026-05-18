# Ask Claread Compatibility Layer

本目录只为 Ask Claread 及其相关 UI 保留，不是 Claread Web 新组件库入口。

- 允许：`components/ui/*` 自身互相引用，以及 Ask Claread 相关文件引用
- 禁止：普通功能页、`components/composed`、`components/layout`、新的 `components/primitives` 继续从这里取组件
- 迁移策略：可共享能力进入 `components/primitives`，本目录只保留 Ask 兼容实现
