# SPEC-AE · 客户健康分与生命周期自动升降级（Agent B 认领）

> 立项：2026-11-04 W49 · PRODUCT_GAP 第四轮复查 Top1/Top2。
> 研究共识：自动化健康分可提前约 63 天预警流失（人工仅 11 天）——工业设备
> 复购周期长（1-3 年），赢单不是终点，续约与复购才是利润大头。
> 吸收：HubSpot lifecycle stage + Gainswright/N-B nxt 健康分思路（行为加权，无 LLM）。

## AE1 健康分模型（backend/app/health.py）

`compute_health_score(session, lead) -> 0-100`，基线 50，行为加权：

| 信号 | 分值 |
|---|---|
| 近 30 天有新信号 | +30（每条 +5 封顶 30） |
| 近 7 天有跟进 | +20（每条 +10 封顶 20） |
| 合同履约中（active） | +20 |
| 超 7 天未跟进 | -20 |
| 超 30 天无任何信号 | -30 |

全部基于已有 signals/followups/contracts 数据，零新增采集成本。

## AE2 生命周期自动流转

`auto_lifecycle(session, lead)`：

- `Lead.status == "won"` 且 stage ∉ {customer_success, at_risk, churned} → **customer_success**（一次性，赢单 API hook + 批量回填双路径）
- customer_success 且健康分 < 30 → **at_risk**（流失预警）
- at_risk 且健康分 ≥ 60 → **customer_success**（回升）
- 其余不动

> 实现注记：赢单判定必须看 `lead.status`（"won" 是状态机值），不能看
> `lifecycle_stage == "won"`（生命周期枚举里没有 won，首轮实现踩过此坑，测试拦截）。

## AE3 批量刷新 cron

- `health_refresh` 每日 06:15（在 contract_renewals 06:10 之后），全量活跃线索
  （following/intent/won）重算健康分 + 生命周期
- 历史赢单线索首次 cron 自动升入 customer_success（无需手工回填）

## AE4 露出层

- **leads API**：序列化新增 `health_score` / `lifecycle_stage`；`GET /api/leads?at_risk=1`
  预警视图（lifecycle=at_risk 或 health_score<40）；`?lifecycle=` 精确筛选
- **dashboard KPI**：新增 `at_risk` 卡（活跃线索中预警数）
- **前端**：线索列表「健康」列（分数色阶 green/orange/red + 生命周期 Tag）；
  「流失预警」tab；工作台第 5 张 KPI 卡

## 边界

- 健康分不含邮件打开/网页访问等行为（无跟踪基础设施），P2 邮件营销轮补充
- won 之前线索（new/following/intent）不参与生命周期升降级，只有健康分
- churned 需人工确认（自动只降 at_risk，不自动 churn）
