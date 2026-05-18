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

现阶段 `analysis_audit_logs` 继续保留作兼容和排障，但新的统一 AI 审计以 `ai_usage_events` 为准。

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
- `grammar_xray`
- `artifact_summary`
- `daily_reader_pipeline`
- `daily_reader_scoring`

新增能力接入前，应先确定 capability code，再决定 scope 和 billing mode。

## 当前接入点

| 调用链路 | scope | billing_mode | capability_code | 说明 |
|------|------|------|------|------|
| `POST /analysis-tasks` worker 执行 | `user_billed` | `user_points` | `analysis_full` | 登录用户正式分析主链路 |
| `POST /analyze` | `anonymous_trial` / `eval_debug` | `trial` / `no_charge` | `analysis_full` | 兼容匿名试用与调试直连，不应扩展成新能力总入口 |
| `POST /dict/ai` | `user_billed` | `user_points` | `dict_ai_lookup` | 登录用户的词典 AI 能力；支持 `context_explain` 与 `missing_fallback` |
| `POST /reader-ask/threads/{thread_id}/messages/stream` | `user_billed` | `user_points` | `reader_ask` | Reader 内 Ask Claread 流式对话；当前文章为默认上下文，按需扩展到历史资产 |
| Daily Reader scoring | `system_internal` | `internal_only` | `daily_reader_scoring` | 候选文章 LLM 评分 |
| Daily Reader workflow / retry | `system_internal` | `internal_only` | `daily_reader_pipeline` | 精读正文生成与重跑 |

## `/analyze` 的定位

`/analyze` 当前保留给两类场景：

- 匿名试用直连分析
- 本地调试、评测和 runtime `model_selection` 验证

它不走 `analysis-tasks` 的任务状态、记录落库包装和用户积分结算主链路。后续正式用户侧 AI 能力不应继续叠加在 `/analyze` 上，而应走新的能力入口或受控任务链路。

## 计费策略现状

当前已接入用户积分策略的 capability 包括：

- `analysis_full`
  - policy: `analysis_weighted_tokens_v1`
  - 公式: `ceil((input_tokens * 1 + output_tokens * 5) / 1000)`
- `dict_ai_lookup`
  - policy: `dict_ai_fixed_points_v1`
  - 固定价格: 每次 `5` 点
  - 真实 token usage 仍写入 `ai_usage_events` 与 billing metadata，仅用于审计和后续定价回看
- `reader_ask`
  - policy: `analysis_weighted_tokens_v1`
  - 预留: 每轮默认先预扣 `10` 点，完成后按 token 加权结果结算并退回未使用部分
  - 公式: `ceil((input_tokens * 1 + output_tokens * 5) / 1000)`
  - metadata 需补线程 ID、record ID、anchor 摘要、是否触发历史资产检索与工具轨迹摘要
  - Ask 内部如果调用了 `dict_ai context_explain`，其 usage 也并入 Ask 的聚合 usage summary，不再额外暴露成用户侧独立扣点动作
  - 实现上应同时收紧 prompt 和 output 预算，避免只在结算阶段做超额截断

该策略已经从任务执行器中抽离到统一的 `app/services/ai_usage/billing.py`，后续 Ask Claread、Grammar X-Ray 等能力应按 capability 独立扩展。

## 下一步建议

- 为用户设置页和运营后台补 usage 查询接口。
- 在长任务场景上补充预估 / 预扣 / 结算闭环。
