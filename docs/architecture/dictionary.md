# 词典能力架构

本文只描述 Claread 当前词典能力的后端事实、数据来源、查询链路与增强方向，不讨论某个客户端的具体 UI。

## 目标定位

Claread 的词典能力不是“通用在线词典替代品”，而是阅读器内的语境化解释服务。它要解决的是：

- 点词后在当前语境下快速命中合理词条
- 支持单词、短语、词形变化和部分片段类查询
- 为生词本、复习和 Ask Claread 提供统一词条来源
- 在正式词典缺失时用 AI 做增强和兜底

当前数据基础来自开源词典 DMX/MDX 解析后落入 PostgreSQL，因此不具备有道、柯林斯、韦氏那种原生商业词典的完整深度。现阶段重点不是“追平专业词典”，而是尽量把现有数据组织好、查询准、兜底稳。

## 当前数据资产

### 核心表

| 表 | 职责 |
|----|------|
| `dict_entries` | 词条详情表，保存解析后的正式词条或可保留 fragment |
| `dict_lookup_targets` | 查询映射表，保存归一化查询词到候选词条的关系 |
| `dict_redirects` | 重定向表，保存 alias / redirect 到词条键的映射 |
| `dict_ai_candidate_entries` | AI missing fallback 候选池 |

### 当前数据规模

当前仓库文档记录的基线数据量为：

- `dict_entries`: 253300
- `dict_lookup_targets`: 1014676
- `dict_redirects`: 848873
- `exam_tags` 非空词条：20239

这些数据来自已有 PostgreSQL 资产恢复结果，不应轻易重导或替换。

### `dict_entries` 当前字段重点

`dict_entries` 当前承载：

- `display_headword`
- `base_headword`
- `homograph_no`
- `phonetic`
- `meanings_json`
- `examples_json`
- `phrases_json`
- `sections_json`
- `raw_html`
- `parse_version`
- `exam_tags`

其中最关键的是：

- `meanings_json`
  - 主释义结构，按词性分组，definition 下可挂例句与翻译
- `examples_json`
  - 词条级例句汇总
- `phrases_json`
  - 短语与简释
  - 当前没有独立例句、独立 entry id 或可跳转元数据
- `exam_tags`
  - 已标注的一部分考试标签
  - 覆盖仍不完整

### 当前数据质量现实

当前词典数据可用于阅读器内查词，但存在明显上限：

- 短语数据深度不足
- phrase 往往只有 `phrase + meaning`
- fragment 质量参差不齐
- `exam_tags` 覆盖有限
- 不同词条的例句与结构完整度不一致

因此词典服务设计必须接受一个现实：查询策略和候选重排的重要性，和底层数据质量同样关键。

## 查询服务链路

### API 入口

词典相关 API 位于 [services/api/app/api/routes/dict.py](../../services/api/app/api/routes/dict.py)：

- `GET /dict`
  - 查询单词或短语
  - 入参：`q`、`type=word|phrase`、`context_sentence`、`occurrence`
- `GET /dict/entry`
  - 根据 `entry_id` 获取完整词条
- `POST /dict/ai`
  - 词典 AI 增强
  - 当前支持 `context_explain` 与 `missing_fallback`

### 归一化层

[services/api/app/services/dictionary/service.py](../../services/api/app/services/dictionary/service.py) 先对 query 做统一归一化：

- 去前后空白
- 统一中英文引号
- 清洗特殊数字字符
- 处理部分缩写 alias
- 去掉头尾无效符号

这一步的目标不是语言学还原，而是减少输入噪声，提高 lookup target 命中率。

### Provider 查询顺序

当前 provider 是 [services/api/app/services/dictionary/providers/tecd3.py](../../services/api/app/services/dictionary/providers/tecd3.py)。

它当前的查询顺序是：

1. direct query
2. 有语境时的 phrase candidate 嗅探
3. phrase query 的 template canonicalization
4. lookup target / redirect / nlp imported target 命中
5. lemma fallback
6. not found

### 点词查询的设计

Claread 的点词查询不是单纯 `q=word`，还会带上：

- `query_type`
- `context_sentence`
- `occurrence`

这样做的目的有两个：

1. 对同一句中重复出现的词，尽量命中当前点击位置相关的候选
2. 对 phrase / fragment / 语境性表达，尽量从上下文生成更合理的候选

### phrase candidate 生成

在有 `context_sentence` 时，provider 会结合：

- spaCy
- phrase template 规则
- `occurrence`

生成一组 phrase candidates，用于：

- 把单词点击提升为短语候选
- 把自然表达规范化成词典模板

