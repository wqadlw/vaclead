# SPEC-AD · 客户全生命周期：合同 + 回款 + 续约（Agent B 认领）

> 立项：2026-11-04 W48 · 赢单之后完全空白——Lead.won 是当前平台的终点，
> 头部 CRM 的起点。补齐 **合同→回款→续约** 后销售闭环才真正完整。
> 契约待调研回传后定稿。

## AD1 合同管理（contract 新表）

- 核心字段：lead_id(FK won 态)/quote_id(FK nullable)/name/amount_wan/
  status(draft→active→completed|terminated)/start_date/end_date/
  payment_terms/owner_id/remark
- 与 Quote 关系：Quote.won 是签合同的触发（但合同可独立于报价创建）
- 端点：CRUD + 状态机 + 按客户/状态/金额筛选

## AD2 回款计划（payment_milestone 新表）

- 典型分期：预付款(30%) → 发货款(40%) → 验收款(20%) → 质保金(10%)
- 字段：contract_id FK/seq/label/amount_wan/due_date/status(pending→paid|
  overdue)/paid_date
- 自动计算：合同额 × 比例 = 各期金额（前端拖滑/输入比例自动分）
- 逾期标记：每日 cron 检查 due_date < today 且 status=pending → overdue

## AD3 续约提醒自动化

- 合同 end_date - 30 天 → 自动创建 Todo「合同续约提醒」（owner=合同 owner）
- 每日 cron 检查即将到期合同

## AB4 前端

- /contracts 列表页 + 详情（回款进度条 + 分期表 + 状态流转）
- 合同数据进分析环：回款金额入加权预测
