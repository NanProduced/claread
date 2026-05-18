# Claread Web Component Library V0

> **状态**: `CURRENT` | **最后更新**: 2026-05-18
> 本文是 Claread Web 功能页组件库的统一规范入口。它覆盖 token、theme、目录结构、组件来源策略、准入流程和分阶段实施路线。Reader 专项细则仍放在 `component-system.md`。

## 1. Scope

本文只服务 Claread **Web 功能页**：

- `/read`
- `/reader/[recordId]`
- `/library`
- `/library/assets`
- `/vocabulary`
- `/review`
- `/settings`

不覆盖：

- Landing / marketing / pricing / changelog 等公开宣传页
- 小程序端 UI 组件体系
- Ask Claread chatbox 当前独立开发中的临时 UI 实现

约束：

- 组件库规范只作用于 `apps/web/`
- 不修改小程序端目录和 UI 约定
- 不要求 render_scene renderer 合约变化
- 业务代码不得直接依赖第三方 UI 组件，必须经过 Claread 包装层

## 2. Current Diagnosis

当前 Web 已有一批可运行功能页，但组件系统尚未形成统一资产：

- token 已在 `src/app/globals.css` 中存在一版实现，但仍以页面手写 class recipe 为主
- 根布局尚未接入 `next/font`
- `components.json` 已存在，但 `src/components/ui/` 实际为空，当前并没有真正成体系的 shadcn primitives
- `src/components/` 只有 `app-shell/`、`brand/`、`reader/` 三层，没有 `primitives/`、`composed/`、`layout/`
- Reader 已抽出部分组件，但词典详情、轻释义、句后摘要等核心 UI 仍滞留在 `ReaderWorkbench.tsx` 内部函数
- Library、Vocabulary、Settings、Read 页存在大量重复的搜索框、状态卡、pill、按钮和 panel 写法
- 当前样式层存在大量 arbitrary utility 和重复 recipe，说明视觉规则没有沉淀为可复用组件

这意味着现在最缺的不是“再做几个页面”，而是建立一套可以托住后续迭代的 Web 组件库边界。

## 3. Relationship To Existing Docs

| 文档 | 角色 |
| --- | --- |
| `apps/web/DESIGN.md` | Web 功能页和 Reader 的品牌/体验北极星 |
| `apps/web/docs/design/component-system.md` | Reader 专项组件规则和交互约束 |
| `apps/web/docs/design/direction-exploration.md` | 页面形态探索和上游 rationale 归档，不再作为当前规范真相源 |
| `apps/web/docs/implementation-plan.md` | Web 整体实施路径 |
| `本文` | Web 功能页组件库总规范，负责 token、theme、目录、组件来源和阶段推进 |

规则：

- Reader 专项问题优先参考 `component-system.md`
- 全站功能页通用组件、token、theme 和引库准入以本文为准
- `direction-exploration.md` 只保留页面结构和早期方向判断，不承担当前组件库规范职责
- 如两者冲突，以本文定义的组件边界和 token/theme 规则为基础，再由 Reader 文档补 Reader 特例

## 4. Token And Theme Decision

### 4.1 是否需要统一 theme 规范

结论：**需要。**

原因：

- 当前 Web 已有 Claread Paper 视觉方向，但只是散落在 `globals.css` 和页面 class 中
- 没有统一 theme 规范，就无法稳定包装 shadcn/Radix/Vaul/Sonner/cmdk 等底层组件
- Reader、Library、Vocabulary、Settings 现在已经共享同一套暖纸/墨色/蓝色焦点语言，theme 实际上已经存在，只是还没正式化

### 4.2 是否需要单独再拆一份 theme 文档

当前结论：**暂不单拆。**

原因：

- 现阶段 Web 组件库规模还不大
- theme 规则和 token、组件变体、第三方包装策略强耦合
- 如果再新建一份平行 theme 文档，会进一步分散设计真相源

因此本阶段做法是：

- 在本文内正式定义 Web theme 规范
- 等未来出现多套阅读主题、营销页主题或导出主题时，再考虑从本文中拆分出独立 theme 文档

### 4.3 Web Theme Model

Claread Web 应统一为一套 **semantic theme**，而不是直接依赖第三方库的默认 zinc / slate / neutral 视觉。

