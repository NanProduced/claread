# Claread Web

`apps/web/` 是 Claread 的 Web 客户端。Web 共享 `services/api/`、PostgreSQL 数据、API contracts、纯业务 utils 和 design tokens，但不复用小程序 UI，也不复制一套后端。

当前 Web 已初始化为 Next.js App Router 项目。首期重点是功能页面、Reader、批注、词典、历史和分享页骨架；landing、关于、帮助、博客等内容页先保留占位入口。

## 技术栈

- Next.js App Router
- React
- TypeScript
- Tailwind CSS
- Radix Primitives / shadcn/ui selective copy
- TanStack Query
- Zustand
- Floating UI
- Motion

## 启动

安装依赖应在仓库根目录执行：

```powershell
pnpm install
```

启动开发服务器：

```powershell
pnpm web:dev
```

默认访问：

```text
http://127.0.0.1:3000
```

验证：

```powershell
pnpm web:typecheck
pnpm web:lint
pnpm web:build
```

也可以直接定位 Web workspace：

```powershell
pnpm --filter @claread/web run dev
pnpm --filter @claread/web run build
```

如果出现 `next` 或 `tsc` 无法识别，优先停止所有 dev/watch 进程，然后回到仓库根目录重新执行 `pnpm install`。

## 文档

Web 专项文档位于 `apps/web/docs/`：

- `development-tracker.md`
- `tech-stack-options.md`
- `api-contract-audit.md`
- `reader-ia.md`
