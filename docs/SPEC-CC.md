# SPEC-CC · 通知中心与晨检行轮：雷达产出首屏收口（Agent B 认领）

> 立项：2026-11-07 W59 · 三条雷达+竞对推送的产出散在铃铛下拉里；
> 管理者/销售的"早上 10 分钟"需要一个收口：**通知中心页**（历史/
> 筛选/批量已读）+ **工作台晨检行**（雷达三数 + 直达链接）。

## CC1 通知中心页 /notifications

- 后端零新增（list 分页/未读筛选/单条已读/全部已读端点已齐）
- 页面：筛选（全部/未读）+ 分页 + 行点击标已读并跳转 ref 目标
  （ref_type→路由映射：signal/sub_signal/owner_signal→/signals、
  competitor_win→/competitors、report→/reports、proposal→/proposals、
  其余不跳）
- 未读行左侧 accent 竖条；kind 徽标（signal/report/system/watchdog）

## CC2 工作台晨检行（dashboard 顶部新增区块）

- GET /api/dashboard/radar-strip（analytics.py，current_user）：
  {competitor_wins_30d, proposals_window_now, contact_untouched}——
  复用 quality/reports 同口径（竞对中标数 30 天/窗口拟建/未触达联系人）
- 工作台首屏渲染三格横条（各带"去查看"链接），置于 KPI 卡上方
- 铃铛未读数保持既有轮询

## CC3 测试 tests/test_spec_cc.py

- radar-strip 数字口径（种竞对中标+拟建+联系人）
- 权限 current_user（销售也可看——晨检是全员场景）

## 边界

- 通知分页沿用既有端点（page/page_size）；不做通知偏好改动

## 落地记录（2026-11-07 W59）

- radar-strip 端点（analytics.py，current_user 全员口径，复用
  quality/reports 同源算法）；通知中心页零新端点（ref_type→路由映射：
  competitor_win→/competitors 等）；铃铛下拉加「查看全部 →」。
- 工作台晨检行：KPI 网格上方三格横条（竞对中标红/招标窗口 amber/
  未触达 neutral，各带直达链接）。
- **坑**：f-string 里嵌 JSX 花括号转义打架——改普通字符串逐行拼接；
  铃铛组件无 router（用 location.href）。
- 门禁：pytest 333 + build + E2E 8 全绿。
