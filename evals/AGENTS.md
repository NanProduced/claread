# Evals Agent 指令

`evals/` 是 Claread 的评测项目，用于 LLM-as-a-Judge、样本集、few-shot / RAG 评测流和后续评测自动化。

## 边界

- 评测代码服务于 Claread 通用后端和 Directus 控制面，不替代 `services/api/` 的业务逻辑。
- 评测样本、rubric、judge 输出和实验过程要能回溯到明确的数据版本、模型版本和 prompt 版本。
- 临时实验记录必须标注 `TMP`，优先放在 `evals/tmp/`，不要沉淀成长期事实来源。

## Serena

- 需要 Serena 做符号级检索、引用分析或重构时，只激活当前子项目 `claread-evals`。
- 不要从本目录向上激活仓库根目录为 Serena 项目；跨子项目检索优先用 RTK / shell / git diff。

## 验证

按任务范围优先跑对应评测或测试；没有稳定入口前，至少运行相关 Python 单测和静态检查，并在交付说明里写清未覆盖的评测缺口。
