---
name: Claread Web Design System
description: Claread Web 的 token-first 视觉系统与阅读型产品界面规范。
colors:
  surface-canvas: "#f7f5f0"
  surface-stage: "#faf9f6"
  surface-raised: "#fbfaf6"
  surface-overlay: "#fbfaf6"
  text-primary: "#151515"
  text-secondary: "#666a73"
  border-subtle: "#e3ded3"
  action-primary: "#1f5eff"
  action-primary-soft: "#eaf1ff"
  feedback-success: "#3c8c68"
  feedback-warning: "#e4b000"
  feedback-error: "#be123c"
typography:
  ui:
    fontFamily: "Inter, PingFang SC, Hiragino Sans GB, Microsoft YaHei, sans-serif"
  reading:
    fontFamily: "Newsreader, Source Han Serif SC, Songti SC, Georgia, serif"
rounded:
  control-xs: "4px"
  control-sm: "6px"
  control-md: "8px"
  surface-sm: "12px"
  surface-md: "14px"
  note: "16px"
spacing:
  1: "4px"
  2: "8px"
  3: "12px"
  4: "16px"
  5: "24px"
  6: "32px"
  7: "48px"
  8: "64px"
---

# Claread Web Design System

## Overview

**Creative North Star: “Notion-like Pragmatic Minimalism for Reading.”**

用户在桌面浏览器中长时间阅读一篇英文材料：左侧资源轨道稳定存在，中央文章列拥有绝对优先级，系统只在用户需要操作或理解当前状态时靠近。界面应像可靠、克制的阅读工具，而不是展示 AI 能力的控制台。

这是产品型 UI，不是营销页面。熟悉的导航、按钮、输入与菜单优先于新奇的视觉语言；文章标题和阅读内容可以有编辑性，所有控制、标签、状态和数据保持清晰的无衬线秩序。实际渲染主题只有浅色与深色；跟随系统是偏好，而不是第三套视觉系统。

**Key Characteristics:**

- 阅读列优先，左侧资源轨道与右侧工具层都不能挤压正文。
- 状态持久、低噪、可追溯；只有用户必须决定时才打断。
- 结构来自留白、细分隔线、稳定排版与有限层级，不来自卡片堆叠。
- Agentic 能力围绕当前文章与锚点出现，不形成页面级聊天线程。

## Colors

颜色以克制的工作表面和一个稀少的行动色组成。surface-canvas 承担应用外层，surface-stage 承担阅读与主任务，surface-raised 和 surface-overlay 只用于需要明确分层的局部表面。

**The One Accent Rule.** action-primary 只用于主要行动、当前选择、键盘焦点和少量关键状态。它不得成为装饰色，也不得为不活跃控件制造视觉噪音。

理解颜色只属于文章内部语义：词汇、短语、语境、语法和结构提示必须贴近原文，并以位置、文案或图标补足颜色语义。普通功能页不得借用这些颜色装饰卡片、导航或统计信息。

浅色主题是中性偏冷的工作面；深色主题保持相同信息层级与语义映射。Reader 内部遗留的表面 token 名称不构成额外主题，也不得扩展为第三种外观选择。

## Typography

ui 字体服务导航、按钮、输入、状态、菜单和信息密集型界面；reading 字体只服务文章标题、英文原文、译文及阅读性的少量标题。产品控件不使用 display 式排版，不用全大写或宽字距来伪造层级。

正文阅读列保持适合长时间阅读的行长；文章标题可使用更强的衬线对比，但不得压缩字距或在窄宽度溢出。界面标题采用稳定的固定层级，不使用随视口剧烈缩放的营销式字号。

**The Reading-First Rule.** 一页中最醒目的文字必须是文章标题或当前阅读内容；菜单、状态与 AI 操作永远不能取得同等视觉重量。

## Elevation

默认使用平面表面、色调差和细分隔线建立层级。阴影是结构性工具：浮层、sheet、dialog、临时工具面板和被抬升的交互状态才可使用；普通列表项、设置行和正文卡片不叠加装饰性重阴影。

