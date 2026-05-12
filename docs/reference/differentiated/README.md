# 差异化阅读参考资料

本目录用于保存 Claread 对不同阅读场景的语言学、教学法和产品策略分析。它是参考资料，不是代码实现的唯一真相源。

## 迁移策略

- `academic_reading_differentiation.md`：保留为 academic 阅读参考；实现侧只抽取当前 academic v1 有效原则。
- `exam_gaokao_differentiation.md`、`exam_cet_differentiation.md`、`exam_kaoyan_differentiation.md`、`exam_ielts_toefl_differentiation.md`：可迁入本目录，作为 exam variants 的策略参考。
- `daily_reading_differentiation.md`：可迁入本目录，但必须标注为研究参考，不是实现规范。
- `exam_tem_differentiation.md`：TEM 变体暂缓，保留为参考，不进入当前 API / UI 范围。
- `academic_v1_dev_handoff.md`：不迁移；如果仍有有效结论，合并进 academic reference 或当前功能文档。

## 使用规则

- 开发 agent 不应直接按本目录改 API、schema 或 UI。
- 需要落地为功能时，先把结论转写到 `docs/architecture/workflow.md`、`services/api/docs/api-contracts.md` 或对应客户端文档。
- 如果文档中的策略与当前代码输出不一致，以当前代码和测试为准，再决定是否建立产品改造任务。

## 与实现文档的关系

| 需求 | 应查看 |
|------|--------|
| 当前 workflow 怎样运行 | `docs/architecture/workflow.md` |
| 当前 API 输出什么 | `services/api/docs/api-contracts.md` |
| 某个阅读模式的语言学依据 | 本目录 |
| 是否要新增 reading variant | 先写产品/架构任务，再改 schema、prompt 和测试 |
