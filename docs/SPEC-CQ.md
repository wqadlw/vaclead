# SPEC-CQ · 地图作战轮：区域作战工具的行动闭环（Agent B 主线程+子 Agent 并行）

> 立项：2026-11-08 · 用户："/map 功能没完善好，知道为谁服务，多派
> 子 agent"。**为谁服务**：地图=销售区域作战工具——出门前看"这片
> 有哪些可拨客户"、到现场导航；老板看区域密度。现状断点（全量读码
> 实锤）：**popup 里连电话号码都没有**（只有"有公开电话"四个字）、
> 无意图分层、无图例、学校还挂在筛选里（客户定义修正轮遗漏）、
> 企业名直插 innerHTML 无转义。

## CQ1 后端 geo 增强（enterprises.py，主线程）

- items 补 **phone**（首个可拨号码，弹窗拨打的关键数据）+
  **recent_30d**（近 30 天有信号=还在活跃的买家）
- **排除 research_edu**（客户定义修正轮延续：学校非客户，地图默认
  不打点；industry_key 显式传 research_edu 时不排除——保留审计可能）
- clusters 模式补 recent 计数（聚合圆 tooltip 显示）

## CQ2 前端地图重构（子 Agent）

- **popup 作战卡**：企业名→情报页链接、行业/地区、**电话 chips
  （点击复制 + tel: 拨打）**、意图分、"近期有动态"徽标——看到目标
  即可行动
- **意图分层点样式**：intent ≥80 深紫大点 / ≥60 中 / 其余按
  has_phone 现状；**图例**（右上角，颜色含义一目了然）
- **筛选升级**：新增"近期有动态"chip；INDUSTRIES 移除科研院所、补
  冻干/制药/化工/包装印刷（与词表对齐）
- **XSS 修复**：popup innerHTML 的企业名/字段 escapeHtml 后插入
- 城市 chips 化（10 个 Select 下拉改一行 chips，点选即飞）

## CQ3 门禁

- 后端 pytest（geo 增强：phone/recent 字段、research_edu 排除）
- build + E2E 8；子 Agent build/E2E 自验

## 边界

- clusters 聚合模式不做电话明细（点进城市自然降级为 points）
- 地图不做录航/轨迹（导航交给高德 App，tel: 已闭环）
