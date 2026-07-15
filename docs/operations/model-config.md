# 模型配置

Claread 后端当前使用三层模型配置：

- `provider`：连接信息、鉴权、兼容性差异。
- `model`：provider 下的远端模型标识。
- `profile`：业务场景配置。是否开 thinking、是否作为 Ask planner / replan / workflow 默认，都落在这一层或 route override。

这样做的目的有两个：

1. 同一个 provider 可以挂多个 model，不再为每个 model 重复写一份 `base_url` / API key。
2. 同一个 model 可以在不同场景下复用，但 profile 允许叠加不同 `model_settings`。例如 workflow 关闭 thinking，而 Ask 主回答开启 thinking。

## 文件与入口

常用配置入口：

- `services/api/config/model-profiles.json`
- `services/api/config/model-presets.json`
- `services/api/config/reader-ask-model-options.json`
- `services/api/.env`

对应示例文件：

- `services/api/config/model-profiles.example.json`
- `services/api/config/model-presets.example.json`
- `services/api/config/reader-ask-model-options.example.json`
- `services/api/.env.example`

## 核心概念

| 概念 | 作用 |
|------|------|
| provider | 供应商连接配置，如 `base_url`、`api_key_env`、OpenAI 兼容性 profile |
| model | provider 下的远端模型名，如 `qwen3.7-max`、`deepseek-v4-flash` |
| profile | 场景级配置，指向某个 model，并叠加 `model_settings` |
| preset | 一组 route 到 profile 的映射，主要给内部调试、eval 或 workflow 请求级切换使用 |
| model option | Ask Claread 暴露给用户/运营的模型选项白名单，不直接暴露全部 profile |
| runtime budget | Ask 运行时 prompt/input/output 预算。只影响 compaction 和生成上限，不参与积分计费公式 |

## provider / model / profile 示例

```json
{
  "providers": {
    "dashscope": {
      "adapter": "openai_compatible",
      "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
      "api_key_env": "DASHSCOPE_API_KEY",
      "openai_profile": {
        "openai_chat_thinking_field": "reasoning_content",
        "openai_chat_send_back_thinking_parts": "field",
        "openai_supports_tool_choice_required": false
      }
    }
  },
  "models": {
    "qwen37-max": {
      "provider": "dashscope",
      "model_name": "qwen3.7-max"
    }
  },
  "profiles": {
    "workflow-qwen37-max": {
      "model": "qwen37-max",
      "model_settings": {
        "extra_body": {
          "enable_thinking": false
        }
      }
    },
    "ask-main-qwen37-max": {
      "model": "qwen37-max",
      "model_settings": {
        "extra_body": {
          "enable_thinking": true
        }
      }
    }
  }
}
```

上面这套配置表示：

- workflow 和 Ask 主回答复用同一个远端 `qwen3.7-max`
- workflow profile 关闭 thinking
- Ask profile 开启 thinking
- 兼容性差异由 `dashscope` provider 统一声明，而不是分散在每个 model 上

## Ask Claread 选项层

`reader-ask-model-options.json` 负责 Ask Claread 暴露给前端/运营的可选项。这里的选项不是全部 profile，而是一个经过运营筛选的白名单。

这里有两个必须分开的层：

- `billing_defaults` / `price_multiplier`：决定积分如何按实际 usage 结算，以及发起前最低预扣多少积分。
- `runtime_defaults` / `runtime_budget`：决定 Ask 每轮可用的 prompt/input/output token 预算，以及是否会触发 context compaction。

不要再把 `reserved_points` 理解成“这轮 Ask 的 token 上限”。它只是预扣/风控，不是运行预算。

示例：

```json
{
  "default_option": "deepseek-v4-flash",
  "billing_defaults": {
    "reserved_points": 10,
    "tokens_per_point": 1000,
    "billing_policy_version": "analysis_weighted_tokens_v1"
  },
  "runtime_defaults": {
    "max_input_tokens": 24000,
    "max_output_tokens": 3200,
    "prompt_buffer_tokens": 800
  },
  "options": {
    "deepseek-v4-flash": {
      "label": "DeepSeek V4 Flash",
      "description": "默认档位：快速、低成本。主回答与 replan 使用 DeepSeek V4 Flash。",
      "selection": {
        "routes": {
          "reader_ask": { "profile": "ask-main-deepseek-v4-flash" },
          "reader_ask_replan": { "profile": "ask-replan-deepseek-v4-flash" }
        }
      },
      "runtime_budget": {
        "max_input_tokens": 24000,
        "max_output_tokens": 3200
      },
      "price_multiplier": 1.0
    },
    "deepseek-pro": {
      "label": "DeepSeek V4 Pro",
      "description": "高质量备选档位：仅在用户显式选择时使用。",
      "selection": {
        "routes": {
          "reader_ask": { "profile": "ask-main-deepseek-v4-pro" },
          "reader_ask_replan": { "profile": "ask-replan-deepseek-v4-pro" }
        }
      },
      "price_multiplier": 1.3
    }
  }
}
```

