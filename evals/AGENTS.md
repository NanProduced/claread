# Evals Agent 指令

`evals/` 是 Claread 的评测项目，用于 LLM-as-a-Judge、样本集、few-shot / RAG 评测流和后续评测自动化。

## 边界

- 评测代码服务于 Claread 通用后端和 Directus 控制面，不替代 `services/api/` 的业务逻辑。
- 评测样本、rubric、judge 输出和实验过程要能回溯到明确的数据版本、模型版本和 prompt 版本。
- 临时实验记录必须标注 `TMP`，优先放在 `evals/tmp/`，不要沉淀成长期事实来源。

## Serena

- 在 `evals/` 内做代码任务时，优先使用 Serena 做符号级阅读、声明跳转、引用分析和重构，不要先整文件通读。
- 需要 Serena 做符号级检索、引用分析或重构时，只激活当前子项目 `claread-evals`。
- 已知目标文件时，优先 `get_symbols_overview` -> `find_declaration` / `find_referencing_symbols` / `find_implementations`；需要修改整个符号时，优先 `rename_symbol`、`replace_symbol_body`、`safe_delete_symbol`。
- 目标落点不明确时，先用 Serena `search_for_pattern` 在本项目内粗搜；只有跨子项目排查、非代码文件、或 Serena 当前不支持的文件类型，才优先用 RTK / shell / git diff。
- 不要从本目录向上激活仓库根目录为 Serena 项目；跨子项目检索优先用 RTK / shell / git diff。
- 未经用户明确要求，不写 Serena memory；长期事实应更新正式文档或本 `AGENTS.md`。如果 memory 与代码、测试或正式文档冲突，以后者为准，并删除或覆盖过期 memory。

## 验证

按任务范围优先跑对应评测或测试；没有稳定入口前，至少运行相关 Python 单测和静态检查，并在交付说明里写清未覆盖的评测缺口。
