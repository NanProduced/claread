# Ask Claread V1

`Ask Claread` 是 Claread 解析页内的 grounded AI chatbox。它不是独立 AI 页面，也不是通用聊天产品；它的目标是在 Reader 里围绕当前文章、当前选区和按需获取的用户资产，提供可回源、可保存、受控计费的多轮解释能力。

## 产品定义

- 运行位置：解析页内右侧 AI 工作区。
- 线程边界：按文章绑定；每篇文章 1 个默认线程，可创建 `New chat`。
- 会话能力：同一线程允许挂多个 anchor，并在当前文章内做多轮追问。
- 默认输出：流式 Markdown。
- 上下文策略：默认只用当前文章上下文；只有在用户明确表达“以前/之前/我记过/在哪见过”等历史意图时，才按需扩展到跨文章资产检索。
- 写操作策略：所有非只读操作都必须经过 human-in-the-loop 确认。

## V1 目标

V1 解决的是“阅读阻塞点内的 AI 解释与追问”，不是“全量学习资产总结器”。

用户可以：

- 从正文选区快捷入口发起对话。
- 直接在右侧 chatbox 输入问题。
- 把多个 anchor 挂入同一线程继续追问。
- 获得带引用来源的回答，并在需要时把结果保存为笔记、摘录或收藏。

系统必须做到：

- 明确知道当前线程对应哪篇文章。
- 对“这里/这句/上一段”之类指代先走会话层锚点解析，再交给模型。
- 对跨文章资产访问做意图触发和来源显式展示。
- 接入统一 AI usage 审计、积分预留和退款闭环。

## Anchor 与上下文

V1 的 canonical anchor 类型包括：

- `sentence`
- `text_range`
- `multi_text`
- `sentence_entry`
- `user_annotation`
- `favorite`
- `dictionary_entry`

上下文解析顺序：

1. 最近一次显式加入本轮消息的 anchor。
2. 当前 Reader focus 的句子或选区。
3. 最近一次 assistant 回答中引用过的正文位置。
4. 仍不明确时，返回澄清而不是让模型猜测。

默认上下文包包括：

- 当前线程 `record_id`
- 当前消息 anchor 集合
- 当前锚点附近的最小阅读窗口
- 当前文章内相关解析内容与摘录资产

不做：

- 默认自动注入用户全历史学习数据
- 脱离当前文章的长期人格化记忆
- 独立的全局 AI 工作区

## 会话与动作模型

- 每篇文章默认有一个主线程；用户可创建额外 `New chat`。
- 同一线程内的消息可挂多个 anchor。
- assistant 回答默认输出 Markdown。
- 需要特殊 UI 时，后续通过结构化 action / render contract 扩展，不要求把普通回答改成结构化 JSON。

V1 写操作：

- 保存为笔记
- 保存为高亮/摘录
- 收藏当前 anchor
- 保存 assistant 回答为笔记

以上动作都必须经过确认；只读查询不需要确认。

## 后端能力边界

Ask Claread 当前作为后端优先能力推进，Reader Web 只是它的第一个正式客户端。

V1 后端需要提供：

- 线程列表、创建与详情接口
- SSE 流式回复接口
- 动作确认接口
- 文章内上下文、摘录资产、词典与按需历史资产检索能力
- 统一的审计、计费、额度预留和退款

当前实现说明：

- `reader_ask` 已切到真正的模型侧 tool-call runtime，不再由服务层把所有上下文预取后直接拼 prompt。
- service 仍负责线程/消息持久化、anchor 解析、积分预留/退款、usage 审计和 SSE 编排。
- 词典与 `dict_ai context_explain` 已纳入同一 Ask runtime；其 usage 会并入 Ask 的聚合 usage summary。

## UI 合同

Web 后续应按以下合同对接：

- 在 `AiWorkspacePanel` 内渲染 article-bound thread。
- 支持选区快捷 `Add to chat` 与直接输入两种入口。
- 在消息上方展示 context chips，允许移除。
- assistant 消息渲染 Markdown。
- 回答中显示 citation / source article label。
- 对写操作渲染 confirm card。
- 通过 Next.js BFF 代理消费 FastAPI SSE。

当前实现说明：

- Web 已通过 `/api/web/reader-ask/*` BFF 路由代理 Ask 线程、SSE 和动作确认。
- `AiWorkspacePanel` 已改成隐式恢复当前文章会话：单线程时不再暴露“默认线程”概念，只有存在多线程后才提供轻量 thread switch。
- draft context 不再作为独立首屏模块；只有用户实际附加选区/卡片后，才在输入框上方显示 context chips。
- Web 已实际接入 Prompt Kit 的基础 primitives：`ChatContainer`、`Message`、`PromptInput`、`Markdown`、`Tool`，同时继续保留 Claread 自己的 citation / confirm card / context chips / SSE transport。
- `SelectionToolbar` 与句子上下文面板都已接入 Ask，支持把当前选区或当前句子挂入对话上下文。
- 开发环境下，Ask 的流式错误会透传后端 `code/detail`，便于联调；生产环境仍保持友好错误文案。

## 实现状态

当前已进入后端优先实现阶段，相关进度和待办只记录在 `docs/product/tmp/ask-claread-v1-tracker.md`。该 `TMP` 文档在功能开发完成且验证通过后删除，稳定结论回收至本文和相关正式文档。
