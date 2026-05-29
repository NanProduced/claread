# Workflow 架构

本文记录 Claread 当前分析 workflow 的稳定事实。旧仓库中的 v0/v1/v2 是历史方案，新仓库以 v3 思路为当前基线。

## 当前原则

- 输入预处理、语义分析、输出组织、渲染投影分层。
- 后端生成 canonical result，并把 `analysis_results.render_scene_json` 作为全量结果快照真相源持久化。
- 客户端阅读页消费专用 `reader scene view`，不再长期绑定 `/records` 的全量 `render_scene_json` 契约。
- workflow 运行完成后会额外写入 `analysis_debug_snapshots`，保存运行时调试摘要，而不是继续把 debug 大字段堆进 render scene。
- 小程序当前使用降级后的 render scene；Web 后续可以生成更丰富的 render profile。
- `schema_version` 和 `workflow_version` 必须保留，便于回看、回归和 eval。

## 主要链路

1. 接收文本、阅读目标和用户配置。
2. 预处理输入，包括语言检测、句切分和快速退出。
3. 根据 reading goal 选择差异化策略。
4. 执行分析 workflow。
5. 生成可保存的分析记录与结果快照。
6. 写入运行时调试摘要与使用量审计。
7. 为目标客户端组装 `reader scene view`。
8. 用户资产与记录、词典、反馈、批注建立关联。

## 当前结果分层

当前稳定事实可理解为四层：

```text
canonical analysis result
  -> persisted render scene snapshot
  -> reader scene view
  -> client local UI state
```

- `persisted render scene snapshot`
  - 指 `analysis_results.render_scene_json`
  - 保留全量结果快照，服务 Directus observability、Inspector 和后续 compare/debug
- `reader scene view`
  - 指专门给 Web / 小程序阅读页消费的精简视图层
  - 当前后端已有独立 reader API，不再要求阅读页直接吃 `/records` 的全量快照
- `client local UI state`
  - 指阅读器展开态、滚动位置、临时交互状态等
  - 不进入后端 canonical truth

## Reading Goal

当前主线包含：

| Goal | 用途 |
|------|------|
| `daily_reading` | 日常阅读理解 |
| `exam` | 考试阅读场景 |
| `academic` | 学术阅读场景 |

后续新增 goal 或 variant 时，必须同步 API schema、数据库记录和前端配置。

## 当前已知限制

- 小程序 render scene 不是 Web 端体验上限。
- Web 端更丰富的 render profile 尚未定义。
- 后续 eval 与 Inspector 仍需要更深层的 debug truth，例如 raw drop 明细、draft validation 结构化摘要和更完整的 trace refs。
- 后续质量回看需要基于 workflow version、prompt version 与 `analysis_debug_snapshots` 追踪输出质量。
