# SPEC-SALE：销售赋能轮（话术卡系统化 + 省份补全收官 + 老板大屏）

> 2026-11-14 · 调研来源：双摸底 Agent（官网通道落点 + refs 抽取模式）+ 5 次实弹探测。
> **调研核心结论（排除法）**：采集侧免费合规弹药已打光——高德 detail 的
> website/email 实测 0/10（字段在但深层信息未填，25k 配额换零产出）；
> beianx.cn 搜索路径 410；creditchina 本网络不可达；ggzy bid_detail 端点
> 不存在（仅结构化记录无公告正文）；boss-zhipin 登录墙实锤
> （LOGIN_RESTRICTED_CODES/LoginGateError，红线不动）；存量语料邮箱仅 3 条。
> **转向：把 SALES_PLAYBOOK 的独有内容做成系统功能**（决策链三人各买不同/
> 装机=资产负债表/确定性=赢点），这是竞品抄不走的层。

## S1 话术卡系统（SALES_PLAYBOOK → 系统）

### 表（新表，init_db create_all 直建，无 ALTER）

`script_card`: id / title / scene / role_hint / body_md / sort / enabled /
created_at / updated_at

- scene 枚举：`first_contact`（首联）/ `competitor_replace`（竞对替换）/
  `repeat_purchase`（老户复购）/ `proposal_window`（拟建窗口）/ `generic`
- role_hint：对齐 Enterprise.customer_role（oem/end_user/reseller/
  integrator，可空=通用）

### 种子（幂等，照 product_seed 06:18 cron 模式，标记防重）

4 张昊志话术卡（内容提炼自 SALES_PLAYBOOK.md，销售语言）：
1. 首联·自报家门（官方授权+区域响应确定性三件套开场）
2. 竞对替换·挖角话术（选型与工况不匹配=螺杆泵采购最常见痛点切入）
3. 老户复购·回访（耗材时钟：密封 12-24 月/滤芯 2000-4000h 到期提醒）
4. 拟建窗口·项目介入（备案后 1-3 个月设备采购前兆，免费勘测钩子）

### API

- `GET /api/script-cards?scene=&role=`（全员，enabled 按 sort 排序）
- admin CRUD：`POST/PUT/DELETE /api/script-cards[/{id}]`

### 通话驾驶舱接线

prep 聚合端点扩 `script_cards` 数组——scene 映射：
- lead.status=new 或无跟进记录 → first_contact
- lead.lost_reason=competitor 或竞对中标信号在 30 天内 → competitor_replace
- lead.status=won → repeat_purchase
- 90 天内有 proposal 信号 → proposal_window
- 优先级：competitor_replace > proposal_window > repeat_purchase >
  first_contact（一个 lead 命中多场景取最紧急），另附 1 张 generic
- role_hint 与企业 customer_role 相等或为空的卡优先

### 前端

- 通话驾驶舱 SideSheet 新「话术」区：卡列表（标题+正文，折叠可展开）+
  一键复制按钮（零跳转，对准通话中场景）
- settings 页新「话术库」管理面板：CRUD 表格 + 场景/角色下拉
  （照自动化面板现有样式）

## S2 ggzy_buyer 省份落库（上轮挂起项转正，实锤路径）

**实弹验证**：bid_list 原始记录 `areaCode` 直接是省名（"山东省"）。

- normalize_bid_record 的 `area` 已解析（ggzy.py:173）——ggzy-harvest
  worker 与 daily_incremental 建 **buyer（ggzy_buyer）** 帧时
  `e.province = area`（只补空不覆盖）；**中标方（bidder）不落**——
  area 是项目所在地不是企业所在地，不发明
- 存量回填：POST /tasks/backfill-buyer-province——有 tender_buy 信号且
  uscc 非空、province 为空的 ggzy_buyer 逐家调 bid_list（1.5s 礼貌间隔，
  cap 500/轮）取最近记录 area 补 province；真库现缺 34 家
- 测试：monkeypatch 模式（照 test_api ggzy-harvest）断言 buyer.province
  落库 + 回填任务幂等

## S3 数据大屏 `/screen`（老板驾驶舱，纯前端零后端改动）

- **布局**（1920×1080 优先，TV 挂载）：顶部标题栏+实时时钟 / 左列 KPI
  大字（企业池/可拨覆盖/今日新增信号/线索漏斗）+ 省份分布条 / 中列
  「今日推荐·先打给谁」轮播（8s 自动切换 Top5）/ 右列 竞对 30 天中标
  动态流 + 晨检三格（招标窗口/未触达/竞对中标）
- 数据全部复用现有端点：dashboard/summary、recommendations、
  competitors overview、quality radar-strip
- 60s 自动刷新；支持 `?token=` 认证回退（AA 轮机制已有）→ 可直接挂 TV
- **无侧栏沉浸式**（AppShell 加 fullscreen 模式或独立 layout，路由
  /screen 不进导航，从命令面板/设置页入口进入）；admin 权限
- 设计铁律不变：framed-sheet 浅色、禁紫禁渐变禁阴影、青蓝 #1b6c80、
  大字号版式（KPI 48px+）、lucide 图标禁 emoji

## S4 顺手债：导出 CSV 联系人列修复

- enterprises.py export（:741-758）「联系人」列改读 phones_json
  （dump_phones）——SPEC-CU 后 title 槽位已不再存号码，现状导出列
  恒空；顺带 email 有值时拼入（"姓名(角色):电话 邮箱"）
- 新增官网列的原始计划随官网通道证伪一并取消

## 验收标准

1. 后端 pytest 全量通过（现 434 + 新增：script_card CRUD/seed 幂等/prep
   场景映射/ buyer 省份/回填幂等/导出列）
2. `npm run build` 通过；/screen 页静态导出可访问
3. 实弹冒烟：script-cards API 往返、大屏页面 200、真库 buyer 回填
4. Mimosa 审查通过（Write/Edit 落盘）

## 不做（如实记档）

- 官网通道（website 字段/联系页抓取）：五路发现源全部实测证伪或灰线，
  待未来有合规域名发现源再立项——本 SPEC 是它的验尸报告
- 招聘信号源：登录墙红线，等官方 API 或用户明确授权再说
- 信用中国风险源：本网络不可达；intel.py 导航层深链维持现状
- 语料再挖任务：实测产出 3 封邮箱，不值得立任务
