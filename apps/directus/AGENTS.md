# Directus Agent 指令

`apps/directus/` 用于 Claread Console 的本地 Directus 部署和扩展开发。

## 边界

- Directus 是控制面，不是 Claread 业务核心。
- 业务核心表默认只读；需要业务动作时优先走 Claread API / worker。
- Bootstrap 阶段只允许做 runtime、扩展壳子和本地开发体验，不做业务 schema 和执行逻辑。
- 不在 Directus hook / endpoint 中塞 judge、workflow replay、向量入库等重逻辑。

## Serena

- 在 `apps/directus/` 内做代码任务时，优先使用 Serena 做符号级阅读、声明跳转、引用分析和重构，不要先整文件通读。
- 需要 Serena 做符号级检索、引用分析或重构时，只激活当前子项目 `claread-directus`。
- 已知目标文件时，优先 `get_symbols_overview` -> `find_declaration` / `find_referencing_symbols` / `find_implementations`；需要修改整个符号时，优先 `rename_symbol`、`replace_symbol_body`、`safe_delete_symbol`。
- 目标落点不明确时，先用 Serena `search_for_pattern` 在本项目内粗搜；只有跨子项目排查、非代码文件、或 Serena 当前不支持的文件类型，才优先用 RTK / shell / git diff。
- 涉及 Directus 与 sibling packages 的 TypeScript 跨包引用时，优先考虑 Serena `additional_workspace_folders` 配置，而不是把仓库根目录整体激活成单一 Serena 项目。
- 不要从本目录向上激活仓库根目录为 Serena 项目；跨子项目检索优先用 RTK / shell / git diff。
- 未经用户明确要求，不写 Serena memory；长期事实应更新正式文档或本 `AGENTS.md`。如果 memory 与代码、测试或正式文档冲突，以后者为准，并删除或覆盖过期 memory。

## 目录约束

- `extensions/modules-bundle/`：Claread Console 自定义 module。
- `extensions/panels-bundle/`：Directus dashboard panel。
- `extensions/endpoints-bundle/`：轻量 API bridge 或健康检查。
- `.runtime/`：本地运行目录，不提交。

## 开发约束

- 优先保持 `watch + auto reload` 闭环，不依赖反复重建容器。
- 占位扩展只做壳子，不写业务数据。
- 任何对业务表的真实读写规则，在正式模块开发前单独评审。
