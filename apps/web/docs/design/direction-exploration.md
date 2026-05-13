# Web UI 方向探索

本文记录 Claread Web 端 UI 方向探索。当前阶段用于选择方向，不作为最终设计规范。

## 目标

先设计功能页面，不先做完整 landing / 产品介绍页。首期页面包括：

- `/app`：粘贴即解读入口。
- `/app/reader/[id]`：核心 Reader。
- `/app/history`：历史记录。
- `/app/vocabulary`：生词本。
- `/app/review`：复习。
- `/app/profile`：用户、配额、设置。
- `/share/[id]`：分享页。
- `/app/export/[id]`：后续导出预览。

## 外部灵感提炼

### Dribbble: annotation / reading web app

可借鉴：

- 文档 + 旁注 / 评论区的清晰结构。
- annotation popover、side panel、floating toolbar 等模式。
- 低噪声的文档阅读容器。

风险：

- 很多方案偏 SaaS dashboard 或协作标注工具，容易让 Claread 变成后台。
- 卡片和侧栏过重会削弱“透读文章”的第一印象。

### Awwwards: interaction / editorial / premium material

可借鉴：

- 高级排版、材质、转场、光感和品牌记忆点。
- 让 Logo 的 lens / aperture 隐喻进入视觉语言。
- 分享页和导出页可以更有表现力。

风险：

- 不应把功能页做成作品集或 campaign 页。
- 不能让动效、材质和视觉表演抢正文。

## 候选方向

### A. Editorial Reader

最安静，单栏阅读为主，旁注以轻浮层、行内展开和小型 note slip 出现。适合作为默认 Reader。

优势：

- 符合“透读英文文章”的核心表达。
- 最不像 SaaS 和 AI dashboard。
- 易于先做功能 MVP。

风险：

- 桌面空间利用不充分。
- 需要高质量的 hover / selection / anchor 交互，否则能力感不够。

### B. Reading Studio

轻应用壳：左侧窄 rail 或顶部导航，中间文章，右侧轻旁注轨道。适合桌面端学习和高频回看。

优势：

- 承载历史、生词、导出、设置更自然。
- 能展示更丰富的语法和句子解析。
- 更接近 Web 端高保真体验。

风险：

- 容易变成 SaaS dashboard。
- 需要严格控制右侧栏密度。

### C. Grammar X-Ray Workspace

围绕句子结构可视化设计，突出主干、修饰、从句、非谓语、指代和逻辑关系。适合作为 Claread 标志性能力页或 Reader 内切换模式。

优势：

- 差异化最强。
- 适合做分享模板和社交传播。
- 能让 Claread 被记住。

风险：

- 首期实现复杂。
- 如果默认打开会打断普通阅读。

### D. Artifact Studio

导出 / 分享工作台。中间是长图、PDF 或笔记预览，侧边是少量模板和导出控制。

优势：

- 直接服务传播和沉淀。
- 可承接 Magazine Brief、Notebook Study、Grammar X-Ray 等模板。

风险：

- 不应先于 Reader 主链路。
- 需要后端和前端一起支持稳定 render/export。

## 初步建议

Web 首期默认采用 A + B 的混合：

- Reader 默认是 A：单栏阅读 + 轻旁注。
- 进入学习/分析模式时逐步显出 B 的右侧轻旁注轨道。
- Grammar X-Ray 作为 Reader 内高价值模式和分享模板，后续增强。
- Artifact Studio 放在第二阶段，不阻塞首期功能页。

这能同时满足“先做功能页面”和“Claread 品牌要有记忆点”。
