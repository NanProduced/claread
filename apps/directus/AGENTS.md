# Directus Agent 指令

`apps/directus/` 用于 Claread Console 的本地 Directus 部署和扩展开发。

## 边界

- Directus 是控制面，不是 Claread 业务核心。
- 业务核心表默认只读；需要业务动作时优先走 Claread API / worker。
- Bootstrap 阶段只允许做 runtime、扩展壳子和本地开发体验，不做业务 schema 和执行逻辑。
- 不在 Directus hook / endpoint 中塞 judge、workflow replay、向量入库等重逻辑。

## Serena

- 需要 Serena 做符号级检索、引用分析或重构时，只激活当前子项目 `claread-directus`。
- 不要从本目录向上激活仓库根目录为 Serena 项目；跨子项目检索优先用 RTK / shell / git diff。

## 目录约束

- `extensions/modules-bundle/`：Claread Console 自定义 module。
- `extensions/panels-bundle/`：Directus dashboard panel。
- `extensions/endpoints-bundle/`：轻量 API bridge 或健康检查。
- `.runtime/`：本地运行目录，不提交。

## 开发约束

- 优先保持 `watch + auto reload` 闭环，不依赖反复重建容器。
- 占位扩展只做壳子，不写业务数据。
- 任何对业务表的真实读写规则，在正式模块开发前单独评审。
