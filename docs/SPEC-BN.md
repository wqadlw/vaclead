# SPEC-BN · 竞对雷达轮：组合级竞对监测视图（Agent B 认领）

> 立项：2026-11-07 W59 · PRODUCT_GAP §5.3 真缺口 #3——"竞对中标动态视图——
> is_competitor 的 tender_win 已在库（零成本），只缺一个专门视图"。
> 对标剑鱼标讯把"竞对监测"当付费功能卖；我们的数据已就位。
> intel.py 的图谱是**单企业视角**；组合级"全部竞对在干什么"没有视图——
> 销售管理者的晨检场景：**今天谁家的蛋糕被竞对抢了**。

## BN1 GET /api/competitors/overview

KPI 卡数据源：
- competitor_total：is_competitor=1 且未删企业数
- signals_30d / wins_30d / win_amount_30d：竞对信号 30 天窗口
  （pub_date 优先、created_at 兜底；金额从公告文本回抽 `成交价X万元`，可溯源）
- lost_to_competitor：Lead.lost_reason='competitor' 线索数 + 关联合同
  金额合计（Contract.amount_wan）——被竞对抢走的生意值多少钱

## BN2 GET /api/competitors（竞对榜单）

逐竞对统计：累计信号/累计中标/累计金额/30 天中标/最近信号日/top_buyer
（从中标信号证据句 `采购人：X` 回抽最频繁者）。排序：30 天中标数降序
→ 累计信号降序。参数：q（名称过滤）、limit。

## BN3 GET /api/competitors/feed（竞对动态流）

Signal × Enterprise(is_competitor=1, 未删)，id 降序取 1000 → Python 侧
窗口过滤（days 默认 90）+ 类型过滤 + 分页。每行附：
- amount（文本回抽）
- stolen_from（tender_win 的采购人——竞对卖给了谁=我们丢掉的机会点）

## BN4 POST /api/competitors/{eid}/mark（admin_only）

手动标记/取消竞对 {flag: 0|1}。此前 is_competitor 只有自动化动作
（tag_competitor）入口，无人工修正通道。

## BN5 前端 /competitors 页

- 四 KPI 卡（framed-sheet 无阴影卡，同详情页分数条样式）
- 竞对动态流表：时间/竞对/类型徽标/标题/抢单对象/金额 → 企业详情链接
- 竞对榜单表：竞对/30天中标/累计中标/金额/采购大户/最近动态
- 侧栏「竞对雷达」项（AppShell NAV）；标记按钮仅 admin 可见
  （role 读 localStorage 'vaclead_role'，AppShell 既有模式）

## BN6 测试 tests/test_spec_bn.py

种 2 竞对 + 1 普通企业 + 信号（30 天内中标带金额/旧中标出窗/proposal）
+ 流失线索（lost_reason=competitor + 合同金额）→ 断言：overview 数字、
feed 排除非竞对与出窗信号、stolen_from/amount 回抽、榜单排序、
mark 切换生效。

## 边界

- 只采库内公开信号，金额/买家从公告文本正则回抽（与 intel/reports 同源
  正则，可溯源不发明）
- 竞对数少（几十级），信号子集小——Python 侧聚合，不做物化
- feed 单轮 1000 条上限，防全表扫

## 落地记录（2026-11-07 W59）

- `app/api/competitors.py` 四端点全通：overview / 榜单（q 过滤）/ feed
  （窗口+类型+分页）/ mark（admin_only）。信号→企业用 or_/== 链（Mimosa
  锚定规避），聚合纯 Python 侧（竞对几十级，无需物化）。
- 前端 `/competitors`：五 KPI 卡（含"被抢走的生意"红色卡）+ 竞对榜单表
  （top_buyer 回抽）+ 动态流（stolen_from 高亮为"被抢走的机会点"）+
  admin 搜索标记。侧栏「竞对雷达」+ PAGE_TITLES 注册。
- **测试教训**：共享测试库下其他模块会留 is_competitor=1 数据——断言
  必须基线相对（种数据前先取 overview 快照，比差值），绝对计数在全量
  跑必翻车（单跑过/全量挂的经典陷阱）。
- **E2E 种子修正**：种子联系人把手机号塞在 title 字段（AH 轮号码 chips
  上线前的历史 hack）→ 号码页面上渲染两遍触发 strict mode 违规。
  修种子（title="采购负责人"）而非 UI——UI 展示是正确的。
- 门禁：pytest 309 + build + E2E 7 全绿。
