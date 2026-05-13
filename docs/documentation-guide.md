# Claread 文档管理指南

> **状态**: `CURRENT` | **最后验证**: 2026-05-13 | **下一检查点**: 2026-06-13
>
> 本文档是 Claread monorepo 的文档管理入口。用户要求"整理文档"、阶段里程碑后，或定时巡检任务触发时，agent 按此检查全库文档，防止过期信息导致开发漂移。

---

## 一、文档分布

文档按三级分布，agent 开发前先读离目标最近的 `AGENTS.md`。

```
根目录/               # 全局总纲：AGENTS.md、README.md、PRODUCT.md、DESIGN.md
├── docs/
│   ├── product/      # 产品定位、当前状态、竞品、设计上下文
│   ├── architecture/ # 架构总览、monorepo 边界、workflow、多端策略、词典
│   ├── design/       # 跨端设计 Agent 指令（AGENTS.md + README.md）
│   ├── development/  # 开发主线
│   ├── operations/   # 本地开发、测试、prompt 版本、模型配置、LangSmith
│   └── reference/    # 参考资料（非实现规范）
│       └── differentiated/  # 差异化分析：学术阅读、每日精读、各类考试
│
├── apps/miniprogram/ # 小程序：README + AGENTS + PRODUCT + DESIGN
├── apps/web/         # Web：README + AGENTS + PRODUCT + DESIGN + docs/
│   └── docs/
│       ├── design/   # UI 方向探索、Mockups
│       ├── tmp/      # 临时过程文档（任务后清理）
│       └── *.md      # 实施计划、API 审计、Reader IA、技术栈等
│
├── services/api/     # 后端：README + AGENTS
├── services/worker/  # Worker 预留（仅有 README.md）
└── packages/
    ├── design-tokens/  # 已落地：品牌资产、logo、icon
    └── (contracts/、shared-utils/ 预留，尚未落地)
```

**关键原则**：
- 全局文档（`docs/`、根目录）只写跨端事实；平台限制写到对应客户端目录。
- `tmp/` 目录只放过程文档，不作为长期事实来源。
- `reference/` 下是参考资料，供设计 prompt 和策略时参考，**不是必须实现的规范**。

---

## 二、文档状态标记

每份文档**建议**在顶部标注状态。新文档和重点文档优先标注，旧文档不强制补标。

| 标记 | 含义 | agent 行为 |
|------|------|-----------|
| `CURRENT` | 与代码一致，可信任 | 正常依据开发 |
| `DRAFT` | 计划/探索中，未落地 | 开发前与代码或负责人确认 |
| `TMP` | 过程性文档 | 不长期依赖，任务后清理 |
| （无标记） | 待确认 | agent 检查时判定应标为 CURRENT、DRAFT 还是 TMP |

**渐进引入规则**：
- 新建文档：必须标注状态。
- 重点文档（AGENTS.md、PRODUCT.md、DESIGN.md、架构文档）：优先补标。
- 其他旧文档：agent 检查时逐个判定，不强制一次性补标。
- 已被替代的文档直接删除，不标 `DEPRECATED`。

**冲突仲裁**：

1. 当前代码、数据库 schema、测试结果、实际命令输出优先于所有文档。
2. 文档之间冲突时，先看是否有代码事实可验证；有则以代码事实为准。
3. 只有在代码无法直接验证的产品/设计/规划问题上，才以 `CURRENT` 且日期新的文档为临时依据。
4. 仍冲突时，不自动拍板，标为 D0/D1 待用户确认。

---

## 三、文档整理 Checklist

用户要求"整理文档"或阶段里程碑后执行。

### 1. 全局检查
- [ ] 根目录 `AGENTS.md` 与 `README.md` 无矛盾。
      验证：AGENTS.md 的"目录边界"表与 README.md 的"代码结构概览"树形图描述一致。
- [ ] 根目录 `PRODUCT.md` 与 `DESIGN.md` 无矛盾。
      验证：PRODUCT.md 的品牌定位与 DESIGN.md 的视觉规则不冲突。
- [ ] `README.md` 中的文档索引链接是否可访问。
      验证：逐个点击 `docs/README.md` 真相源表中的文档路径，确认文件存在。