例如：

- `be there for you` 可能规范化成 `be there for sb`

### lemma fallback

当首轮查词没有命中、且查询是单词级时，系统会使用 [services/api/app/services/dictionary/lemma.py](../../services/api/app/services/dictionary/lemma.py) 做 lemma fallback。

当前特点：

- 仅对不含空格的 `word` 查询生效
- 使用 `lemminflect`
- 优先顺序：`NOUN -> VERB -> ADJ -> ADV`
- 用于处理：
  - 名词复数
  - 动词时态
  - 形容词比较级/最高级
  - 副词变体

这意味着当前点词查询已经不是“点击表面词形直接查一次”，而是：

- exact / phrase / redirect / nlp / lemma 多级组合查询

这也是 Claread 现阶段补足数据不足的关键路径。

### disambiguation

当候选不止一个时，系统不会强行返回一个词条，而是返回 `disambiguation`。

候选当前包含：

- `entry_id`
- `label`
- `part_of_speech`
- `preview`
- `entry_kind`
- `match_kind`
- `lookup_type`
- `candidate_kind`

这样客户端可以在词义冲突、词性冲突、phrase vs word 冲突、proper noun vs common word 冲突时先做选择。

### 缓存

Provider 当前使用词典缓存，cache version 为 `v5`。

cache key 会包含：

- source
- query
- query_type
- context hash
- occurrence
- strategy version

这说明当前缓存不是简单按 query 缓存，而是把语境型查词视为不同请求。

## 短语与搭配能力现状

### 当前已有能力

当前词典链路并不是完全不支持 phrase。

后端已有：

- `query_type=phrase`
- `lookup_type=phrase`
- phrase template canonicalization
- `backfill_phrases.py` 对 `phrases_json` 中隐藏短语写入 `dict_lookup_targets`
- fragment / phrase template 的 lookup target 回填

因此从“查询引擎能力”上说，Claread 已经具备 phrase lookup 基础设施。

### 当前不足

但 `phrases_json` 当前只提供：

- `phrase`
- `meaning`

没有：

- phrase 自己的例句
- phrase 自己的 `entry_id`
- phrase 到 canonical lookup target 的稳定映射元数据
- phrase 是否适合直接再查的置信信号

这会导致“点搭配再查词”虽然可能查得出来，但并不稳定，也不总能回到用户期望的目标。

## AI 增强能力

### `context_explain`

用于正式词典已命中，但用户仍需要“结合本文解释”的场景。

它依赖：

- 已有词条
- `context_sentence`
- `entry_id`

当前返回：

- summary
- best fit sense
- why here
- cue
- translation
- contrast
- learning tip

### `missing_fallback`

用于正式词典未命中时的兜底。

当前返回两类结果：

- `ai_entry`
- `ai_unresolved`

并且会把候选写入 `dict_ai_candidate_entries`，供后续人工审核或词库补录。

### AI 的边界

当前 AI 不是 canonical dictionary 的替代品，而是：

- 词典正式结果存在时，做语境解释
- 词典正式结果不存在时，做兜底补义

因此任何词典增强方向，仍应优先提升 canonical lookup 命中和候选质量，而不是把问题全部推给 AI。

## 当前客户端消费面

当前词典服务已经被多个客户端/模块消费：

- Web Reader 点词查询
- Web Reader 词典侧栏
- 微信小程序日读页与相关词典视图
- 微信小程序生词详情 / 学习卡
- Ask Claread 的 dictionary tools
- 生词本中的 `dict_entry_id` 依赖链

这意味着词典服务改造不能只按 Web UI 决策，必须考虑：

- API 契约兼容性
- phrase / lemma 查询语义是否跨端一致
- `dict_entry_id` 与词条稳定性

## 当前主要短板

1. 数据质量不均匀
   - phrase 深度不足
   - fragment 质量参差
2. `exam_tags` 覆盖不足
3. phrase 虽可查，但 phrase 数据本身缺少稳定二次跳转元数据
4. `vocabulary_book.dict_entry_id` 仍绑定 `dict_entries.id`
5. AI 候选池与 canonical dictionary 的回流机制还不完整

## 增强方向

### 1. 继续增强点词查询

优先级最高的不是 UI，而是继续提升 lookup 命中质量：

- 优化 phrase candidate 生成
- 优化 lemma fallback 的召回质量
- 优化 proper noun / common word 去噪
- 优化 disambiguation 排序

### 2. 补 phrase 数据能力

如果后续要认真支持“点搭配再查词”，需要逐步补齐：

