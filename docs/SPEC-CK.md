# SPEC-CK · 风鸟配额流水线轮：企查查类数据的合规用法（Agent B 认领）

> 立项：2026-11-08 · 用户指出"企业信息是项目基石，我们比不了企查查/
> 风鸟；风鸟送 5 年 SVIP，考虑怎么用"。**合规边界（红线不动）**：
> 不爬登录后的商业产品；**官方导出+人工查询=合规**（W52 定调：
> 买官方 API/导出=合规路）。风鸟免费 SVIP 正好走这条路。

## CK1 缺数购物清单：GET /api/tasks/gongshang-lookup-export（admin）

- 产出去风鸟逐家查询的 CSV：活跃企业中缺 uscc/法人/注册资本/成立
  日期或无可拨电话者，**意图分降序**（最值得花钱查的排前面），上限 500
- 列：企业名/地区/行业/意图分/缺失项（逗号组）/建议动作
- 用法：下载 → 风鸟逐家查询 → 官方导出/复制表格 → 走 CK2 回灌

## CK2 回灌端点：POST /api/import/gongshang-csv（admin）

- 输入 rows: list[dict]（列名→值，风鸟导出列直贴）
- 列名映射（局部）：统一社会信用代码/纳税人识别号→uscc；法定代表人→
  legal_rep；注册资本→registered_capital_wan（清洗"1000万人民币"→
  1000.0）；成立日期→established_at；企业电话/联系电话→phone；
  地址/经营范围→address/business_scope
- 语义：resolve_enterprise（**仅补空不覆盖**，既有安全语义）+ 电话
  建 Contact（source="riskbird"，evidence="风鸟工商档案公开电话"，
  同企业已有同号码跳过）
- 返回 {matched, filled, phones_added, skipped}

## CK3 文档：DATA_ACQUISITION §8 风鸟通道

- 注册送 5 年 SVIP（手机号）；官方导出/报告下载=合规数据源
- 用法三步：缺数清单→人工查询（配额内）→CSV 回灌
- Provider 槽位：gongshang.py 预留 riskbird（若有官方 API 开放再加，
  本轮不做自动调用）

## 测试 tests/test_spec_ck.py

- 清单：缺数企业入选+意图排序+上限
- 回灌：补空生效/已有值不覆盖/电话建 contact/同号跳过/新企业建档

## 边界

- 风鸟 API 未核实官方开放条款——Provider 实装留待拿到 API 文档
- 电话只进 contact 明文库（内部自用红线不变）
