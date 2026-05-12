# Prompt 版本管理

Claread 当前阶段继续使用后端内置 prompt registry。后续如果改为数据库或 Directus 管理，也必须保留版本可追踪能力。

## 版本位置

当前版本号在：

```text
services/api/prompts/registry.yaml
```

历史路径不作为当前事实来源；新代码以 `services/api/prompts/registry.yaml` 为准。

## 版本语义

| 变更类型 | 递增方式 | 示例 |
|----------|----------|------|
| 措辞修正，输出行为不变 | patch | `0.0.1` -> `0.0.2` |
| 策略调整，输出行为有变化 | minor | `0.1.0` -> `0.2.0` |
| 架构重构或新增 agent | major | `1.0.0` -> `2.0.0` |

## 修改流程

1. 修改 `services/api/prompts/` 下对应 YAML。
2. 递增 `registry.yaml` 中的 `version`。
3. 用 debug 接口预览完整 prompt。
4. 跑相关 workflow 测试。
5. 在提交信息或变更说明里记录 prompt version。

示例：

```bash
curl -X POST http://localhost:8000/debug/prompt-preview \
  -H "x-debug-api-key: <key>" \
  -H "Content-Type: application/json" \
  -d '{"reading_goal":"exam","reading_variant":"gaokao","agent_type":"vocabulary","include_instructions":true}'
```

## 约束

- 不在文件名里编码版本号，Git 和 registry version 是版本来源。
- prompt 变化可能改变输出质量，不能只跑类型检查。
- 涉及 schema、字段含义或输出结构时，必须同步更新 API 文档、测试样例和 LangSmith / eval 过滤方式。
- Web、小程序和 future app 共享同一后端 prompt 输出，不能为某个客户端直接修改 canonical result 语义。

## 未来方向

Directus 或内部后台可以承载 prompt 审核、灰度和实验记录。当前先保留文件式 registry，降低变量。
