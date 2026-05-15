# Claread Web 文档

本目录用于记录 Claread Web 客户端的设计、实现边界和验证文档。

Web 端共享 `services/api/`、PostgreSQL 数据、API contracts、纯业务 utils 和 design tokens，但不复用小程序 UI。Web 可以拥有独立的信息架构、阅读体验、交互密度、快捷键、批注能力和 render profile。

## 文档入口

| 文档 | 用途 |
|------|------|
| `../PRODUCT.md` | Web 端 impeccable 产品上下文，限定 Web 功能优先、Reader 优先、兼容多端总纲 |
| `../DESIGN.md` | Web 端 impeccable 设计系统，限定 Reader 工作台、当前语法/句析 baseline、AI 工作区和后续 Artifact Studio / Grammar X-Ray 方向 |
| `tech-stack-options.md` | 技术栈备选对比、推荐方案、依赖清单、OpenAPI 类型生成策略 |
| `api-contract-audit.md` | Web 首期接口审计、枚举审计、错误态审计、需后端新增/增强清单 |
| `baseline-adaptation-plan.md` | 对照小程序 MVP 的 Web baseline 适配计划和已知后续评审项 |
| `reader-ia.md` | Web Reader 信息架构、页面结构、核心交互、快捷键、词典浮层、批注系统、历史回看 |
| `design/` | Web 端 UI 方向探索、视觉参考、设计纲要草案 |

## 文档边界

- Web 专属体验、技术栈、页面结构、浏览器验证和客户端实现记录在本目录。
- 任务分配、子任务拆分、agent prompt 和进度流水账只放 `tmp/`，不进入长期设计/实施文档。
- 跨端产品事实记录在 `docs/product/`。
- 跨端架构和共享边界记录在 `docs/architecture/`。
- 跨端设计原则记录在 `docs/design/`。
- 后端 API 事实以 `services/api/docs/api-contracts.md` 和 OpenAPI 为准。

## 当前原则

- 不把小程序 UI 拉伸成 Web 版。
- 不为 Web 复制一套业务后端。
- Web 首期先依赖现有 Claread API，再逐步推动 `packages/contracts/` 生成类型。
- Web 端能力增强通过客户端 UI、auth adapter、render profile 和后端通用能力协作完成。
- Web 临时任务文档必须标注 TMP，阶段完成后删除或压缩为稳定结论。
