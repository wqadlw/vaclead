# SPEC-AZ · 触达覆盖与私有案例轮（Agent B 认领）

> 立项：2026-11-06 W58 · 27k 池的"覆盖度量"与"最强信任资产"补完。
> 调研：未触达线索堆积需按阶段监控（New→Contacted→Called）+ 每周触达率；
> 私有成交案例是比公开招标证据更强的提案资产（42% 买家称案例最有影响力）。

## AZ1 触达覆盖筛选（企业列表）

- `GET /api/enterprises?call_status=untouched|touched`：
  - untouched = 有可拨电话 且 全部联系人 call_result=pending（**还没打过的金矿**）
  - touched = 任一联系人已标记结果
- Quick picks：「未触达（有电话）」「已触达」——打电话的覆盖率一眼可管

## AZ2 私有成交案例库（case_study 新表）

- 字段：enterprise_id nullable/industry/title/content/year/created
- CRUD：POST/GET /api/cases（admin；按 industry 筛选）
- **提案优先级**：print 视图私有成交案例节在前（"昊志真实交付"），公开
  招标案例节在后（"行业公开记录"）——两层证据互补

## 边界

- 案例内容为手工录入（真实交付后由销售填写，不虚构）
- untouched 判定=电话联系人口径（无电话企业不进该筛选）
