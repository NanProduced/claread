# Composed Components

本目录用于承接 Claread Web 的无业务组合控件，例如 `SearchField`、`InfoCard`、`StatCard`、`PageHeader`。

当前已包含具体组合组件：`SearchField`、`InfoCard`、`StatCard`、`SectionCard`、`ListRow`、`EmptyState`、`PageHeader`、`FilterBar` 与 `TopActionBar`。

这里的组件不得耦合页面业务数据或 Reader 画布/锚点逻辑。它们的视觉与交互契约以 `apps/web/DESIGN.md` 为准；稳定组件应各自维护实现、story 和简短 README。
