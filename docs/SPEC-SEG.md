# SPEC-SEG：客户分层看板（报表中心"客户分层"区块）

> 第十六次功能轮。定位：管理视角的客户资产结构一屏——意图分层/可触达性/
> 行业/省份/管线阶段五个切面，全部带下钻（点条跳企业筛选）。
> 数据零新表（enterprise/lead 现有列聚合），图表全复用 charts.tsx
> （BarList/ProvinceBars 均带 onPick）。

## 1. 后端契约（reports.py）

`GET /api/reports/segments`（登录，全局口径）：

```json
{
  "total": 27421,
  "by_intent": [
    {"name": "高意图 ≥60", "value": 120},
    {"name": "中意图 30-59", "value": 800},
    {"name": "低意图 <30", "value": 26500}
  ],
  "by_dial": [
    {"name": "有号码·未触达", "value": n},
    {"name": "有号码·已触达", "value": n},
    {"name": "无号码", "value": n}
  ],
  "by_industry": [{"name": "半导体/集成电路", "value": n, "key": "semiconductor"} ×≤8],
  "by_province": [{"name": "江苏省", "value": n} ×≤8],
  "by_stage": [{"name": "新线索", "value": n}, ...lead 状态中文标签]
}
```

- intent 三档：≥60 / 30-59 / <30（与作战简报 intent_band 同口径）
- by_dial：has_phone 判断沿 contacts 列表口径（phones_json 非空）；已触达=
  该企业存在 call_result != "pending" 的联系人（一次 join 聚合）
- industry 聚合按 industry_key 计数、名称取 industry_name；value desc ×8
- province ×8（现有 city→省 回填后覆盖 87%，语义正确）
- by_stage：Lead.status 分组计数，中文标签复用 leads 页状态表（grep
  STATUS/PIPELINE 常量同源），仅统计未删除 lead

## 2. 前端契约（reports 页）

- 页面结构：单页滚动式（我的周报 → KPI → 雷达动态）——**追加**「客户分层」
  区块（h2 + grid，放雷达动态下方）
- 布局：`grid grid-cols-1 md:grid-cols-2 gap-3`，五卡：
  1. 意图分层（BarList，无下钻 v1）
  2. 可触达性（BarList，无下钻 v1）
  3. 行业 Top8（BarList onPick → `/enterprises?industry_key=<key>`）
  4. 省份 Top8（ProvinceBars onPick → `/enterprises?province=<name>`）
  5. 管线阶段（BarList，无下钻 v1；md:col-span-2 横铺）
- 卡头右侧 total 徽章（"共 N 家在管企业"）
- 下钻跳转前先确认 enterprises 页 URL query 支持（grep applied 初始化
  逻辑——支持 industry_key/province 则跳，不支持则 onPick 不传并记录）
- 移动端：单列纵排（全站惯例 grid-cols-1 md:grid-cols-2 自动生效）

## 3. 测试（test_spec_seg.py）

- 空库全零不炸；种子多企业后 intent 三档边界（60/30 精确切）
- by_dial：有号码未触达/已触达（造 call_result 非 pending 联系人）计数
- industry key 计数与排序；401（无 token）

## 4. 非目标

- 不做时间维对比（环比放周报域）；不做自定义分层规则（后续轮）；
  intent/dial 下钻 v1 不做（enterprises 快捷筛选 query 参数化后再接）
