# Implementation Plan

- [ ] 1. 重建后端 Reader 标注模型并删除旧聚合链路
  - 新增 `reader_notes` schema、service、route、tests，并实现 exact-hit note reopen 与 note text-only edit 规则
  - 将 `user_annotations` 收口为纯高亮模型，删除 note 语义、相关校验与测试
  - 将 `favorite_records` 收口为纯文章收藏，删除文本收藏 target、接口与测试
  - 删除 `/excerpt-assets` route、service、schema、tests 与相关 shared contracts
  - 补齐 schema reset / migration / cascade delete / baseline 校验
  - _Requirement: 1, 2, 3, 4, 5, 6, 12, 13_

- [ ] 2. 重构 Ask Claread 到新双模型
  - 重写 attachment / citation / trace 语义，移除 `history_lookup`、`record_excerpt_assets`、`user_excerpt_asset` 等旧能力
  - 保留 external stable analysis / supplement asset disambiguation
  - 将 AI 写高亮动作改为写入纯高亮模型
  - 将 AI 写笔记动作改为写入 `reader_notes`
  - 删除文本收藏与 `save_answer_note` 等旧动作语义，并重写对应测试
  - _Requirement: 3, 10, 11, 12_

- [ ] 3. 重构 Web Reader 标注 UI 与交互
  - 重构 `ReaderWorkbench` 状态组织，拆分 highlight state、note state、ask attachment state
  - 更新 `SelectionToolbar`，删除文本收藏和内嵌旧 note 编辑心智，保留新动作模型
  - 实现句侧 note rail、note card、单 note focus、focused quote projection
  - 更新 gutter / marker / route focus / bridge 语义，删除 asset-center 相关 UI 与状态
  - 删除 `/library/assets` 页面、BFF、types、tests，并改造 Ask 前端面板到新 contract
  - _Requirement: 2, 3, 4, 5, 6, 7, 8, 10, 11, 12, 14_

- [ ] 4. 收口小程序到新查看模型
  - 删除 `packageA/excerpts`、旧摘录入口、旧文本收藏与旧 note-writing 状态路径
  - 更新结果页为新高亮/notes 读取与 focused quote projection
  - 收口小程序 API client、storage、cloud sync，只保留文章收藏写入与 notes/hightlights 查看
  - 保持 Daily Reader 不变，仅确保本轮 analysis record 重构不污染其逻辑
  - _Requirement: 1, 2, 3, 7, 9, 12_

- [ ] 5. 清理文档、测试与残留语义
  - 删除或重写旧“用户学习资产 / 摘录资产 / 文本收藏 / 混合 annotation note”文档事实
  - 删除旧 asset-center、excerpt-assets、text favorite、mixed note/highlight 自动化测试
  - 增补新高亮、reader note、Ask write action、Web note rail 的验证覆盖
  - 做一轮仓库级搜索，确认不再残留面向产品语义的旧入口、旧命名、旧 contract
  - _Requirement: 1, 2, 3, 10, 11, 12, 14_
