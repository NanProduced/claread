# AI 使用审计与结算

本文记录 Claread 平台当前用于承接多端 AI 能力的底座约束。

## 目标

- 把 AI 调用审计和用户积分结算拆开。
- 让 Web、小程序、Daily Reader 和后续词典 AI 能复用同一套 usage 语义。
- 保留后续扩展空间，不把 capability 写死成数据库枚举。

## 两层职责

| 层 | 主要表 / 入口 | 作用 |
|------|------|------|
| 审计层 | `ai_usage_events` | 记录一次 AI 调用发生了什么，包括作用域、能力代码、usage、模型信息、状态和关联对象 |
| 结算层 | `user_credit_accounts` / `user_credit_ledger` | 只负责用户余额与积分变动 |

统一 AI 审计以 `ai_usage_events` 为准；旧 `analysis_audit_logs` 表已随旧分析链退出，不再是审计事实源。

## Usage Scope

`usage_scope` 当前规范为：

| scope | 含义 |
|------|------|
| `user_billed` | 面向用户、参与积分结算的 AI 调用 |
| `system_internal` | 平台内部行为，不影响用户额度 |
| `anonymous_trial` | 匿名试用调用 |
| `eval_debug` | 本地调试、评测或显式 runtime model selection 调用 |

后续如果需要新增 scope，应先更新后端常量、migration 和文档，再接业务能力。

## Billing Mode

`billing_mode` 当前规范为：

| mode | 含义 |
|------|------|
| `user_points` | 记入用户积分体系 |
| `internal_only` | 只审计，不结算到用户 |
| `trial` | 试用路径，无用户积分扣减 |
| `no_charge` | 调试/评测路径，无用户积分扣减 |

## Capability Code

`capability_code` 使用开放文本，不做数据库枚举锁死。当前已规范的代码包括：

- `analysis_full`
- `dict_ai_lookup`
- `reader_ask`
- `reader_translation` / `reader_vocabulary` / `reader_grammar_bundle` / `reader_title_generation` / `reader_semantic_outline`
- `grammar_xray`
- `artifact_summary`
- `analysis_overview_hint`
- `daily_reader_pipeline`
- `daily_reader_scoring`
- `rag_embedding` / `rag_rerank`

新增能力接入前，应先确定 capability code，再决定 scope 和 billing mode。

## 当前接入点

| 调用链路 | scope | billing_mode | capability_code | 说明 |
|------|------|------|------|------|
| Reader orchestration worker（translation / vocabulary / grammar_bundle / display_title / semantic_outline / pipeline_runner） | `system_internal` | `internal_only` | `reader_translation` 等 reader_* 代码 | 增强层生成的统一 usage 审计，关联 reading_record / run / job / layer |
| `POST /dict/ai` | `user_billed` | `user_points` | `dict_ai_lookup` | 登录用户的词典 AI 能力；支持 `context_explain` 与 `missing_fallback` |
| `POST /reader/records/{reading_record_id}/ask/threads/{thread_id}/messages/stream` | `user_billed` | `user_points` | `reader_ask` | Reader 内 Ask Claread 流式对话；当前文章为默认上下文 |
| Daily Reader scoring | `system_internal` | `internal_only` | `daily_reader_scoring` | 候选文章 LLM 评分 |
| Daily Reader workflow / retry | `system_internal` | `internal_only` | `daily_reader_pipeline` | 精读正文生成与重跑 |

Ask 链路的 usage/ledger 闭环（turn run `usage_summary_json` / `usage_event_id` 落账）是已预留字段，实际写账接入属于 post-cutover backlog。

## 计费策略现状

当前已接入用户积分策略的 capability 包括：

- `dict_ai_lookup`
  - policy: `dict_ai_fixed_points_v1`
  - 固定价格: 每次 `5` 点
  - 真实 token usage 仍写入 `ai_usage_events` 与 billing metadata，仅用于审计和后续定价回看
- `reader_ask`
  - policy: `analysis_weighted_tokens_v1`
  - 计费配置按 Ask model option 挂载（`price_multiplier` 来自 `reader-ask-model-options.json`）
  - 公式: `ceil((input_tokens * 1 + output_tokens * 5) / 1000)`
  - turn run 已预留 `usage_summary_json` / `usage_event_id` 字段；实际预扣/结算写账接入属于 post-cutover backlog

`analysis_full` 与 `analysis_weighted_tokens_v1` 的加权公式保留为通用计费策略；Reader orchestration worker 当前按 `system_internal` / `internal_only` 只审计不结算，用户侧计费口径待统一监测与计费适配收口。

该策略已经从任务执行器中抽离到统一的 `app/services/ai_usage/billing.py`，后续 Ask Claread、Grammar X-Ray 等能力应按 capability 独立扩展。

## 下一步建议

- 为用户设置页和运营后台补 usage 查询接口。
- 在长任务场景上补充预估 / 预扣 / 结算闭环。
