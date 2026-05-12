# Web Agent 指令

`apps/web/` 是 Claread Web 客户端。迁移第一阶段可以为空，后续开发时按本文件扩展。

## Web 定位

- Web 端应追求比小程序更完整的阅读、解析、批注和管理体验。
- Web 端共享 `services/api/`，不单独复制一套后端。
- Web 可以拥有更丰富的 render profile，但 canonical result 仍由后端统一产生。

## 开发原则

- 不被小程序 UI 限制反向约束。
- 桌面和移动 Web 都要考虑，但第一阶段可以先定义 MVP 范围。
- 设计规则放在 `docs/design/` 和 `apps/web/docs/`，不要复用旧小程序 handoff 文档作为 Web 真相源。
- 新增跨端类型时优先放入 `packages/contracts/`。

## 验证

Web 开发开始后，应补齐：

- typecheck
- build
- browser smoke test
- 关键阅读/解析页面截图验证

浏览器端显示效果需要用真实页面验证，不能只依赖静态代码审查。
