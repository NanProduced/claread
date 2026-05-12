# 后续主线流程

本文给新会话 agent 和开发者说明 Claread 当前基线之后的推进顺序。

## 总原则

先稳定已有小程序和通用后端，再扩展 Web、Directus、评测和 RAG。

不要把后续工作理解为“重做小程序”。当前小程序是第一个客户端和可运行基线；后续新增能力应服务 Claread 多端产品。

## 阶段 1：稳定当前基线

目标：确保微信小程序和通用后端在新仓库内稳定运行。

主要事项：

- 后端核心测试保持通过。
- 微信小程序构建和 TypeScript 检查保持通过。
- 微信开发者工具 smoke test 覆盖登录、解析、结果页、词典、生词本、历史、每日精读和反馈。
- 模型 profile 切换后必须重新验证结构化输出是否包含词汇、语法、句式和翻译。
- 本地 PostgreSQL/Redis、词典三表和 Docker 命名保持 Claread 规范。

完成标准：

- 新仓库可独立启动后端和小程序。
- 解析链路可产生可渲染结果。
- 主链路问题不再需要回旧仓库排查。

## 阶段 2：收紧 API 契约

目标：让后端作为多端服务而不是小程序专属后端。

主要事项：

- 补齐关键接口 `response_model`。
- 明确 `client_record_id`、`cloud_record_id`、`task_id` 语义。
- 收紧 `source_type`、records、daily reader 和 user assets 相关枚举。
- 为 `packages/contracts/` 准备 OpenAPI / DTO 生成策略。
- 保持小程序兼容，不破坏当前客户端依赖字段。

完成标准：

- 新增 Web 端时不需要猜后端响应结构。
- 小程序端不再承担隐式契约说明职责。

## 阶段 3：Web 端启动

目标：建设 Claread 高保真阅读体验。

Web 端不是小程序页面的复刻。它应共享后端和数据，但可以拥有更强的阅读、批注、排版、快捷键、布局和管理能力。

主要事项：

- 选择 Web 技术栈并建立 `apps/web/` 基础项目。
- 定义 Web reader 的 MVP 范围。
- 设计 Web auth adapter。
- 定义 Web render profile，不把小程序降级表现当作 Web 上限。
- 建立浏览器 smoke test 和截图验证。

完成标准：

- Web 可登录或进入可控调试态。
- Web 可读取/创建分析记录并展示结果。
- Web reader 有独立体验方向，而不是小程序样式拉伸。

## 阶段 4：Directus 与内部工具

目标：用 Directus 承载内部数据可视化、运营、样本审核和部分 workflow 管理。

Directus 是内部管理面板和扩展平台，不是业务后端替代品。业务事实仍在 Claread 后端和 PostgreSQL 中。

主要事项：

- 建立 `apps/directus/` 本地部署配置。
- 明确 Directus 访问哪些表、哪些只读、哪些可写。
- 设计样本集、审核状态、rubric、judge run 的数据模型。
- 规划 Directus extension / operation 与后端 worker 的边界。

完成标准：

- 可用面板查看核心业务数据。
- 可维护评测样本和人工审核状态。
- 不绕过后端写入关键业务状态。

## 阶段 5：LLM-as-a-Judge 与 Few-Shot RAG

目标：把解析质量验证、样本沉淀和 few-shot 检索形成闭环。

主要事项：

- 在 `evals/` 下定义 dataset、rubric、run 记录格式。
- 使用 LangSmith trace 观察单次运行过程。
- 使用 Directus 管理样本、人工标注和审核状态。
- 将高质量审核样本进入 Zilliz，服务 Grammar RAG 和后续 few-shot。
- 明确 judge 输出如何回写、如何对比版本、如何避免污染生产数据。

完成标准：

- 可以对同一批样本比较不同 prompt/model/profile 的效果。
- few-shot 示例来自审核过的样本，不再依赖临时脚本堆积。

## 新会话 Agent 工作流

新会话开始时建议按顺序阅读：

1. `AGENTS.md`
2. `README.md`
3. `docs/README.md`
4. `docs/product/current-state.md`
5. 本文档
6. 目标目录最近的 `AGENTS.md`

执行任务前先判断任务属于哪个阶段。不要因为看到旧的小程序实现，就默认 Claread 只是微信小程序项目。

## 暂不做

- 不清理旧 Docker volume，直到新仓库稳定运行一段时间。
- 不把旧脚本式 regression suite 迁入新仓库主线。
- 不为 Web 复制一套业务后端。
- 不在全局文档里写小程序平台专属限制。
- 不把真实模型 key、微信 secret、Zilliz token 或本地 `.env` 提交到仓库。
