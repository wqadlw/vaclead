# SPEC-AX · 应收账款守护轮（Agent B 认领）

> 立项：2026-11-06 W58 · 健康检查式盘点发现 AD 轮契约承诺的"回款分期
> 逾期 cron"从未落地（due_date 过期无标记/无提醒/无应收视角）——
> 对设备销售商，应收逾期=直接的现金流风险，这是 Playbook"信任运营"
> 层的最大欠账。

## AX1 回款逾期扫描（cron 06:25）

- `run_payment_overdue_sweep()`：pending 且 due_date < 今日 → status=overdue
  （幂等：仅 pending→overdue 单向）+ 自动建 Todo「回款逾期：{企业}
  {label} {金额}万」（owner=合同 lead 的 enterprise 对应线索负责人；
  open 唯一索引防重建）——逾期分期从"数据状态"变成"待办动作"

## AX2 应收视角露出

- dashboard forecast 区新增「应收账款」行：逾期金额合计 + 30 天内到期
  合计（active 合同的 pending 分期，万元）——现金流风险一眼可见
- contracts 前端页：overdue 分期红色 Tag（枚举已在模型）

## 边界

- 不做逾期罚息计算（合同条款各异，P2）
- 提醒对象=线索负责人（lead.owner_id），无负责人则 admin
