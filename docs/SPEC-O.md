# SPEC-O · 报表中心 + 筛选订阅（分析环收尾 + 拓客自动化深化）

> 立项：2026-10-31 W37 · Agent B 认领。补 PRODUCT_GAP §一④"数据简报/业绩分析"
> （业绩排行榜归 Agent A 的 SPEC-N，本 SPEC 不做 owner 维度报表避免撞车）
> + 剑鱼/阿里云标讯产品的"按筛选条件订阅"标配（SPEC-M 调研报告 P0 项）。
> 文件边界：api/reports.py（新）/app/reports/page.tsx（新）/governance.py（我的）/
> bulletin.py（我的）/signals page（我的）/SubRulesPanel（我的）；
> 共享文件仅 main.py cron 注册一段追加 + AppShell ADMIN_NAV 一行——**零 jobs.py 改动**。

## O1 报表中心

### 后端 app/api/reports.py（新文件，全部只读聚合）
- `GET /api/reports/weekly?offset=0`：按需生成某周经营周报（offset=0 上周、1 上上周）
  - KPI（本周 vs 上周环比）：新增企业/新增信号/新增线索/跟进次数/赢单数+赢单金额
  - 漏斗：线索状态分布（复用 analytics 口径）
  - Top 标讯：该周金额 Top10（复用 bulletin 查询口径）
  - Top 新客户：该周新建线索按综合分 Top10（名称/来源/评分/归属企业名）
- `GET /api/reports/weekly/export?offset=0`：同数据 CSV 下载（分节：KPI/漏斗/Top标讯/Top客户）
- 定时：每周一 08:30 生成上周周报摘要 → notify_dispatch(admin, kind="report")
  （email 渠道走 J4 已就位的 emailengine；user.email 未配置时优雅 skip）
  ——worker 函数放 reports.py（reports_weekly_sweep），main.py `_register_cron` 追加，
  **jobs.py 不动**（Agent A 高频修改中，跨文件依赖走 api 层先例）

### 前端 app/reports/page.tsx（新页，ADMIN_NAV「报表中心」）
- 周切换（上一周/下一周/本周偏移显示）+ KPI 环比卡 + 漏斗条 + 两张 Top 表
- 「导出 CSV」按钮；空周真实显示零

## O2 筛选订阅（subscription_rule 扩 kind="filter"）

- governance.py：SubRuleIn.kind 白名单加 "filter"；value=筛选 JSON
  `{type?,industry_key?,province?,q?,days?}` 服务端校验字段白名单与长度（≤200 字符）
- 标讯中心页加「订阅当前筛选」按钮（把当前筛选序列化存规则；409 已存在提示）
- run_subscription_rules：kind=="filter" 走 Python 侧匹配（增量信号 ≤500 条拉回后
  逐条按快照判断 type/province/industry/q contains/days——复用 bulletin 的参数语义，
  无 SQL 技巧）；通知文案带条件摘要
- SubRulesPanel：filter 规则显示条件摘要标签
- **无需表结构变更**（kind/value 均为现有 TEXT 列，兼容存量 keyword/region 规则）

## 测试

- test_spec_o.py：周报 KPI/环比/Top 表正确性（种子数据双周对照）；CSV 导出可解析；
  filter 订阅匹配命中/豁免/幂等；settings 面板字段校验
- 全量门禁 + E2E（E2E_PORT=8017）

## 边界

- 不做 owner 维度排行（SPEC-N 领地）、不做 xlsx（CSV 够用，零新依赖）、
- 不做报表历史存档表（按需生成幂等，成本毫秒级）
