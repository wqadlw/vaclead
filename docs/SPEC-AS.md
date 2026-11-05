# SPEC-AS · 昊志业务锚点落地轮（Agent B 认领）

> 立项：2026-11-05 W56 · Playbook 四层缺口的落地第一波：装机台账（CMMS 模型）
> + 客户角色 + 产品线反向匹配。业务锚点：昊志=昊青官方销售商+真空系统搭建商。

## 调研依据

- Playbook（SALES_PLAYBOOK.md）：装机保有量=资产负债表、耗材时钟=定时订单、
  工程师买"不背锅"（选型适配）、三人决策链
- CMMS 生态数据模型（Grash/SuperCMMS）：asset→maintenance_schedule→work_order
  ——抄模型不引代码
- 同行打标现状（icp_judge_rule:55）：`名称含真空+设备` =泵制造商 ✓ 语义
  基本正确；缺的是**关系维度**（制造商=同行 / 应用设备厂=OEM 客户 /
  集成商=渠道伙伴）

## AS1 装机台账（installed_unit 新表，CMMS 模型移植）

- 字段：enterprise_id FK / product_id FK nullable / model / serial /
  install_date / status（active→replaced|retired）/ consumable_json
  （`{"seal_due":"YYYY-MM-DD","filter_due":"..."}` 耗材时钟）/ notes
- **耗材时钟→回访待办**：每日 06:20 cron 扫描 30 天内到期的耗材项 →
  自动建 Todo「{企业} {耗材名}更换回访」（open 唯一索引防重建）——
  "每个装机自带下次采购的闹钟"
- API：GET/POST /api/enterprises/{eid}/units，PATCH /units/{uid}（状态流转）
- 前端：情报页新 tab「装机设备」（列表+新增+状态标记）

## AS2 客户角色（customer_role 新列）

- enterprise.customer_role：`oem`（应用设备制造商，买泵装设备）/ `end_user`
  （终端使用）/ `reseller`（同行分销）/ `integrator`（系统工程商）/ NULL 未定
- 情报页档案区 Select 编辑；企业列表筛选参数
- **打标语义不动**（is_competitor=泵制造商）——角色与同行正交
  （爱发科=同行 AND 泵制造商；镀膜机厂=OEM AND 非同行）
- 推荐引擎：OEM +10 理由「应用设备制造商——批量配套潜力」

## AS3 产品线反向匹配（推荐销售语言化）

- Product 加 `application_industries`（JSON，行业 key 列表）
- 昊青产品线种子（product 表为空时）：
  油封螺杆真空泵（食品/木器/烟草/医疗灭菌/包装）、螺杆真空泵（电子/半导体
  显示/镀膜）、压缩机、鼓风机
- **推荐理由销售语言化**：候选行业 ∈ 某产品应用面 → 理由
  「工艺适配：{产品名}适配其 {行业} 工序」

## 边界

- 环评设备清单采集（信号源）留 SPEC-AT 单独立项（采集器工程量独立）
- 装机台账不含维保工单流（CMMS 全量功能过度，只做台账+时钟+待办）
- 产品价格默认 0（报价时可改）——产品目录仅作推荐与台账锚
