# SPEC-V · 客户经营件：企业备注 + 标签系统（Agent B 认领）

> 立项：2026-11-01 W39 · 用户持续要求大力发展功能。CRM 日用刚需（twenty/espocrm/
> 探迹全系标配），平台此前的"备注"仅存在于跟进记录（Followup，绑定线索）——
> **企业主体的自由备注与自定义标签缺失**。零外部依赖。

## V1 企业备注（enterprise_note 表）

- 表：id/enterprise_id(FK CASCADE)/user_id(FK)/content(TEXT ≤2000)/created_at
  + Index(ix_note_enterprise)
- 端点（enterprises.py）：
  - GET /enterprises/{id}/notes：倒序全量（量级小）
  - POST /enterprises/{id}/notes {content}：本人落款；审计不动（内部笔记非关键实体变更）
  - DELETE /notes/{nid}：本人或 admin
- 情报页档案页签底部「备注」区：列表 + 输入框即时添加 + 本人可删；
  **不并入信号时间线**（时间线=客观事实流，备注=主观工作笔记，分色分区防混淆）
- 权限：admin 全看；sales 只看自己+公海企业？——**备注跟随企业可见性**
  （企业列表同款语义：不额外收口，情报页本身可达即可见——与 contacts 一致）

## V2 企业标签（Enterprise.tags JSON 列）

- 列：`tags TEXT NOT NULL DEFAULT "[]"`（JSON 数组，幂等 ALTER + OPERATIONS §8）
- 端点：
  - PUT /enterprises/{id}/tags {tags: string[]}：全量替换；单标签 ≤20 字、≤8 个、去重 trim
  - GET /api/tags：全库 DISTINCT 聚合（标签云/自动补全建议，Python 侧 Counter）
- 筛选：企业列表 `tag=?` 参数（JSON 列 LIKE '%"tag"%' 参数化匹配——量级允许；
  带参强制 SQLite 路径同 SPEC-M 模式）
- 前端：
  - 情报页头部：标签行（Tag 可删 + 输入即时添加，自动补全 GET /api/tags）
  - 列表行：标签徽标（最多 2 个 + n）
  - 筛选条：标签下拉（GET /api/tags 聚合）
- 导出 CSV 增「标签」列

## V3 测试与门禁

- test_spec_v.py：备注 CRUD/权限（他人删 403 admin 可删）；tags 替换/上限校验/
  标签筛选命中；GET /api/tags 聚合去重；导出含标签列
- 全量门禁 + E2E（E2E_PORT=8017）

## 边界

- 不做 @提及/附件（espocrm 帖子型 Note——P2）
- 不做标签合并治理工具（防爆炸靠 ≤8 个上限 + 补全建议；P2 观察后立）
- 完整度不含备注/标签（完整度=工商资料概念，W38 已定义）
