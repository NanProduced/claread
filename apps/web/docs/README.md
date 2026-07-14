# Claread Web 文档

本目录用于记录 Claread Web 客户端的设计、实现边界和验证文档。

Web 端共享 `services/api/`、PostgreSQL 数据、API contracts、纯业务 utils 和 design tokens，但不复用小程序 UI。Web 可以拥有独立的信息架构、阅读体验、交互密度、快捷键、批注能力和 render profile。

## 文档入口

| 文档 | 用途 |
|------|------|
| `../PRODUCT.md` | Web 端 impeccable 产品上下文，限定 Web 功能优先、Reader 优先、兼容多端总纲 |
| `../DESIGN.md` | Web 端正式设计系统与治理入口，限定主题、token、组件契约、页面模式与迁移边界 |
| `implementation-plan.md` | Web 当前实施基线、Reader/功能页范围、阶段划分与当前真实落地状态 |
| `tech-stack-options.md` | 技术栈清单、依赖用途、数据与状态管理、字体策略、API Contracts 策略 |
| `api-contract-audit.md` | Web 首期接口审计、枚举审计、错误态审计、需后端新增/增强清单 |
| `reader-ia.md` | Web Reader 信息架构、页面结构、核心交互、快捷键、词典浮层、批注系统、历史回看 |
| `../../../docs/architecture/multi-client-capability-matrix.md` | 以用户能力为观测点追踪 Web、小程序和后端共享能力、文本选区、批注收藏与学习资产差异 |
| `design/` | Reader 专项规范与本地评审参考；稳定设计结论以 `../DESIGN.md` 为准 |

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
- Web 首期先依赖现有 Claread API；`@claread/contracts` 当前承载跨端常量和轻量类型，后续再推动 OpenAPI 生成类型。
- Web 端能力增强通过客户端 UI、auth adapter、render profile 和后端通用能力协作完成。
- Plate readOnly runtime 已经是当前 Reader 主底座；相关真实状态以 `implementation-plan.md`、`reader-ia.md` 和 `design/component-system.md` 为准。
- `/library/assets` 已删除。Web Reader 的用户痕迹统一回到解析页正文、句侧 note marker 和 Ask Claread 显式引用，不再保留独立“学习资产中心”产品面。
- Web 临时任务文档必须标注 TMP，阶段完成后删除或压缩为稳定结论。

## 设计文档层级

- `../DESIGN.md` 是 Claread Web 的设计系统、token 与通用组件治理入口。
- `design/component-system.md` 只处理 Reader 专项组件和交互规则。
