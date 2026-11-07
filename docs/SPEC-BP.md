# SPEC-BP · 拟建雷达轮：备案信号专属视图与培育流（Agent B 认领）

> 立项：2026-11-07 W59 · PRODUCT_GAP §5.3 真缺口 #1 的视图层收口。
> "拟建项目"是剑鱼的核心付费点（提前 3-7 天预告），我们粤/鄂两条备案
> 采集通道已跑（105 条 proposal 信号在库），只缺**专属视图与培育流**——
> "早"的三级货币化阶梯里，备案是最早的一级，配得上一个雷达位。

## BP0 数据地基：省份补齐（本次实测缺口）

- 现状：105 条 proposal 信号，enterprise.province 覆盖 **0**（粤/鄂采集
  建档时未填省）；行业仅 17/105
- 修法①：两扫描段（jobs.py GD/HB）建档时 province 为空则填——
  来源通道→省份是确定性映射（proposal_gd→广东省 / proposal_hb→湖北省），
  **不是发明数据**
- 修法②：`backfill_proposal_geo()` 幂等回填存量（按 source 条件），本次
  真库执行一次

## BP1 GET /api/proposals/center（新文件 api/proposals.py）

- overview：30 天新增 / 90 天新增 / **预计进入招标窗口**（备案日落在
  [今日-90 天, 今日-30 天] = 招标 1-3 个月后的承诺窗口）/ 省份分布 /
  行业分布
- list：province / q（单位或项目名）/ days / limit / offset 过滤；
  每行：备案时间、单位、地区、行业、来源徽章（粤/鄂）、**项目名**
  （证据句 `「…」` 正则回抽）、原文链

## BP2 POST /api/proposals/{eid}/nurture（培育流）

- 幂等建回访 Todo：「拟建回访：{单位}（备案公示，预计进入招标窗口）」
  due = 今日 +60 天，source=automation（open 唯一约束防重建）
- 转线索复用既有 POST /enterprises/{eid}/lead（前端直调，不重复造）

## BP3 前端 /proposals「拟建雷达」

- KPI 四卡（framed-sheet）+ 省份分布 BarList（charts 复用）
- 列表：备案时间/来源徽章/单位/项目名/地区/行业/原文 ↗/
  「培育回访」按钮（Toast 后可转线索）
- 侧栏「拟建雷达」+ PAGE_TITLES

## BP4 测试 tests/test_spec_bp.py

- backfill 幂等 + 建档补省（直接调扫描段 mock 采集器）
- center 数字/窗口语义（种 30-90 天前的信号断言 window_now）/
  省份行业分布 / q 过滤
- nurture 幂等（重放 0 新建）+ Todo 字段断言

## 边界

- 备案→招标 1-3 个月是行业经验窗口，Todo due=60 天取中值，语义如实
- GD 列表仅单位级（项目名在未暴露的详情接口，SPEC-AB 边界不变）；
  HB 为项目级，项目名从证据句回抽
- 拟建单位=买家侧（扩建/新建主体），正常不带 is_competitor；若被误标
  同行则前端隐藏转线索按钮（复用既有语义）

## 落地记录（2026-11-07 W59）

- BP0 真库回填 **96 家**拟建企业省份（proposal_gd→广东省 / proposal_hb→
  湖北省，来源通道确定性映射非发明）；两扫描段建档即补省（幂等只填空）。
- `api/proposals.py`：center（overview 30/90 天+window_now 窗口语义+
  省份/行业 Counter+列表过滤）+ nurture（Todo due=+60 天，exists 预查+
  uq_todo_open_dedup 双保险幂等）。
- 前端 /proposals：KPI 四卡（"正处招标窗口"amber 卡）+ ProvinceBars
  （点击即筛选）+ 列表（粤/鄂徽章、项目名证据句回抽「…」、培育回访/
  转线索双动作）。侧栏「拟建雷达」+ PAGE_TITLES。
- 调研改道记录：官网级 waterfall 因企业池无 website 字段（0 覆盖）且
  官网发现需搜索引擎（ToS 灰线）**如实搁置**；批量操作轮发现 SPEC-Y
  已交付（batch-lead/tags/pool/cleanup）避免重复造轮——先摸代码再立项
  的直接收益。
- 测试 test_spec_bp.py 3 条：省份回填幂等+不覆盖已有 / center 窗口语义
  （60 天前备案=window_now）+项目名回抽+q 过滤 / nurture 幂等+已删 404。
  门禁：pytest 314 + build + E2E 7 全绿。
