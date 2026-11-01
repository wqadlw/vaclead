# SPEC-Y · 批量操作轮：批量打标签 + 批量退公海（Agent B 认领）

> 立项：2026-11-01 W43 · 企业列表已有行多选+批量转线索（SPEC-C F2）——销售日常
> 高频的**批量打标签**与**批量退公海**缺失。零表结构变更（tags 列 W39 已有）。

## Y1 批量打标签

- 端点：POST /api/enterprises/batch-tags {ids: int[], tags: string[], mode: "append"|"replace"="append"}
  - append：对每家企业**合并**现有 tags（去重 trim）后写回——不覆盖销售已有标签
  - 校验复用单家语义：≤8 个/家（append 后总数）、单标签 ≤20 字
  - 返回 {updated: n, skipped: [{id, reason}]}（不存在/超上限的部分成功明细）
- 上限：单批 ≤200 家（同 batch-lead 惯例）

## Y2 批量退公海

- 端点：POST /api/leads/batch-pool {ids: int[]}（ids=lead ids）
  - 语义与单条退公海一致：status→pool、清 owner_id/claimed_at、记 updated_at
  - 只处理 status ∈ {following, intent} 的（won/lost/pool 跳过，进 skipped 明细）
  - 返回 {moved: n, skipped: [{id, reason}]}
- 前端入口在企业列表操作条（选中→「批量退公海」→ 确认弹窗）——语义上操作的是
  选中企业的线索；无线索的跳过

## Y3 前端

- 操作条扩展：已选条加「打标签」「批量退公海」按钮 + 标签 Modal（输入+聚合建议）
- 部分成功反馈：Toast 汇总（"成功 N 家"）+ 失败明细 Modal（可展开）

## 测试

- test_spec_y.py：batch-tags append 语义（保留已有标签）/上限校验/部分成功明细；
  batch-pool 状态守卫/owner 释放/非 following 跳过；权限（sales 只能操作自己线索？——
  沿用公海领取的对称语义：退公海=放弃自己的线索，admin 可操作全部）
- 全量门禁 + E2E（E2E_PORT=8017）

## 边界

- 不做批量删除企业（危险操作，审计语义重——P2 单独评估）
- 不做批量修改字段（高级筛选联动批量更新——P2）
