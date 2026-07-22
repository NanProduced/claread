# Claread Web 统一主题与语义 Token 调研

> 日期：2026-07-16  
> 范围：只研究 Claread Web 的 `system | light | dark` 主题偏好、CSS semantic tokens 与 shadcn/ui、Tailwind 的衔接；不改产品代码或配置。  
> 依据：仅使用 shadcn/ui 和 Tailwind CSS 官方文档。本文不是视觉稿，也不替代后续无障碍审计。

## 结论摘要

1. **保留一个主题偏好，渲染为两个 token 集。** `system` 应只决定 Light 或 Dark 的解析结果；运行时根元素只暴露一种实际渲染状态（建议 `.dark` 或无 `.dark`），不引入第三套 Paper 颜色。Tailwind 默认以 `prefers-color-scheme` 驱动 dark variant，并明确支持以 class/data attribute 覆盖，因而可与一个应用级 provider 对接。[Tailwind Dark mode](https://tailwindcss.com/docs/dark-mode)
2. **以语义 token 而不是色阶或页面局部 raw 色值组织主题。** shadcn/ui 建议 CSS variables，并把这些值映射为 `bg-background`、`text-foreground`、`border-border`、`ring-ring` 等 utility；组件只应消费语义角色，视觉校准只改 Light/Dark token 值。[shadcn/ui Theming](https://ui.shadcn.com/docs/theming)
3. **基础层采用中性灰而非暖黄纸色或蓝黑色。** 这是基于两张当前截图的产品判断：Light 的暖白、Dark 的蓝黑与暖白文字，均使三栏阅读工作台显得有多套视觉语言。shadcn/ui 的 `neutral` 是可用的起点而非要复制的成品；它提供 `background`、`card`、`muted`、`border`、`sidebar` 等角色的完整浅深对应值。[shadcn/ui 默认 neutral scaffold](https://ui.shadcn.com/docs/theming#default-theme-css)
4. **蓝色只承担交互/定位，不承担大面积装饰。** 使用 `primary`、`ring` 和必要的 `accent` 表达主操作、焦点与当前态；正文、主画布、侧栏、卡片和分隔线以中性 token 构成层级。shadcn/ui 的 token 约定明确区分 high-emphasis primary、hover/selected accent、muted supporting UI、border/input/ring，适合把阅读内容从控制 UI 中分离出来。[shadcn/ui token roles](https://ui.shadcn.com/docs/theming#theme-tokens)

## 建议目标架构

```text
用户偏好：system | light | dark
        │
AppearanceProvider：持久化偏好 + 监听系统主题（仅 system）
        │
根元素：Light => 无 .dark；Dark => .dark
        │
CSS：:root / .dark 各定义同名 semantic token
        │
Tailwind：@theme inline 映射 --color-* 到 token
        │
组件：只使用 bg-background、text-foreground、border-border、
      bg-card、bg-popover、bg-accent、text-muted-foreground、ring-ring 等
```

此结构的关键是：偏好层不发明组件颜色，组件也不直接分支 `dark:` 去表达同一个语义颜色。跨主题变化集中在同名 CSS 变量的覆盖值中。Tailwind 说明 `@theme` 变量会生成可用 utility，而普通 `:root` 变量仍可用于不需要 utility 的 CSS 值；shadcn/ui 的 `@theme inline` 映射展示了将二者结合的方式。[Tailwind Theme variables](https://tailwindcss.com/docs/theme)；[shadcn/ui Adding New Tokens](https://ui.shadcn.com/docs/theming#adding-new-tokens)

## 最小 token 合同

| 语义层 | 必需 token | 使用规则 |
| --- | --- | --- |
| 应用画布 | `background` / `foreground` | 页面、阅读区的默认底色与正文；不得用页面局部 HEX 覆盖。 |
| 层级 surface | `card`、`popover` 及各自 `-foreground` | `card` 仅用于确有层级的面板；浮层统一走 `popover`，不以渐变制造层级。 |
| 低强调 UI | `secondary`、`muted` 及 `-foreground` | 辅助按钮、元数据、占位与弱化区域；正文不可借用 `muted-foreground`。 |
| 交互 | `primary`、`accent`、`ring` 及对应前景色 | `primary` 为高强调操作；`accent` 为 hover/selected surface；`ring` 始终可见且独立于颜色状态。 |
| 结构 | `border`、`input` | 统一分隔线和表单轮廓，避免对每个三栏边界单独调色。 |
| 侧栏 | `sidebar`、`sidebar-foreground`、`sidebar-accent`、`sidebar-border`、`sidebar-ring` | 左侧阅读导航与右侧 Ask 面板应共享角色词汇；若视觉层级不同，只在 semantic token 值中区别，不另建 Paper 主题。 |
| 状态 | `destructive` / `destructive-foreground`；按需扩展 `warning`、`success` 及前景 token | 每个状态都需文字、图标或形状补充，不能只依赖颜色。 |

上述 token 及其职责来自 shadcn/ui 的官方角色表；若扩展 `warning` 等 token，官方示例要求同时在 `:root` 与 `.dark` 定义并在 `@theme inline` 暴露，避免某一主题出现未映射状态。[shadcn/ui Theme tokens](https://ui.shadcn.com/docs/theming#theme-tokens)；[shadcn/ui Adding new tokens](https://ui.shadcn.com/docs/theming#adding-new-tokens)

## 落地原则

### 1. 主题所有权与迁移

- `AppearanceProvider` 是唯一可写主题偏好的 owner；Reader、Ask、Settings 和所有私有页只能读解析后的 theme，不应再各自创建 localStorage key。
- 将历史 `paper` 在读取时迁移为 `system`，随后停止写入并删除界面入口。此为 Claread 约束下的兼容策略；Tailwind 所需只是一个可控制 dark variant 的 selector，不要求第三主题。[Tailwind Dark mode](https://tailwindcss.com/docs/dark-mode)
- `color-scheme` 应与实际 Light/Dark 状态同步，让原生控件与滚动条不落在相反主题；这是浏览器 UI 协调项，应随实现和目标浏览器验证。

### 2. Token 与 utility 的边界

- 应用 token 值放在主题 CSS 中；用 `@theme inline` 将它们映射为 Tailwind `--color-*`，从而形成稳定的 `bg-*`、`text-*`、`border-*` API。不要把 `--color-neutral-*` 当成页面视觉语义 API。Tailwind 明确区分会生成 utility 的 `@theme` 与仅保存一般变量的 `:root`。[Tailwind Theme variables](https://tailwindcss.com/docs/theme)
- 组件优先写 `bg-card text-card-foreground` 这类前景/背景成对 token。shadcn/ui 的约定就是 surface 与 `-foreground` 配对，能避免浅深主题切换后文字反差丢失。[shadcn/ui Token convention](https://ui.shadcn.com/docs/theming#token-convention)
- 只在真实的特例引入领域 token，例如阅读标注的 `reading-highlight-*`、句法层的 `analysis-*`；每个领域 token 必须定义 Light/Dark 两套值、前景色和用途，且不能代替通用 surface token。

### 3. 三栏阅读工作台的视觉分层

- 左侧导航、中央阅读区、右侧 Ask 面板首先由 `sidebar`、`background`、`card`/`popover` 的明度阶差表达；边界以 `border` 表达。截图中的纸纹、暖黄渐变、蓝黑渐变、glow 应从私有功能页基线移除。
- 中央阅读区保持最平静的 `background`；标题、正文、译文、元数据分别使用 `foreground` 与 `muted-foreground` 的明确层级。舒适度由正文宽度、行高、段距和留白承担，而不是替内容铺纹理。
- 右侧 Ask 仍是操作区域，不应变成另一套主题：其输入框、依据卡、工具栏分别使用同一套 `input`、`card`/`popover`、`border`/`accent` 角色。只有当前会话、焦点与主发送动作使用交互色。

### 4. 避免两套机制互相竞争

- 对同一语义色，优先 token utility；不要同时给组件写一套 raw 色值和 `dark:` 覆盖。Tailwind 的 dark variant 适合结构或显示差异，色彩翻转应由 `.dark` 覆盖 token 完成。[Tailwind Dark mode](https://tailwindcss.com/docs/dark-mode)
- 只在内容的确不同（例如图标在暗色下替换、阴影/边界样式特殊）时使用 `dark:`，并限定在组件内部；不能让 Reader、Ask 与 Settings 各自解释主题。
- 保留一个 `--radius` 基准并推导层级圆角，避免三栏面板、卡片、输入框出现无意的圆角语言混杂。shadcn/ui 以单一 radius token 推导 scale。[shadcn/ui Radius scale](https://ui.shadcn.com/docs/theming#radius-scale)

## 候选的实施拆分（供后续批准后执行）

1. **盘点与合同冻结（只读 + 测试设计）**：列出现有全局 token、Reader 平行主题、raw 色值、纹理/渐变、所有主题持久化键；确定旧 `paper -> system` 的读取迁移与删除窗口。产物是 token 对照表和页面矩阵。
2. **主题 owner 收敛**：以 `AppearanceProvider` 统一偏好、系统监听和根元素 selector；Reader 删除独立主题读写与 Paper 菜单，保留旧值迁移。此切片不改各页面视觉风格。
3. **token 基线重置**：在 design-token 层落定中性 Light/Dark token、Tailwind 映射和语义状态 token；逐项验证 shadcn 组件的默认 class 都落入正确颜色角色。
4. **Reader 主面板迁移**：先中央阅读区，再左导航和右 Ask，移除纹理、渐变、raw 色值，并将表面、边界、选中态迁至语义 utility；不触及旧 AI Workflow / 新 Agentic orchestration 的路由或功能。
5. **其余私有页迁移**：Settings、阅读记录、Daily Read 等按共享 token 复用；Daily Read 仍不实现收藏、解析或 Ask。
6. **视觉与无障碍验收**：对 system/light/dark、窄屏、键盘焦点、hover/selected/disabled/destructive、长中文与长英文内容逐页截图比对；以 WCAG 2.2 复核正文与 UI 的对比度、焦点和非色彩状态。

## 后续验收问题

- system 在操作系统切换后是否同步更新，手动 light/dark 是否保持稳定？
- 刷新后 Reader、Ask、Settings 是否始终显示同一个实际主题，且本地已存 `paper` 用户不会被阻断？
- 任一可点击元素是否在键盘焦点、hover、selected、disabled、error 下仍可辨识，且状态并非只靠颜色？
- 搜索、下拉菜单、Dialog、Tooltip、输入框、滚动区域等 shadcn surface 是否继承同一个 token 合同？
- Reader 文本、译文、标注、语法分析与 Ask 引用是否在两套主题中保持阅读层级，而无额外纸纹/渐变带来的噪声？

## 官方来源

- [shadcn/ui — Theming](https://ui.shadcn.com/docs/theming)
- [shadcn/ui — Dark Mode](https://ui.shadcn.com/docs/dark-mode)
- [Tailwind CSS — Theme variables](https://tailwindcss.com/docs/theme)
- [Tailwind CSS — Dark mode](https://tailwindcss.com/docs/dark-mode)
- [WCAG 2.2 — Contrast (Minimum)](https://www.w3.org/TR/WCAG22/#contrast-minimum)
- [WCAG 2.2 — Non-text Contrast](https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast.html)
