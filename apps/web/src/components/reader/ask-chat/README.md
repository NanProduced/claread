# Ask Claread UI Layer

本目录是 Ask Claread 的业务包装层，负责把 `ai-elements` 组合成 Reader 内真正使用的对话 UI。

- 允许：业务文案、面板布局、空态、footer、Ask 专属样式包装
- 禁止：重复实现 markdown、tool trace、reasoning、prompt input 等底层能力

分层规则：

- 可复用 AI 能力放 `components/ai-elements`
- Ask Claread 专属组合放 `components/reader/ask-chat`
- 页面级状态和接口编排留在 `components/reader/AiWorkspacePanel.tsx`
