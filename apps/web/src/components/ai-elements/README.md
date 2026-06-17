# AI Elements Boundary

本目录放 Ask Claread 共享的低层 AI 交互组件，只承载通用能力，不承载 Reader 业务语义。

- `ai-elements/*`：消息、thinking、tool、task、prompt input、markdown/mermaid 渲染等基础 AI 交互能力
- `reader/ask-chat/*`：Ask Claread 的文案、布局、业务包装层
- `reader/AiWorkspacePanel.tsx`：线程、上下文、SSE、proposal、证据与实际页面集成

约束：

- 不在这里加入 Reader 专属业务概念，例如 citation/source panel、article supplement proposal 文案、record context UI
- 新的业务态优先落到 `reader/ask-chat/*`，只有被多个 AI 场景复用时才下沉到这里
- 如果只是样式或布局差异，优先在上层包装，不要复制一份新的 `ai-elements` 组件
