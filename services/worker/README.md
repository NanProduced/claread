# Claread Worker

`services/worker/` 是后续后台异步任务服务的预留目录。

当前不用急着实现独立 worker。现阶段分析任务 worker 仍在 `services/api/` 内运行，便于保持小程序和后端基线稳定。

## 未来职责

当后台任务变重时，可以逐步把以下能力拆到独立 worker：

- 分析任务队列消费。
- Daily Reader 抓取和生成。
- Web / 小程序不同 render snapshot 生成。
- LLM-as-a-Judge 评测运行。
- Directus 触发的后台 action。
- RAG ingestion。
- 审核样本写入 Zilliz。
- 批量重跑 prompt / model eval。
- 长耗时数据清洗。

## 与 API 的关系

`services/api/` 负责 HTTP API 和用户请求入口。

`services/worker/` 负责后台执行，不直接承载用户请求。

两者共享：

- PostgreSQL。
- Redis / queue。
- prompt 和模型路由配置。
- contracts。
- 必要的业务 service 代码。

## 拆分原则

只有当后台任务影响 API 响应、部署弹性或运行稳定性时，才拆出独立 worker。

拆分前应先明确：

- 任务队列机制。
- 幂等策略。
- 失败重试和死信处理。
- worker heartbeat。
- 与 `analysis_tasks` 状态机的关系。
- 日志、trace 和 usage 记录。

当前阶段不要为了目录存在而创建空服务框架。