推荐分层：

1. **Foundation scale**
   - 基于 Radix Colors
   - light：`sand` + `sage`
   - dark：`slate` + `sage` 或与之等价的暖暗阶

2. **Claread semantic tokens**
   - `canvas`
   - `paper`
   - `panel`
   - `surface`
   - `line`
   - `ink`
   - `ink-muted`
   - `lens`
   - `vocab`
   - `phrase`
   - `context`
   - `grammar`
   - `structure`
   - `danger`

3. **Component tokens**
   - button
   - input
   - panel
   - card
   - overlay
   - list row
   - reader mark

原则：

- Theme 是 Web-only，不向小程序施压
- theme 不等于“换肤玩具”，它首先是功能页的统一视觉语法
- 第三方组件进入 Claread 后，必须先消费 semantic token，而不是保留原库默认色

## 5. Token Rules

### 5.1 Delivery Form

所有 token 以两种形式交付：

- CSS variables
- Tailwind preset / theme extension

原因：

- 自写组件直接读 CSS variables
- Tailwind utility、shadcn 包装组件和第三方适配层统一消费 semantic aliases

### 5.2 Color Rules

- `#FAF9F6` 是 light theme 的 paper baseline
- neutral 基础阶优先从 Radix `sand / sage` 映射
- `lens-blue` 只用于 CTA、focus、激活态、品牌链接，不用于普通按钮海洋
- 语义色只允许出现在有明确语义的 mark、badge、state 中

### 5.3 Typography Rules

Web 应统一使用 `next/font` 管理四类字体槽位：

- 英文正文 serif：`Newsreader` 或 `Source Serif 4`
- 英文 UI sans：`Inter` / `Geist`
- 中文正文 serif：`Source Han Serif SC` 或同级方案
- 中文 UI sans：系统 `PingFang SC` 为第一阶段基线

原则：

- Reader 正文字体和 UI 字体必须分离
- 功能页标题可以使用 editorial serif，但控件与表单必须回到清晰的 UI sans

### 5.4 Spacing Rules

只允许使用以下节奏：

- `4`
- `8`
- `12`
- `16`
- `24`
- `32`
- `48`
- `64`

规则：

- 禁用奇数和临时拍脑袋 spacing
- 允许少量字号和 line-height 的非整数值，但 layout spacing 不允许漂移

### 5.5 Radius Rules

拆成两套：

- UI controls：`4 / 6 / 8`
- Containers / surfaces：`12 / 14`

Reader 专项可继续保留更柔和的大圆角，但应属于 surface token，而不是页面自己写值。

### 5.6 Shadow Rules

只保留 3 档：

- `shadow-1`：安静卡片 / 输入框
- `shadow-2`：浮层 / 面板
- `shadow-3`：高层弹出层

禁止继续出现每个页面各写一条 `shadow-[...]` 的情况。

### 5.7 Motion Rules

统一：

- duration：`160 / 240 / 320ms`
- easing：一套标准曲线
- Framer Motion 作为标准动画层，但第一阶段只先定义 token，不要求立刻大规模接入

原则：

- Claread 的动效应是沉静、克制、带一点纸面器物感
- 不为“精致”增加营销式大动作

## 6. Directory Standard

建议目录：

```text
packages/design-tokens/
  src/
    web/
      tokens.css
      tailwind-preset.ts
      fonts.ts

apps/web/src/components/
  primitives/
  composed/
  reader/
  layout/
  brand/
```

定义：

- `primitives/`
  - 底层无业务原子
  - 可包装 Radix / Vaul / Sonner / cmdk / Base UI
- `composed/`
  - 业务无关的组合控件
  - 例如 `SearchField`、`InfoCard`、`StatCard`、`PageHeader`
- `reader/`
  - Claread Reader 特有组件
  - 不要求复用通用 card 语义
- `layout/`
  - App shell、page frame、section shell、sidebar / rail

## 7. Naming And API Rules

### 7.1 Naming

- 组件名：PascalCase
- 文件名：与导出组件同名
- 变体命名统一使用：
  - `variant`
  - `size`
  - `tone`
  - `density`

### 7.2 Props

所有公共组件默认要求：

