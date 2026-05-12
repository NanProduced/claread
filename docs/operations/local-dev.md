# 本地开发环境

本文描述新仓库本地开发环境的原则。具体命令在迁移落地后校准。

## 配置原则

- 不提交真实 `.env`。
- 不提交模型 API key、微信 secret、Zilliz token。
- 不写个人局域网 IP。
- 不写明文 DB / Redis 密码到 compose。
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

新仓库默认使用 Claread 命名的 PostgreSQL volume。

词典三表优先通过 dump/restore 从旧库迁入。短期连接旧 Postgres 只作为 fallback，并且只写在本地 `.env`，不进入默认 compose。

compose 中的 DB / Redis 用户名和密码必须通过 `infra/docker/.env` 注入。

## 小程序 API 地址

`apps/miniprogram` 使用：

```text
TARO_APP_API_BASE_URL=http://localhost:8000
```

dev/staging/prod 由构建环境注入。

## Redis

本地可以默认关闭或按需开启。生产环境如有多 worker、缓存和任务能力，应显式启用 Redis。

## 模型配置

真实模型配置不迁移。新仓库通过 `services/api/config/model-profiles.example.json`、`services/api/config/model-presets.example.json` 和环境变量注入模型配置。
