# SPEC-C · 功能扩展包一（订阅监控/批量操作/导出/相似推荐）

> 立项：2026-10-25 · 响应"功能太少"——对照头部拓客平台补销售日常刚需
> 原则：全部基于已有数据与通道，不发明数据；每项带测试

## 功能清单

### F1 监控订阅 → 通知中心（激活既有 subscription 表）
- DDL 新增 `notification`：id/user_id/kind/title/body/ref_type/ref_id/read/created_at，
  UniqueConstraint(ref_type, ref_id, user_id) 防重
- 语义：关注某企业后，其**新信号**（中标/公告）生成通知。扫播式生成（jobs.generate_notifications）：
  挂在采集任务完成回调 + 每日 04:05 定时，幂等可重跑
- API：GET /api/notifications?unread=1（含 unread_count）/ POST /{id}/read / POST /read-all
- 前端：顶栏铃铛（未读角标）+ 下拉列表；订阅入口=企业抽屉"关注此企业"

### F2 批量转线索 + 导出 CSV
- POST /api/enterprises/batch-lead {ids:[]} → 逐个套用单条转换校验（同行拒绝/已存在跳过），
  返回 {converted, skipped_competitor, existed}
- GET /api/enterprises/export?<同列表筛选> → CSV 流（SQLite 路径同款筛选，上限 5000 行）
- 前端：列表行勾选 + 浮动批量条（批量转线索）+ 工具行"导出 CSV"按钮（带当前筛选）

### F3 相似企业推荐
- GET /api/enterprises/{id}/similar → 启发式：同 industry_key 且非同行 → 同省 → 意图分近邻，LIMIT 5
- 前端：企业抽屉底部"相似企业"区（点击可跳转）

## 门禁
新增 test_features.py：订阅→通知生成幂等/批量转换汇总/导出 CSV 行数/相似排序。全量回归 + build。
