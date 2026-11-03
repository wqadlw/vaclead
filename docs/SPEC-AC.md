# SPEC-AC · 销售粘性轮：我的周报 + 团队共享视图（Agent B 认领）

> 立项：2026-11-04 W47 · 销售粘性双件：**个人成绩单**（每周一邮件，销售离不开）
> + **团队共享视图**（统一管线口径，twenty view 服务端模式）。
> 与 SPEC-N 业绩排行榜（工作台卡）互补不重复；与 SPEC-O 全局周报并列（admin vs 个人）。

## AC1 我的周报（per-user）

- 端点 GET /api/reports/my-week?offset=0：同周窗口，维度全部按 owner_id=我
  （线索/跟进/赢单/金额/待办完成/停滞客户计数 + 我的新线索 Top 表）
- 前端：报表中心新增「我的周报」区块（页首，与全局周报并列 tab 切换）
- 数据源：Lead.owner_id / Followup.user_id / Todo.owner_id（均已入库，零表变更）

## AC2 周一个人周报邮件

- 每周一 08:40：为每个 active 用户生成个人周报摘要 → notify_dispatch（email 渠道，
  user.email 未配置优雅 skip）——**与既有全局 admin 周报并列不重复**
- worker：reports.py my_weekly_sweep；_register_cron day_of_week="mon", 8, 40

## AC3 团队共享视图（saved_view 新表）

- 表 saved_view：id/name/page("enterprises")/owner_id/user_ids(JSON 可见名单，
  空数组=全员可见)/filters JSON/hidden_cols JSON/dense/col_aggs JSON/created_at
- 端点（enterprises.py 或新文件 views.py——放 reports.py 不贴切，用 **api/views.py** 新文件）：
  - GET /api/saved-views：我的私有 + 团队共享给我的
  - POST /api/saved-views {name, page, filters, hidden_cols, dense, col_aggs, shared: bool}
  - DELETE /api/saved-views/{id}（本人或 admin）
- 前端：视图下拉加「团队共享」分组（图标区分，只读应用——不能改别人的共享视图，
  可"另存为我的"）；保存 Modal 加共享开关
- create_all 自动建表（零 ALTER）

## 测试与门禁

- test_spec_ac.py：my-week owner 隔离（A 的数据不进 B 的周报）；邮件 sweep per-user；
  saved-view 共享/私有可见性/删除权限
- 全量门禁 + E2E（E2E_PORT=8017）

## 边界

- 不做视图编辑权限（共享视图只读，P2 再议协作编辑）
- 排行榜归 SPEC-N（已由 Agent A 交付）