- `className?`
- `children?`
- `asChild?` 仅在确有 slot/trigger 需求时提供
- `data-*` 和 aria 属性可透传

优先：

- `forwardRef`
- 可组合的受控 / 非受控模式
- 不把页面私有状态耦进 primitive

### 7.3 Accessibility Defaults

- icon-only 按钮必须要求 `aria-label`
- Dialog / Drawer / Sheet 默认含标题和关闭路径
- Select / Switch / Slider 不允许裸用无 label 形态进入业务页
- sentence / selection / lookup 这类 Reader 特殊控件必须保留键盘路径

## 8. Common Component Inventory

下表列的是后续应纳入 Claread Web 组件库的通用组件，不等同于一次性全部实现。`来源策略` 表示推荐的底层实现方式。

| 组件 | 层级 | 来源策略 | 说明 |
| --- | --- | --- | --- |
| `Button` | primitive | 自写包装，视觉自研；底层可参考 shadcn | 所有按钮的统一入口 |
| `IconButton` | primitive | 自写 | 替代散落的收藏、删除、更多等按钮 |
| `Input` | primitive | 自写包装 | 文本输入基元 |
| `Textarea` | primitive | 自写包装 | `/read`、反馈、批注、AI 输入共用 |
| `SearchField` | composed | 自写，组合 `Input` + icon + clear | Library / Vocabulary / Assets 共用 |
| `Select` | primitive | Phase 1 先包原生，Phase 2 评估 Base UI / Radix | Settings 首要替换目标 |
| `Switch` | primitive | Radix 包装 | 设置项、阅读模式开关 |
| `Slider` | primitive | React Aria 或 Base UI 包装 | 字号 / 行距等 |
| `Tabs` | primitive | Radix 包装 | Filter / segment 统一 |
| `SegmentedControl` | composed | 自写，基于 button/tabs | `/read` 模式和细分场景 |
| `Dialog` | primitive | Radix 包装 | 确认、阻断流程 |
| `Sheet` | primitive | Radix Dialog 包装 | 桌面侧边面板 |
| `Drawer` | primitive | Vaul 包装 | 移动端 bottom sheet / drawer |
| `Popover` | primitive | Radix 包装 | 普通触发弹层 |
| `Tooltip` | primitive | Radix 包装 | 轻提示 |
| `DropdownMenu` | primitive | Radix 包装 | 更多操作 |
| `CommandMenu` | primitive | cmdk 包装 | 全局命令面板 |
| `Toast` | primitive | Sonner 包装 | 成功/失败/轻反馈 |
| `ScrollArea` | primitive | Radix 包装 | panel / rail / menu |
| `InfoCard` | composed | 自写 | 右栏说明卡、状态说明卡 |
| `StatCard` | composed | 自写 | 数量、同步、额度等信息卡 |
| `PageHeader` | composed | 自写 | 功能页统一标题区 |
| `SectionCard` | composed | 自写 | Settings / form / grouped content 容器 |
| `EmptyState` | composed | 自写 | Library / Vocabulary / Assets 共用 |
| `ListRow` | composed | 自写 | 阅读记录、生词、资产列表的基础骨架 |
| `Badge` | primitive | 自写或 shadcn 包装 | 状态、标签、语义 pill |
| `ProgressBar` | primitive | 自写 | Settings 配额条等 |
| `RailNav` | layout | 自写 | App shell 左侧 rail |
| `AppShell` | layout | 自写 | 页面布局容器 |

## 9. Reader-Specific Component Target List

Reader 核心组件不应塞进通用 `composed/`，应单独演化：

| 目标组件 | 当前映射 | 后续动作 |
| --- | --- | --- |
| `ReadingPane` | `ReaderCanvas` + `ReaderSentenceRow` | 作为 Reader 主画布正式命名 |
| `WordLookupSlip` | `InlineLookupPreview` + `DictionaryDetailPanel` | 从 `ReaderWorkbench` 抽出 |
| `SentenceCard` | `SentenceEntryCard` + `SentenceEntrySummary` | 重写为统一句后卡体系 |
| `DictionaryPanel` | `DictionaryDetailPanel` | 抽出并独立文档化 |
| `SelectionToolbar` | 已存在 | 保留并纳入正式 primitives 约束 |