- [ ] `docs/architecture/monorepo-boundaries.md` 中的目录职责描述是否与实际目录一致。
      验证：文档中列出的每个目录是否实际存在；实际存在的目录是否在文档中有描述。
- [ ] `docs/development/mainline.md` 中的阶段计划是否仍与当前推进阶段匹配。
      验证：文档中"当前阶段"描述与 `docs/product/current-state.md` 一致。

### 2. 客户端文档检查
- [ ] 各端 `AGENTS.md` 中的验证命令是否仍可用。
      轻量验证：检查命令是否仍存在于根 `package.json` 或对应 package scripts。
      深度验证：按需执行小程序 `pnpm miniprogram:build`、Web `pnpm web:typecheck` 等命令。
- [ ] 客户端 `PRODUCT.md` / `DESIGN.md` 是否与当前实现功能一致。
      验证：PRODUCT.md 列出的功能与客户端实际路由/页面匹配。
- [ ] **小程序**：检查是否有平台专属限制被误写入全局文档。
      验证：搜索 `docs/` 和根目录 `.md` 中出现"小程序"、"微信"的位置，判断是否应移至 `apps/miniprogram/`。
- [ ] **Web**：`apps/web/docs/implementation-plan.md` 进度是否与代码一致；`apps/web/docs/api-contract-audit.md` 是否跟进后端变更。
      验证：implementation-plan.md 中的 Wave 状态与 `apps/web/src/app/` 实际页面匹配。
- [ ] **Web**：`apps/web/docs/` 下的非 tmp 文档（`development-tracker.md`、`tech-stack-options.md`、`reader-ia.md` 等）是否有标了 TMP 但未放 tmp/ 目录的情况，或内容已过时。
      验证：检查这些文档顶部的状态标记和内容时效性。

### 3. 后端文档检查
- [ ] `services/api/AGENTS.md` 中的后端边界（如"不写小程序专属逻辑"）是否与代码一致。
      验证：搜索 `services/api/app/` 中硬编码的 `wechat_miniprogram`、`miniprogram` 等字符串，并判断是否属于 auth adapter / 兼容层。只有小程序假设泄漏到通用业务核心时才标为问题。
- [ ] `docs/operations/prompt-versioning.md` 中的 registry 路径和版本规则是否仍被遵守。
      验证：文档中的路径与 `services/api/config/prompts/` 实际目录结构一致。
- [ ] 后端新增的 `response_model`、枚举、错误态是否已在客户端契约文档中同步。
      验证：比对以下同步对：
      - `services/api/app/schemas/*.py` ↔ `apps/web/docs/api-contract-audit.md`
      - `infra/migrations/*.sql` ↔ `docs/architecture/overview.md` 数据模型部分
      - `services/api/app/api/routes/*.py` 新增路由 ↔ `apps/web/docs/api-contract-audit.md`

### 4. 共享包检查
- [ ] `packages/design-tokens/`（已落地）的实际内容与 `packages/README.md` 描述是否匹配。
      验证：README 中描述的目录结构与 `assets/brand/`、`assets/icons/`、`assets/logos/` 一致。
- [ ] `packages/contracts/`、`packages/shared-utils/`（预留，尚未落地）是否仍为空目录或不存在。
      验证：如果已创建目录且有内容，更新 README；如果仍为空，跳过。

### 5. TMP 与过期清理
- [ ] 扫描全库 `tmp/` 目录（当前仅 `apps/web/docs/tmp/`），判断任务是否已完成。
      验证：读取每个 `tmp-*.md` 的内容，检查其描述的任务是否已在代码中落地。
- [ ] 扫描全库 `.md` 文件中顶部标了 `TMP` 但未放在 `tmp/` 目录的文档（如 `development-tracker.md`）。
      验证：grep `TMP` 标记，确认文件位置与标记一致。
- [ ] 已完成的 TMP 文档：有效结论是否已压缩回正式文档？是则删除。
- [ ] 检查是否存在超过 2 周未被引用的 TMP 文件，标为 D2 待清理。
- [ ] 搜索全库 `.md` 中明显过时的日期或阶段描述（如"当前为 2026-04"），更新或删除。

