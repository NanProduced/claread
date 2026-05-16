# 模型配置

Claread 后端支持通过 profile、preset 和请求级 `model_selection` 切换模型。现阶段这套机制略重，但先保持稳定，不在当前基线中重构。

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
| `daily_annotation` | Daily Reader 高亮与词汇标注 |
| `daily_analysis` | Daily Reader 段落透读、收束和评分 |
| `daily_review` | Daily Reader 质检与 refinement |

## 推荐配置方式

优先使用外部 JSON 文件，不把 key 写进仓库：

```bash
DEFAULT_MODEL_PROFILE=minimax_m27
ANNOTATION_MODEL_PROFILE=minimax_m27
DAILY_ANNOTATION_MODEL_PROFILE=minimax_m27
DAILY_ANALYSIS_MODEL_PROFILE=minimax_m27
DAILY_REVIEW_MODEL_PROFILE=minimax_m27
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

## 注意事项

- 不提交真实 API key、个人 base URL 和本地 `.env`。
- 保留 `model_selection` API 兼容性，方便本地切模型和未来 eval。`/analyze` 当前主要承担匿名试用 / 调试兼容直连，不是正式用户能力的长期总入口。
- 配置文档放在全局 `docs/operations/`，因为 Web、小程序和内部评测都会共享同一后端模型配置。
- 如果未来简化模型配置，先补测试保护 `/analyze`、Daily Reader、academic workflow 的模型选择行为。
- 结构化输出质量和模型能力相关；切换模型后必须重新验证解析结果中的词汇、语法、句式和翻译字段。