说明：

- Ask Claread chatbox 当前由其他 agent 独立推进，本轮不纳入组件库统一改造范围
- 后续只要求它最终接入 Claread token 和包装边界，不要求现在并入本文实施项

## 10. Third-Party Admission Rules

任何新第三方组件进入 Web 端必须满足：

1. 先说明场景和引入理由
2. 先用 Claread token 改造样式
3. 包装进 `components/primitives/`
4. 业务代码禁止直接 import 第三方组件
5. 必须补 story 和 README

评估优先级：

1. 视觉中立性
2. 可访问性
3. 打包体积
4. 维护活跃度
5. TypeScript 体验

推荐底座策略：

- 保留主底座：shadcn/ui + Radix
- 明确引入：Vaul / Sonner / cmdk / Floating UI
- 观察位：Base UI / React Aria
- 仅借鉴，不直接进入主链：HeroUI / Mantine / Geist UI / Park UI

### 10.1 Locked Primitive Decisions

本轮已经拍板的第三方 primitive 底座如下：

| Claread Primitive | 底座 |
| --- | --- |
| `Dialog` | `@radix-ui/react-dialog` |
| `Popover` | `@radix-ui/react-popover` |
| `Tooltip` | `@radix-ui/react-tooltip` |
| `Tabs` | `@radix-ui/react-tabs` |
| `Sheet` | `@radix-ui/react-dialog` 二次包装 |
| `DropdownMenu` | `@radix-ui/react-dropdown-menu` |
| `Switch` | `@radix-ui/react-switch` |
| `ScrollArea` | `@radix-ui/react-scroll-area` |
| `Toast` | `sonner` |
| `CommandMenu` | `cmdk` |
| `Select` | `@base-ui/react/select` |
| `Slider` | `@base-ui/react/slider` |

明确不在本轮接入：

- `Drawer`
- `Vaul`

### 10.2 Transition Boundary

- `apps/web/src/components/primitives/` 是 Claread Web 新组件库主入口。
- `apps/web/src/components/ui/` 当前保留为 Ask Claread 兼容层，不作为本轮主入口。
- 新业务代码只允许 import `components/primitives/`。
- 现有 Ask Claread UI 文件不要求本轮迁移，但后续应逐步接入 Claread token 和 primitive 边界。

## 11. Deliverables

每个正式组件至少包含：

- `*.tsx`
- `*.stories.tsx`
- `README.md`

README 最少写清：

- 组件角色
- 推荐使用场景
- variant / size / tone 说明
- a11y 默认行为
- 与第三方底座的关系

Stories 展示层当前使用 `Ladle`。本轮不并行引入 Storybook。

## 12. Phased Rollout

### Phase 1

- 建立 `packages/design-tokens`
- 接入 `next/font`
- 正式定义 Web theme
- 建立 `primitives / composed / layout / reader` 目录
- 优先收口：
  - `Button`
  - `IconButton`
  - `SearchField`
  - `InfoCard`
  - `StatCard`
  - `Select`

### Phase 2

- 把高频 shadcn 默认形态包装成 Claread primitives
- 引入并包装：
  - `Vaul`
  - `Sonner`
  - `cmdk`
  - 必要时的 `Radix ScrollArea`
- 清理页面中的重复 recipe

### Phase 3

- Reader 三件套重写：
  - `ReadingPane`
  - `WordLookupSlip`
  - `SentenceCard`
- 去 card 化
- 增强纸面质感
- 保持 render_scene 合约不变

## 13. Coordination Constraints

- Ask Claread 当前独立开发中的 chatbox UI 不在本轮重构范围
- 如组件目录或 token 文件与其工作区发生边界冲突，优先保持文件写入范围清晰，不回退对方改动
- 任何组件库调整必须保持小程序端零影响

## 14. Verification

文档层验收标准：

- Web docs 入口能明确找到组件库规范
- Reader 专项规范与全站组件库规范边界清楚
- TMP 跟踪文档只记录阶段进展，不写长期事实

实施前置验证入口仍为：

```powershell
pnpm --filter=@claread/web lint
pnpm --filter=@claread/web typecheck
pnpm --filter=@claread/web build
```