- phrase 的稳定 lookup key
- phrase 到 canonical target 的可追踪关系
- phrase 是否应直接再查的标记
- phrase 的上下文说明或例句

#### 完整方案建议：让 phrase 成为一等查询对象

如果后续不满足于“前端把 phrase 文本再拿去试查一次”，而是希望 phrase 查询稳定、跨端一致、可持续补数据，建议按完整方案推进。

##### 目标

让词典中的 `phrase` 不再只是词条附属文本，而是具备：

- 可直接查找
- 可稳定回到 canonical 结果
- 可在 Web / 小程序 / Ask 中一致消费
- 可继续补充例句、语境与考试标签

##### 方案分层

1. **数据层**
   - 继续保留 `dict_entries.phrases_json` 作为原始附属信息
   - 额外为 phrase 建立更稳定的查询映射，而不是只依赖展示文本
   - 最低要求：
     - `normalized_phrase`
     - `lookup_type=phrase`
     - `match_kind=phrase|phrase_template`
     - phrase 对应的 source / source_entry_key / entry_id
   - 理想情况下，后续可评估独立 phrase 明细表，例如：
     - `dict_phrases`
     - `dict_phrase_examples`
     - 或至少在 `phrases_json` 中补齐结构化元数据

2. **查询层**
   - phrase 点击后不只是把原文字符串重新查询，而是优先走 phrase lookup target
   - phrase query 的排序应优先：
     - exact phrase
     - canonicalized phrase template
     - phrase redirect
     - 退化到 word/fragment 结果
   - phrase 查询结果应返回更明确的候选类型，避免 phrase 和普通单词候选混在一起时前端难以解释

3. **结果层**
   - phrase 命中后，客户端应拿到的不只是 `phrase + meaning`
   - 至少应有能力返回：
     - 主释义
     - phrase 类型
     - 上下文说明
     - 例句或来源句
     - 是否来自 canonical entry / AI fallback
   - 如果 phrase 实际映射到某个正式词条，也应该能显式说明当前展示的是：
     - 独立短语结果
     - 或短语命中后映射到的 canonical entry

4. **客户端层**
   - Web Reader、微信小程序、Ask Claread 应共享同一套 phrase 查询语义
   - 点击搭配、点击 phrase gloss、手动 phrase 搜索，都应落到同一种服务能力上
   - 不能让 Web 走 phrase lookup、小程序仍把 phrase 当静态文本

##### 改造范围

- 后端：
  - `services/api/app/services/dictionary/providers/tecd3.py`
  - `services/api/app/services/dictionary/schemas.py`
  - `services/api/app/services/dictionary/db_pg.py`
  - `services/api/scripts/import_tecd3.py`
  - `services/api/scripts/backfill_phrases.py`
- Web：
  - Reader 词典 rail
  - quick peek / inspect card 中的 phrase 入口
- 小程序：
  - `VocabDetailView`
  - `VocabStudyCard`
  - 日读页相关词典入口
- Ask：
  - dictionary lookup tool 的 phrase 行为需要保持一致

##### 难度与收益判断

- 难度：中到高
  - 不是单纯前端交互问题，核心在数据建模和 phrase 查询稳定性
- 收益：高
  - 一旦打通，阅读器中的短语能力会从“附属展示”升级为“正式词典能力”
  - 这对精读、考试阅读、搭配学习、生词复习都有长期价值

##### 推荐推进顺序

1. 先统计高频 phrase 的命中质量与失败类型
2. 明确 phrase 查询结果的最小契约
3. 补 phrase lookup target 与 phrase 元数据
4. 再让 Web / 小程序接入“点搭配再查词”
5. 最后再考虑 phrase 例句、phrase 专属 AI 解释等深层增强

### 3. 提升词库可持续补录能力

- 让 `dict_ai_candidate_entries` 真正服务词库完善
- 建立 AI 候选审核与回流路径
- 逐步修复高频缺词、脏 fragment、低质量 phrase

### 4. 提升 `exam_tags` 覆盖

当前 `exam_tags` 是用户感知很强但覆盖不完整的一层，后续仍需要专门补链路和回填脚本。

## 当前结论

Claread 现在已经不是“只有一个简单单词表”的查词系统，而是一个具备：

- canonical dictionary
- query normalization
- phrase candidate generation
- lemma fallback
- disambiguation
- AI context explain
- AI missing fallback

的多级词典服务。

它的真正瓶颈已经不只是“能不能查”，而是：

- 数据深度不够
- phrase 能力不够稳定
- 候选排序与回流机制还不够强

后续词典能力建设应优先沿着这三条线推进，而不是只停留在客户端展示层。
