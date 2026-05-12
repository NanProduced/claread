# Workflow 架构

本文记录 Claread 当前分析 workflow 的稳定事实。旧仓库中的 v0/v1/v2 是历史方案，新仓库以 v3 思路为当前基线。

## 当前原则

- 输入预处理、语义分析、输出组织、渲染投影分层。
- 后端生成 canonical result，客户端按自身能力渲染。
- 小程序当前使用降级后的 render scene；Web 后续可以生成更丰富的 render profile。
- `schema_version` 和 `workflow_version` 必须保留，便于回看、回归和 eval。

## 主要链路

1. 接收文本、阅读目标和用户配置。
2. 预处理输入，包括语言检测、句切分和快速退出。
3. 根据 reading goal 选择差异化策略。
4. 执行分析 workflow。
5. 生成可保存的分析记录。
6. 为目标客户端生成 render snapshot。
7. 用户资产与记录、词典、反馈、批注建立关联。

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
- 后续 eval 需要基于 workflow version 和 prompt version 追踪输出质量。