**TMP 标记与 tmp/ 目录的关系**：
- `tmp/` 目录：存放临时过程文档的物理位置，文件名以 `tmp-` 开头。
- `TMP` 标记：文档顶部的状态声明，表示过程性文档。
- 两者应统一：标了 `TMP` 的文档应放在 `tmp/` 目录下；放在 `tmp/` 目录下的文档应标 `TMP`。
- 例外：`apps/web/docs/development-tracker.md` 等早期文档标了 TMP 但未放 tmp/，agent 检查时应标记为需迁移或清理。

---

## 四、Agent 执行流程

用户要求"整理文档"时，agent 按以下步骤执行。

### 分层执行

为控制 token 消耗，分两层执行：

**轻量检查**（每次可执行，约 10-15 个文件）：
1. 扫描文件存在性：guide 中提到的文档路径是否都存在。
2. 状态标记扫描：哪些文档有标记、哪些没有。
3. tmp/ 目录清理：检查 `apps/web/docs/tmp/` 下的文件时效性。
4. 过期日期扫描：grep 全库 `.md` 中的旧日期。
5. 命令存在性检查：只确认脚本名存在，不默认执行构建/测试。

**深度检查**（按需执行，需读 30+ 文件做交叉比对）：
5. 内容一致性：AGENTS.md vs README.md vs 代码实际目录。
6. API 契约同步：schemas/*.py vs api-contract-audit.md。
7. 后端边界合规：搜索硬编码的小程序假设。
8. 运行必要验证命令，如 `pnpm web:typecheck`、`pnpm web:build`、后端核心测试、小程序构建。

用户未指定时，默认执行轻量检查。

### 定时任务默认权限

定时执行时默认使用 **report-only** 模式：

- 可以读取文档、代码、目录结构和 git status。
- 可以生成问题清单、建议修改项和需要用户确认的问题。
- 不自动删除文件。
- 不自动移动文档。
- 不自动修改 `CURRENT` / `DRAFT` 状态。
- 不自动更新本文档的"最后验证"日期。
- 不自动运行耗时构建、测试或需要外部服务的命令，除非 automation prompt 明确要求。

只有用户在当前会话明确授权，或 automation 配置了 `auto-fix-safe-docs=true`，agent 才能执行低风险自动修复：

- 修正明显失效的相对链接。
- 把已完成且结论已合并的 `tmp/` 文件删除。
- 更新索引中已确认存在/不存在的文档路径。
- 修正已由代码事实证明的过期字段名、命令名或阶段描述。

即使开启自动修复，也禁止：

- 删除非 `tmp/` 文档。
- 删除 `AGENTS.md`、`PRODUCT.md`、`DESIGN.md`、README 或架构文档。
- 因为"超过 2 周"而直接删除 TMP；必须先确认有效结论已压缩回正式文档。
- 在没有代码/测试依据时把规划文档标为 `CURRENT`。

### 问题分级

使用 D 前缀（Documentation），避免与代码 P0-P3 混淆：

| 级别 | 含义 | 示例 |
|------|------|------|
| **D0** | 代码与文档冲突，可能导致开发漂移 | 文档说 Web auth 已落地，但代码没有对应路由；或通用业务核心硬编码小程序平台假设 |
| **D1** | 状态标记过期或缺失 | 文档内容已落地但仍标 DRAFT；重点文档无状态标记 |
| **D2** | TMP 堆积或位置不一致 | 标了 TMP 但未放 tmp/ 目录；tmp/ 下超过 2 周的文件 |
| **D3** | 措辞优化、链接失效 | README 中的链接 404；描述与实际目录略有偏差 |

### 执行步骤

1. 读取本文档。
2. 确定执行层级（轻量/深度）。
3. 按 Checklist 逐项检查，每项按"验证"方法执行。
4. 记录发现的问题，按 D0-D3 分级。
5. 默认只报告，不修改；如用户授权，才对低风险项执行修复。
6. 对 D0 给出明确证据、影响范围和建议修改位置。
7. 对 D1-D2 给出清理建议，标明是否需要用户确认。
8. 只有完整执行目标检查且无未处理 D0/D1 时，才更新本文档的"最后验证"日期。
9. 向用户汇报：问题数、已修数、待确认数、跳过项和未运行验证命令。
