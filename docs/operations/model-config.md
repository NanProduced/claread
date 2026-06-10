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
| model | provider 下的远端模型名，如 `qwen3.7-max`、`glm-5.1` |
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
  "default_option": "glm-standard",
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
    "glm-standard": {
      "label": "GLM-5.1",
      "description": "默认档位：主回答与 replan 使用 GLM-5.1，planner 固定走 Qwen 3.6 Plus。",
      "selection": {
        "routes": {
          "reader_ask": { "profile": "ask-main-glm51" },
          "reader_ask_planner": { "profile": "ask-planner-qwen36-plus" },
          "reader_ask_replan": { "profile": "ask-replan-glm51" }
        }
      },
      "runtime_budget": {
        "max_input_tokens": 24000,
        "max_output_tokens": 3200
      },
      "price_multiplier": 1.0
    }
  }
}
```

这里的 `description` 既是前端提示文案，也承担“注释”作用。因为配置文件使用严格 JSON，不支持额外注释字段。

结算上，Ask 现在按实际 usage 计算积分，再用“预扣 + 差额补扣/退款”完成 settle；不再把最终扣费硬封顶到 `reserved_points`。

## 环境变量建议

优先把 provider key 放到本地 `.env` 或部署环境，而不是写进 JSON：

```bash
DEFAULT_MODEL_PROFILE=workflow-qwen36-plus
ANNOTATION_MODEL_PROFILE=workflow-qwen36-plus
ASK_CLAREAD_PROFILE=ask-main-glm51
READER_ASK_PLANNER_MODEL_PROFILE=ask-planner-qwen36-plus
READER_ASK_REPLAN_MODEL_PROFILE=ask-replan-glm51
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
- 这轮统一的是 LLM 文本生成配置。embedding / rerank 仍需后续继续并到同一概念模型下。
