# Claread Web Component Library

> **状态**: `CURRENT` | **最后更新**: 2026-05-18
> 本文只记录 Claread Web 当前组件库事实、公共约束和后续维护规范。过程决策、阶段拆分和临时推进记录不再放在这里。

## 1. Purpose

Claread Web 组件库服务于登录后功能页和 Reader 工具层：

- `/read`
- `/reader/[recordId]`
- `/library`
- `/library/assets`
- `/vocabulary`
- `/review`
- `/settings`

它不覆盖：

- Landing / marketing / pricing / changelog
- 小程序端 UI
- 后端协议和 `render_scene` 合约

组件库的目标不是“再包一层 shadcn”，而是把 Claread 的 **lucid / editorial / instrumental / tactile** 视觉语言落实为可复用资产。

## 2. Document Map

当前只保留两份设计系统文档：

| 文档 | 职责 |
| --- | --- |
| `component-library-v0.md` | Web 通用组件库真相源，负责 token、theme、目录结构、通用组件、兼容边界和第三方准入 |
| `component-system.md` | Reader 专项补充文档，只处理原文画布、左右工具层、句后卡、选区、词典和 Ask Claread 等 Reader 特例 |

说明：

- Reader 以外的组件、token 和目录边界，以本文为准。
- Reader 的 slot、画布规则和交互特例，以 `component-system.md` 为准。

## 3. Current System

### 3.1 Token / Theme

当前 Web 统一消费 Claread Web token：

- 代码来源：`packages/design-tokens/src/web/`
- 交付形式：
  - `tokens.css`
  - `fonts.ts`
  - `tailwind-preset.ts`
- light-first，中文与英文字体槽位已在 Web Root Layout 接入

当前主语义 token：

- `web-canvas`
- `reader-paper`
- `surface`
- `surface-warm`
- `ink`
- `muted`
- `subtle`
- `hairline`
- `lens-blue`
- `lens-blue-soft`
- `vocab-amber`
- `phrase-lavender`
- `context-blue`
- `grammar-violet`
- `structure-green`
- `error-red`

约束：

- `lens-blue` 只用于焦点、激活、链接和关键动作
- 普通功能页和 Reader 工具层默认使用纸面和墨色语言
- 不允许在组件内部继续散落原始 hex 和各页私有 shadow recipe

### 3.2 Directory

当前 Web 组件目录：

```text
packages/design-tokens/
  src/web/

apps/web/src/components/
  primitives/
  composed/
  layout/
  reader/
  ui/
```

定义：

- `primitives/`：底层原子和第三方底座包装
- `composed/`：无业务数据耦合的 Claread 组合控件
- `layout/`：AppShell、SidebarRail 等页面骨架
- `reader/`：Reader 专属组件
- `ui/`：Ask Claread compatibility layer，不是新组件库主入口

### 3.3 Available Components

当前已落地的通用组件：

**Primitives**

- `Button`
- `IconButton`
- `Dialog`
- `Popover`
- `Tooltip`
- `Tabs`
- `Sheet`
- `DropdownMenu`
- `Switch`
- `ScrollArea`
- `Toast`
- `CommandMenu`
- `Select`
- `Slider`

**Composed**

- `SearchField`
- `SegmentedControl`
- `SelectField`
- `InfoCard`
- `StatCard`
- `SectionCard`
- `ListRow`
- `EmptyState`
- `PageHeader`
- `FilterBar`
- `TopActionBar`

**Layout**

- `AppShell`
- `SidebarRail`

### 3.4 Ask Compatibility Boundary

`apps/web/src/components/ui/` 当前只为 Ask Claread 保留，允许继续承载：

- `chat-container`
- `markdown`
- `collapsible`
- `message`
- `prompt-input`
- `tool`

但其规则已经固定：

- 只能给 Ask Claread 相关 UI 使用
- 新功能页和新通用组件禁止从这里取组件
- 可见样式必须向 Claread 自有组件库对齐
- 共享能力应逐步上移到 `primitives/` 或 `composed/`

## 4. Usage Rules

### 4.1 Import Rules

- 功能页业务代码优先从：
  - `@/components/primitives/*`
  - `@/components/composed/*`
  - `@/components/layout/*`
- `reader/` 内部只处理 Reader 特殊结构，不向普通功能页扩散
- `components/ui/*` 只允许 Ask Claread 及其兼容层内部使用
- 业务代码不得直接 import 第三方 UI 库

### 4.2 Component Rules

所有正式组件默认要求：

- `*.tsx`
- `*.stories.tsx`
- `README.md`

公共 props 约束：

- 优先提供 `variant / size / tone / density`
- icon-only 按钮必须有 `aria-label`
- 能 `forwardRef` 的组件一律 `forwardRef`
- 组件内部消费 Claread token，不允许页面继续覆盖出第二套语义

### 4.3 Style Rules

- spacing 只用 `4 / 8 / 12 / 16 / 24 / 32 / 48 / 64`
- 控件圆角使用 control token
- 容器圆角使用 surface token
- 阴影只使用组件库约定层级
- 不允许新增 shadcn 默认 zinc / neutral 后台风格

## 5. Third-Party Policy

当前锁定底座：

| Claread 组件 | 底座 |
| --- | --- |
| `Dialog` | Radix Dialog |
| `Popover` | Radix Popover |
| `Tooltip` | Radix Tooltip |
| `Tabs` | Radix Tabs |
| `Sheet` | Radix Dialog 二次包装 |
| `DropdownMenu` | Radix DropdownMenu |
| `Switch` | Radix Switch |
| `ScrollArea` | Radix ScrollArea |
| `Toast` | Sonner |
| `CommandMenu` | cmdk |
| `Select` | Base UI |
| `Slider` | Base UI |

准入规则：

1. 先说明场景和引入理由
2. 先用 Claread token 改造样式
3. 先包装进 `components/primitives/`
4. 业务层禁止直接 import 第三方实现
5. 必须补 story 和 README

## 6. Current Cleanup Standard

组件库收尾阶段必须保持以下状态：

- 不保留同一组件的双实现和双入口
- 不保留页面私有旧 recipe 与 `composed/` 并行存活
- 不把临时 tracker、截图或设计探索文档继续当真相源
- 不把 Ask Claread compatibility layer 重新扩散到普通功能页

当前文档边界：

- Web 通用组件库、token、theme、目录和第三方准入规范集中在本文。
- Reader 特有的画布、锚点、工作区和交互规则保留在 `component-system.md`。

## 7. Verification

组件库改动默认验证入口：

```powershell
pnpm --filter=@claread/web lint
pnpm --filter=@claread/web typecheck
pnpm --filter=@claread/web build
pnpm --filter=@claread/web ladle:build
```

说明：

- 如果 `lint` 被 Reader 既有问题阻塞，必须明确标注为 pre-existing，不得为了“通过 lint”随意改动 Reader 主体逻辑。
- 视觉类改动必须至少补一轮真实页面验证，而不是只看 stories。
