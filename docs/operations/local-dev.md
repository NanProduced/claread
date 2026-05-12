# 本地开发环境

本文描述 Claread 本地开发环境。

## 配置原则

- 不提交真实 `.env`。
- 不提交模型 API key、微信 secret、Zilliz token。
- 不写个人局域网 IP。
- 不把真实 DB / Redis 密码提交到仓库。
- 本地和生产配置通过 `.env.example` 区分。

## 推荐配置文件

```text
.env.example
services/api/.env.example
apps/miniprogram/.env.example
infra/docker/.env.example
services/api/config/model-profiles.example.json
services/api/config/model-presets.example.json
```

## 数据库

本地 Docker Compose 位于：

```text
infra/docker/docker-compose.local.yml
```

启动：

```powershell
cd infra/docker
docker compose -f docker-compose.local.yml up -d
```

当前使用 Claread 命名的 project 和 volume：

```text
claread
claread_postgres_data
claread_redis_data
```

词典三表已恢复到 `claread_postgres_data`。短期连接其他 Postgres 只作为本地 fallback，并且只写在本地 `.env`，不进入默认 compose。

compose 中的 DB / Redis 用户名和密码必须通过 `infra/docker/.env` 注入。

## 小程序 API 地址

`apps/miniprogram` 使用：

```text
TARO_APP_API_BASE_URL=http://localhost:8000
```

dev/staging/prod 由构建环境注入。

微信开发者工具本地调试 `http://localhost:8000` 时，需要关闭本地域名校验，或使用已经配置到小程序后台的合法 request 域名。

## Redis

本地可以默认关闭或按需开启。生产环境如有多 worker、缓存和任务能力，应显式启用 Redis。

## 模型配置

真实模型配置不提交。通过 `services/api/config/model-profiles.example.json`、`services/api/config/model-presets.example.json` 和环境变量注入模型配置。

结构化输出链路对模型能力敏感。更换 `DEFAULT_MODEL_PROFILE` 或 `ANNOTATION_MODEL_PROFILE` 后，需要重新验证解析结果是否包含词汇、语法、句式和翻译字段。
