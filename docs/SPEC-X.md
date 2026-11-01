# SPEC-X · 分析环深化：转化率 + 来源 ROI + 跟进健康度（Agent B 认领）

> 立项：2026-11-01 W42 · PRODUCT_GAP §三 P0-3 遗留半项（"来源 ROI（各来源企业的
> 线索转化对比）"当时只做了来源分布计数）。数据可行性：audit_log（W38 起）可重放
> lead status 流转边；来源/last_followed_at 均在库。文件边界：analytics.py/reports.py/
> page.tsx 均为 Agent B 领地；零表结构变更。

## X1 阶段转化率（audit_log 重放法）

- 数据源：audit_log WHERE entity='lead' AND action='update' AND changes_json 含
  "status"——重放 [before, after] 边计数（W38 起的数据；口径标注"近 30 天流转"）
- **口径（调研校准，Pipedrive/HubSpot 流转事件口径）**：转化率(S→T)=到达过 S 且
  到达过 T ÷ 到达过 S；实现为该边发生数 ÷ (曾从 S 转出数 + 当前停留 S 数)。
  **公海回流口径**：pool→following 回流计入 following 的进入数（每轮回流=一次真实
  商机机会，分母含回流轮次——拓客平台语义下合理，文档注明）
- 端点：GET /api/analytics/conversion
- 数据不足时返回 {"status": "accumulating"}（前端显示"数据积累中"）

## X2 来源 ROI（first-touch 快照法）

- 按企业 source 分组：线索数 / 赢单数 / 赢单金额 / **赢单率**（won/(won+lost)）
- 端点：并入 /api/analytics/conversion 或 reports
- 前端：报表中心新增「来源转化对比」表（来源/线索/赢单/金额/赢单率）

## X3 跟进健康度

- 指标：活跃线索（following/intent）中 last_followed_at < now-7d 的计数与占比；
  超 14d 二级警示
- 端点：并入 /api/dashboard/summary（工作台已有作战清单，互补不重复）
- 前端：工作台健康度小卡（与预测卡同排）

## 测试与门禁

- test_spec_x.py：转化率重放（种子 audit 流转边）、来源 ROI 计算、健康度阈值边界
- 全量门禁 + E2E（E2E_PORT=8017）

## 边界

- 不做多触点归因模型（first-touch 够用，P2 再说）
- 不动 SPEC-N 领地（leaderboard 归同事）
