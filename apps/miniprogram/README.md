# Claread 微信小程序

`apps/miniprogram/` 是 Claread 的微信小程序客户端。

它是 Claread 的第一个客户端，也是 Web 端未来能力的功能子集。小程序内的实现需要尊重微信平台限制，但这些限制不应反向污染后端 canonical 模型和 Web 架构。

当前小程序状态是稳定基线，不是功能终局。后续小程序仍会继续迭代，并和 Web 端共享后端能力与数据契约。

## 技术栈

- Taro 3
- React
- TypeScript
- Sass
- Zustand

## 启动

安装依赖应在仓库根目录执行：

```powershell
pnpm install
```

从仓库根目录构建微信小程序：

```powershell
pnpm miniprogram:build
```

从仓库根目录启动开发监听：

```powershell
pnpm miniprogram:dev
```

类型检查：

```powershell
pnpm miniprogram:typecheck
```

在当前目录内也可以直接运行：

```powershell
pnpm run build:weapp
pnpm run dev:weapp
pnpm run typecheck
```

然后使用微信开发者工具打开 `apps/miniprogram`。项目配置中的 `miniprogramRoot` 指向 `dist/`。

本地 API 使用 `http://localhost:8000` 时，微信开发者工具需要关闭本地域名校验，或使用已配置到微信后台的合法 request 域名。

新仓库小程序端统一使用 pnpm；不要同时维护 npm 和 pnpm 两套 lock 文件。

如果出现 `'taro' is not recognized`，优先回到仓库根目录重新执行 `pnpm install`。这通常是 workspace 安装或 `.bin` 链接未完成，不代表 Web 端和小程序端依赖冲突。

## 目录职责

```text
src/
  pages/        # 主包页面
  packageA/     # 历史、生词、个人中心等分包
  packageB/     # 每日精读分包
  packageC/     # 反馈、引导、关于等分包
  components/   # 小程序 UI 组件
  services/     # API、storage、sync、auth 等服务
  stores/       # Zustand 状态
  types/        # API DTO 和 ViewModel
  config/       # 环境、路由、阅读目标、反馈配置
```

## 平台边界

这些能力是微信小程序专属实现，不应和 Web 强行共享：

- `wx.login` / `Taro.login`
- 小程序分享
- Taro storage
- 小程序分包
- 小程序页面栈导航
- `Taro.showToast` / `Taro.showModal`
- `Taro.createInnerAudioContext`
- 微信头像和昵称能力
- 自定义导航栏和安全区计算

Web 端可以共享业务契约，但应使用 Web 原生实现。

## ID 契约

小程序当前依赖三类 ID：

| 字段 | 语义 | 用途 |
|------|------|------|
| `clientRecordId` | 客户端生成的稳定记录 ID | 页面路由、本地 storage、历史回看 |
| `cloudRecordId` | 云端 `analysis_records.id` UUID | 后端写操作、云端记录引用 |
| `taskId` | 分析任务 ID | 任务状态查询和轮询 |

禁止把云端 UUID 写入本应表达 `clientRecordId` 的字段。

## 本地优先同步

用户资产使用本地优先策略：

1. 用户操作先写本地 storage。
2. 写入持久化 sync queue。
3. 后台 flush 到云端。
4. 云端成功后更新本地同步状态。
5. 同步失败不回滚用户可见结果。

关键本地 key：

- `analysis_record_ids`
- `analysis_record_{id}`
- `record_identity_map`
- `sync_queue`
- `vocab_ids`
- `vocab_entry_{id}`
- `vocab_lemma_index`
- `favorite_records`
- `reading_preferences_local`

稳定基线范围见 `apps/miniprogram/docs/freeze-baseline.md`。小程序会继续推进功能和体验增强，但新增能力应服从多端 API 契约。