说明：

- 默认档位是 DeepSeek V4 Flash；Flash profile 默认关闭模型内部 thinking，以控制延迟和成本。
- 用户可见运行步骤由安全的 Agent activity/progress 协议提供，不展示原始 chain-of-thought。
- DeepSeek V4 Pro 是显式高质量备选，不作为 Flash 的静默 fallback。
- Article RAG 是否开启与主回答模型选择正交；RAG-off 不得依赖切换模型。

这里的 `description` 既是前端提示文案，也承担“注释”作用。因为配置文件使用严格 JSON，不支持额外注释字段。

结算上，Ask 现在按实际 usage 计算积分，再用“预扣 + 差额补扣/退款”完成 settle；不再把最终扣费硬封顶到 `reserved_points`。

## 环境变量建议

优先把 provider key 放到本地 `.env` 或部署环境，而不是写进 JSON：

```bash
DEFAULT_MODEL_PROFILE=workflow-qwen36-plus
ANNOTATION_MODEL_PROFILE=workflow-qwen36-plus
ASK_CLAREAD_PROFILE=ask-main-deepseek-v4-flash
READER_ASK_REPLAN_MODEL_PROFILE=ask-replan-deepseek-v4-flash
MODEL_PROFILES_JSON=config/model-profiles.json
MODEL_PRESETS_JSON=config/model-presets.json
READER_ASK_MODEL_OPTIONS_JSON=config/reader-ask-model-options.json
DASHSCOPE_API_KEY=...
DEEPSEEK_API_KEY=...
MINIMAX_API_KEY=...
MOONSHOT_API_KEY=...
```

注意：

- `Settings` 会读取 `.env`，provider 的 `api_key_env` 也会从本地 `.env` 或外部环境变量中解析。
- `MODEL_PROFILES_JSON` 现在只支持新三层结构，不再兼容旧的“profile 直接写 provider/model/base_url/key”扁平格式。

## 请求级切换

`POST /analyze` 仍可带 `model_selection`。现在推荐优先切 profile 或 preset，而不是在请求里临时拼 provider 信息。

```json
{
  "text": "Your English article...",
  "model_selection": {
    "preset": "workflow_qwen37_max"
  }
}
```

或者：

```json
{
  "text": "Your English article...",
  "model_selection": {
    "routes": {
      "annotation_generation": {
        "profile": "workflow-deepseek-v4-pro"
      }
    }
  }
}
```

## 注意事项

- 不提交真实 API key、个人 base URL 和本地 `.env`。
- thinking 是否开启必须由 profile / route settings 决定，不允许再在业务代码里强制改写。
- Ask Claread 的主回答、planner、replan 可以分别走不同 profile。
- 结构化输出质量和模型能力相关；切换 provider、model 或 profile 后，必须重新验证解析结果中的词汇、语法、句式和翻译字段。
- 这轮统一的是 LLM 文本生成配置。embedding / rerank 已纳入同一概念模型，见下方。

## Embedding / Rerank 配置

embedding 和 rerank 使用与 chat/completion 相同的 provider / model / profile 三层配置，通过 `rag_embedding` 和 `rag_rerank` 两个 route 绑定。

### Adapter 类型

| Adapter | 用途 | SDK 调用 | 需要 base_url |
|---------|------|----------|--------------|
| `dashscope_embedding` | 文本向量 | `dashscope.TextEmbedding.call` | 否 |
| `dashscope_rerank` | 文档精排 | `dashscope.TextReRank.call` | 否 |

两者均通过 `api_key` / `api_key_env` 鉴权，不需要 `base_url`。

### 配置示例

```json
{
  "providers": {
    "dashscope_embedding": {
      "adapter": "dashscope_embedding",
      "api_key_env": "DASHSCOPE_API_KEY",
      "provider_options": {
        "dimension": 1024
      }
    },
    "dashscope_rerank": {
      "adapter": "dashscope_rerank",
      "api_key_env": "DASHSCOPE_API_KEY"
    }
  },
  "models": {
    "text-embedding-v4": {
      "provider": "dashscope_embedding",
      "model_name": "text-embedding-v4",
      "provider_options": { "dimension": 1024 }
    },
    "qwen3-rerank": {
      "provider": "dashscope_rerank",
      "model_name": "qwen3-rerank"
    }
  },
  "profiles": {
    "rag-embedding-v4": { "model": "text-embedding-v4" },
    "rag-rerank-qwen3": { "model": "qwen3-rerank" }
  }
}
```

