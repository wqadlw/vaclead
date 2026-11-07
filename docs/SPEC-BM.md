# SPEC-BM · 老户雷达轮：负责人自动信号推送（Agent B 认领）

> 立项：2026-11-06 W59 · 新信号落地时，系统从不主动告诉"这个客户的负责人"
> ——订阅要手动配、规则要手动建。而"我的在管客户出现在新公告里"应该
> **零配置自动推送**：这是拓客系统对销售最基本的忠诚。补上它，
> 机会雷达就完整覆盖"在管客户"这一最高价值人群。

## BM1 负责人信号推送扫描（cron 每 30 分钟）

- `owner_signal_sweep()`：扫水位以来新信号 → join 线索
  （status ∈ following/intent/won）→ notify_dispatch 通知线索负责人
- **通知标题语义分层**：
  - won 客户 → 「老户新动向」（复购/替换机会，最优先）
  - intent 客户 → 「意向客户新信号」（临门一脚）
  - following 客户 → 「跟进客户新信号」
- 正文 = 信号标题 + 企业名 + 证据句（evidence_claim 截断）
- **去重**：Notification 唯一约束 (ref_type='owner_signal', ref_id=signal.id,
  user_id=owner) 预查——重扫/多负责人各推一条
- 水位：sync_watermark 'owner_signal'（=cron 注册名；BV 体检修正旧名错位）

## BM2 附带修正

- notify 时 body 不含手机号明文（联系人数据在详情页，通知只导流）

## 边界

- 每信号每负责人一条（不按信号类型拆多条轰炸）
- 公海（无 owner）/已删企业不推
- email 渠道依赖 user.email 配置（未配优雅降级为 in_app）

## 落地记录（2026-11-07 W59）

- `jobs.owner_signal_sweep()`：游标走 stats_json.last_signal_id（信号 id 单调递增）。
  **首跑基线模式**——无水位行时只记游标不推存量（防一次性轰炸）；
  失败轮游标丢失 → 全量重扫，Notification 唯一约束预查兜底幂等。
- 同 (信号, 负责人) 多条线索并存取最高优先状态（won > intent > following）。
- 注册：`_register_cron("owner_signal", …, "*", "*/30")`（minute 表达式支持，
  `_register_cron` 注解已放宽 `int | str`）。
- 测试 `test_spec_bm.py` 3 条：基线不轰炸 / 三状态分层推送+公海已删不推 /
  重扫幂等+游标丢失恢复。回归 307 全绿。
- 注：SYNC 水位由 `_run_job_with_watermark` 落库（返回 dict 即 stats），实现内不重复写。