**The Quiet Surface Rule.** 边框与宽阴影不能同时作为装饰叠加在同一普通卡片上。阅读界面不使用玻璃拟态、发光、条纹、网格背景或模拟材质纹理。

交互转场采用现有快速、短距离的状态变化。不得用页面入场编排、持续旋转、弹跳或自动展开来表现解析过程；prefers-reduced-motion 下必须保留即时或交叉淡化的等价状态。

## Components

### App shell and reading column

左侧资源轨道承载搜索、新解读、最近阅读和阅读资产；它稳定、窄、低噪。中央列是文章与理解信息的唯一主舞台。右侧只在 Ask 或其他临时工具真正打开时出现，并保持正文仍可阅读。

### Buttons, icon buttons, and fields

公共按钮使用受限 variant、size 与 density；icon-only 控件必须有可访问名称。所有可交互控件至少呈现默认、hover、focus-visible 与 disabled；触发真实异步动作时，等待状态必须保留动作语义与可访问名称，不能只用无标签 spinner。

主要行动稀少且明确；次要、outline、quiet 与 ghost 行为不得伪装成主行动。输入和搜索字段提供可见 label 或等价名称，错误不能只依赖颜色。

### Article status

文章标题下的 badge 只呈现一句友好总览：“解析中”“解析完成”“等待继续”“需要确认”或“解析遇到问题”。它不显示内部执行名、百分比、模型或诊断。

更多菜单提供“文章状态”入口，进入二级界面后才展示面向用户的细节与必要操作。状态不能以顶部 alert、连续 toast、自动弹出面板或抢占滚动的页内提示出现。

### Candidate confirmation

确认阅读内容属于输入流程，不属于 Reader。短文以全文预览为默认；长文和多页文件以内容结构、必须查看的高影响风险点及按需完整预览为默认。普通提示不阻塞，只有高影响风险点必须逐一查看后才允许“确认并开始阅读”。

确认 dialog 或 review surface 应解释用户将获得什么阅读内容，而不是暴露解析管线、置信度字段或内部对象名。它可以在长文时扩展为输入流程内的全屏 review surface，但不得伪装成已进入阅读的 Reader 页面。

### Ask sidecar

Ask 是当前文章的右侧助手，不是全局聊天页面。有选区时默认带入当前词句或段落；无选区时带入本文。sidecar 持续显示“这句话 / 这一段 / 本文”的当前范围，并允许明确切换。

Ask 的回答涉及原文时必须能回到文章锚点。回答不能直接改写原文；需要加入阅读页的内容由用户确认后以补充内容出现。文章引用尚未准备好时，Ask 继续保持可用，但不伪造引用或将该状态渲染为页面故障。

## Do's and Don'ts

### Do:

- **Do** 让文章、当前任务和下一步行动先于系统过程出现。
- **Do** 用固定左轨、居中阅读列、细分隔线和受控留白建立稳定秩序。
- **Do** 把解析详情、继续操作和可追溯状态收纳进“文章状态”二级菜单。
- **Do** 只在用户必须确认、继续或重试时使用 dialog，并让关闭后的事项可再次找到。
- **Do** 让 Ask 从选区或本文上下文启动，并始终标明它正在讨论的范围。
- **Do** 使用现有 token 和公共 primitives；新页面不得自行引入原始色值、私有阴影或页面专属 hover recipe。

### Don't:

- **Don't** 把 Reader 做成 SaaS 后台、可拼装控制台、Word/WPS 式编辑器或 NotebookLM 式多资料工作台。
- **Don't** 用顶部 alert、过程 toast、任务时间线、技术进度条或“AI 正在思考”动画打断阅读。
- **Don't** 在普通界面显示 workflow、run、job、模型、token、provider、失败码或原始诊断。
- **Don't** 把 Ask 做成 Reader 中的无上下文全局聊天线程，或让聊天记录取代文章与锚定理解。
- **Don't** 把词汇、语法等文章语义色扩散为普通页面的装饰色。
- **Don't** 引入第三种主题、暖黄旧化背景、纸张纹理、拟物便签、玻璃拟态、霓虹发光、装饰网格或厚重卡片阴影。