### 环境变量

```bash
RAG_EMBEDDING_MODEL_PROFILE=rag-embedding-v4
RAG_RERANK_MODEL_PROFILE=rag-rerank-qwen3
```

### 向后兼容

当 `RAG_EMBEDDING_MODEL_PROFILE` 或 `RAG_RERANK_MODEL_PROFILE` 未配置时，embedding/rerank 模块会回退到旧字段：
- `BAILIAN_API_KEY`
- `BAILIAN_EMBEDDING_MODEL`（默认 `text-embedding-v4`）
- `BAILIAN_EMBEDDING_DIMENSION`（默认 `1024`）
- `BAILIAN_RERANK_MODEL`（默认 `qwen3-rerank`）

注意：
- 这是 deprecated fallback，仅用于兼容旧环境。
- 如果 route 已配置，但 profile 最终解析到错误 adapter，运行时会直接 fail fast，不会再静默回退到 `BAILIAN_*`。

### 审计字段

`rag_embedding` / `rag_rerank` 的 usage audit event 现在与 chat 主链路使用相同的字段：
- `model_route`：`rag_embedding` 或 `rag_rerank`
- `model_profile`：从 registry 解析的 profile 名
- `model_provider`：从 registry 解析的 provider 名（不再硬编码 `"bailian"`）
- `model_name`：实际调用的远端模型名

## Directus Authoring Workflow

LLM 配置可通过 Claread Console（Directus）进行可视化管理。

### 前置条件

1. Directus 容器运行中（`pnpm directus:up`）
2. 已执行 metadata sync：`pnpm directus:llm-config:sync-metadata`

### Authoring 流程

1. 在 Directus Admin UI 中打开 **LLM Config** module（landing page）
2. 点击卡片或侧栏导航进入对应集合进行 CRUD 操作：
   - **Providers**：添加/编辑供应商连接配置
   - **Models**：添加/编辑远端模型定义（选择 provider）
   - **Profiles**：添加/编辑场景配置（选择 model）
   - **Presets**：添加/编辑 route→profile 映射集合
   - **Ask Options**：添加/编辑 Ask Claread 用户可选档位
   - **Ask Config**：编辑 Ask 顶层配置（default_option / billing_defaults / runtime_defaults）
3. 新增记录默认 status=draft，确认后改为 active
4. 执行 export：`pnpm directus:llm-config:export-bundle`
5. 校验通过后，将导出的 3 个 JSON 文件复制到 `services/api/config/`

### Import / Backfill 流程

首次部署或 JSON 变更后，需要将 services/api/config/ 中的 3 个 JSON 文件同步到 Directus：

1. 确保 Directus 容器运行中（`pnpm directus:up`）
2. 执行 metadata sync：`pnpm directus:llm-config:sync-metadata`
3. 执行 import：`pnpm directus:llm-config:import-bundle`
4. 在 Directus UI 中验证各 collection 的记录数

import 脚本执行收敛同步：
- 按 slug 幂等 upsert，不会产生重复记录
- JSON 中省略的可选字段会显式写 null/默认值
- 例如：JSON 中删除了 provider 的 base_url，Directus 中对应值会被清空

### 完整 Authoring 流程

1. Import：`pnpm directus:llm-config:import-bundle`（JSON → Directus）
2. 在 Directus UI 中编辑配置
3. Export：`pnpm directus:llm-config:export-bundle`（Directus → JSON）
4. 校验通过后，将导出的 3 个 JSON 文件复制到 `services/api/config/`

### 数据流

```
JSON (真源) ──import──→ Directus (控制面) ──export──→ JSON bundle → services/api/config/
```

- Import 是收敛同步：JSON 省略的字段会在 Directus 中清空
- Export 从 Directus 读取 active 记录，包括 llm_ask_config 单例
- Round-trip 保证：import → export 后的 JSON 与源 JSON 等价（不含 status/sort/enabled 等控制面字段）

### 校验规则

Export 脚本在导出前自动校验，规则与后端 Pydantic schema 对齐：

- Adapter 必须是合法枚举值
- `openai_compatible` 必须有 `base_url`
- `dashscope_native` / `dashscope_embedding` / `dashscope_rerank` 必须有 `api_key_env`
- FK 引用链完整（profile → model → provider）
- Route 名称必须是合法 ModelRoute 枚举
- Embedding / rerank provider 存在时检查是否有对应 profile
