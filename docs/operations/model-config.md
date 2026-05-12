# 模型配置

Claread 后端支持通过 profile、preset 和请求级 `model_selection` 切换模型。现阶段这套机制略重，但迁移阶段先保持，不在 clean import 中重构。

## 核心概念

| 概念 | 作用 |
|------|------|
| model profile | 单个模型供应商、模型名、base URL、key 和 settings |
| model preset | 一组路由到 profile 的预设 |
| model selection | 请求级覆盖，用于本地调试、评测或特定任务切换 |

当前主要路由：

| 路由 | 用途 |
|------|------|
| `annotation_generation` | 主教学标注和解析生成 |

## 推荐配置方式

优先使用外部 JSON 文件，不把 key 写进仓库：

```bash
DEFAULT_MODEL_PROFILE=minimax_m27
ANNOTATION_MODEL_PROFILE=minimax_m27
MODEL_PROFILES_JSON=config/model-profiles.local.json
MODEL_PRESETS_JSON=config/model-presets.local.json
```

`config/*.local.json` 应进入 `.gitignore`。仓库只保留 `.example.json`。

示例结构：

```json
{
  "minimax_m27": {
    "provider": "openai_compatible",
    "model_name": "MiniMax-M2.7",
    "base_url": "https://api.example.com/v1",
    "api_key": "use-env-or-local-only",
    "model_settings": {
      "temperature": 0.1,
      "max_tokens": 4000
    }
  }
}
```

## 请求级切换

`POST /analyze` 可带 `model_selection`：

```json
{
  "text": "Your English article...",
  "model_selection": {
    "preset": "local_eval"
  }
}
```

也可以直接指定路由：

```json
{
  "text": "Your English article...",
  "model_selection": {
    "routes": {
      "annotation_generation": {"profile": "local_qwen"}
    }
  }
}
```

## 迁移注意

- 不迁移真实 API key、个人 base URL 和旧 `.env`。
- 保留 `model_selection` API 兼容性，方便本地切模型和未来 eval。
- 配置文档放在全局 `docs/operations/`，因为 Web、小程序和内部评测都会共享同一后端模型配置。
- 如果未来简化模型配置，先补测试保护 `/analyze`、daily reader、academic workflow 的模型选择行为。
