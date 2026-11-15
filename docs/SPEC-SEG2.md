# SPEC-SEG2：客户画像数据化（客户群成为系统一级实体，全国范围）

> 2026-11-15（用户："做全国的；画像表放进项目里可编辑；融入整个流程"）。
> 客户群从静态文档升级为**数据库实体 + 可编辑界面 + 全流程消费**。
> 客户定义以 docs/CUSTOMER_PROFILE.md v2.2 拍板沿革为准（检测/实验室、
> OEM、政采买家、液环行业等已出局）。注意：旧 SPEC-SEG.md 是已交付的
> "客户分层看板"（报表中心聚合视图）——本 SPEC 是客户群**实体化**，两者
> 互补不冲突。

## 1. 新表 `customer_segment`（create_all 直建）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | PK | |
| name | TEXT | 客户群名（销售语言），如"镀膜加工厂" |
| code | TEXT unique | 如 coating_factory |
| keywords | TEXT JSON | POI 采集词数组，如 ["真空镀膜","水镀"] |
| industry_keys | TEXT JSON | 映射现有行业类目（名单归属依据），如 ["coating"] |
| business_lines | TEXT JSON | 买昊志什么：["整机","维修","配件","泵油"] |
| priority | TEXT | P0 / P1 / P2 |
| enabled | INT | 停用=退出推荐/统计 |
| notes | TEXT | 典型客户描述（销售能懂的话） |
| sort | INT | 展示顺序 |

## 2. 种子（10 群，全国版，幂等照 script_card 模式：SyncWatermark DB 标记+名称查重）

| name | code | keywords | industry_keys | lines | priority |
|---|---|---|---|---|---|
| 镀膜加工厂 | coating_factory | 真空镀膜/水镀/光学镀膜 | [coating] | 整机/维修/配件/泵油 | P0 |
| 半导体/电子厂 | semiconductor_factory | 半导体/晶圆/集成电路/封装测试 | [semiconductor] | 整机/系统 | P0 |
| 热处理厂 | heat_treatment_factory | 热处理/真空淬火/退火炉 | [heat_treatment] | 整机/泵油/配件 | P0 |
| 食品冻干厂 | food_freeze_dry | 冻干食品/脱水蔬菜/食品加工 | [freeze_dried] | 整线/配件 | P0 |
| 制药厂 | pharma_factory | 制药/药业/生物科技/冻干 | []（词表待补） | 整线/维保 | P0（enabled=0，notes 注明"词表待用户确认"） |
| 光伏/锂电厂 | pv_battery_factory | 光伏/太阳能/锂电/动力电池/电芯 | []（暂混半导体/未分类） | 整机 | P0 |
| 化工/新材料厂 | chemical_factory | 精细化工/锂电材料/石墨碳素 | [chemical_new_material] | 系统/整机 | P1 |
| 吸塑/塑料成型厂 | plastics_factory | 吸塑包装/医疗包装/吸塑厂/厚片吸塑 | [plastic_rubber] | 泵/配件 | P1 |
| 包装印刷厂 | packaging_factory | 真空包装/镀铝膜/包装印刷 | [packaging_printing] | 泵/配件 | P1 |
| 中央系统改造厂 | central_retrofit | 注塑厂/压合/化工厂（资格清单见 SEGMENTATION §四：应用点≥8） | [] | 系统集成 | P1 |

## 3. 后端 API

- `GET /api/segments`（全员）——每群实时统计：
  `{id, name, code, keywords, industry_keys, business_lines, priority,
    enabled, notes, enterprise_count, dialable_count}`
  统计口径：enterprise.industry_key ∈ industry_keys 的存活企业，**叠加
  全部非客户排除条件**（口径=contacts._non_buyer_conds 同款：竞对/
  research_edu/medical_health/lab_instrument/*_buyer/oem/公共机构名称词
  ——从 contacts.py 抽公共函数供两处复用，防口径漂移）；可拨=该群企业
  去重可拨联系人。
- `POST/PUT/DELETE /api/segments[/{id}]`（admin）：CRUD；code 唯一。
- 种子 cron：`segment_seed` 06:50（幂等，标记+名称查重双保险）+ lifespan
  启动即种一次。

## 4. 流程融入（四出）

1. **客户画像页** `/profile`（新页，导航入"信息中心"组）：主表=十群总表
   （名称/关键词/业务线/优先级/企业数/可拨数/状态）+ 行内"看名单"跳
   `/enterprises?segment=<code>` + admin 编辑 Modal（name/keywords 多选/
   industry_keys 多选/lines/priority/notes/enabled）。页头说明"画像定义
   驱动推荐/名单/采集"。
2. **企业列表** `/enterprises`：筛选器加"客户群"下拉（GET /api/segments
   驱动），选择后按该群 industry_keys 集合过滤（后端 enterprises 列表加
   segment 参数，OR 括号分组 SQL，Meili 分支同步）。
3. **拓客任务 POI 通道**：加"按客户群带词"下拉——选中客户群后 POI 关键
   词框自动填充该群 keywords（前端拼接，后端零改动）。
4. **推荐/补数清单**：出 **segment_priority 权重**（P0 群企业 +5 分，P1
   +2，P2 +0——recommend.py 在 ICP 分后叠加，理由链显示"客户群：镀膜加
   工厂（P0）"）。补数清单按群优先级排序（priority P0 在前）。

## 5. 前端契约要点

- `/profile` 页面权限全员可看，编辑仅 admin（role 判断照话术库面板）
- `/api/segments` 返回 `{items:[...]}`；编辑 Modal 字段与 POST/PUT 对齐
- 企业列表 segment 筛选参数名：`?segment=<code>`（后端解析→industry_keys
  IN 查询）
- 设计铁律不变：framed-sheet/禁紫禁渐变禁阴影/青蓝/无 emoji

## 6. 测试

- 种子幂等（跑两次数量不变）+ 十群内容断言
- CRUD 往返（admin 建/改/停用，sales 只读，未登录 401）
- 统计口径：某群 enterprise_count 排除非客户（造一粒 *_buyer 沙粒断言
  不计入该群）
- segment 筛选：enterprises?segment=coating_factory 只回该群企业
- recommend segment 权重：P0 群企业得分高于同分 P2 群企业（对照组）
- 隔离铁律：VACLEAD_DB setdefault + 模块快照清理

## 7. 验收

1. 后端全量通过（461+新增）；build 通过
2. 实弹：/profile 页面渲染十群、企业列表 segment 筛选生效、POI 通道
   带词生效
3. 文档：CUSTOMER_PROFILE.md 补采计划改全国版（画像数据化说明入档）

## 8. 不做

- 未分类 6,924 的自动归群（关键词匹配业务_scope 打标——下轮视效果）
- segment 与 Meili 检索联动（SQLite 口径先行）
- 客户群与自动化规则/订阅规则的联动

---

# 附录 A：作业化改造（2026-11-15 用户反馈："数量和质量不满意；给销售的不要高大上要务实；确保有用不只是看看"）

## A1. 后端：GET /api/segments 统计扩展（每群加"今天能打什么"）

在现有 enterprise_count/dialable_count 基础上，每群追加：

| 字段 | 口径 |
|---|---|
| dialable_untouched | 可拨且**全部联系人未拨**（call_result=pending）的企业数——"今天能打的" |
| touched | 已有联系人被拨过的企业数 |
| intent_high | 意图分 ≥60 的企业数 |
| following | 该群在管线索数（lead.status 非公海/流失） |
| new_7d | 近 7 天入库企业数（新面孔） |
| last_signal | 群内最新信号 {type, title(截 40), date, ent_name}（证据感） |

实现：企业级聚合一次查询为主（segment 集合内），避免 N+1；群数仅 10。

## A2. 联系人工作列表接入客户群（流程闭环的关键环）

`GET /api/contacts` 加 `segment` 参数：解析 code→群 industry_keys→
`Enterprise.industry_key IN (...)` 叠加现有筛选（与非客户排除天然兼容）。
前端 contacts 页筛选区加"客户群"Select（与 /api/segments 驱动）。

## A3. /profile 重构：销售作业视角（定义编辑降级）

布局（上作业/下配置）：
1. **客户群作战卡**（每群一卡，grid 双列）：
   - 卡头：群名 + 优先级 tag + 看名单
   - 主数字：**能打未触达 N 家**（最大字号，主 CTA）
   - 副指标行：已触达 N｜高意图 N｜跟进中 N｜近 7 天新面孔 N
   - 群内最新信号一行（日期+类型+标题截断——"这个群最近在发生什么"）
   - 按钮组：**开打未触达**（→ /contacts?segment=xx&call_status=untouched）、
     看全部名单（→ /enterprises?segment=xx）
2. **管理定义**（收进页底可折叠区，admin 才显示编辑钮）：
   现有十群定义表+编辑 Modal 原样迁入

## A4. 测试

- 新统计字段：untouched/touched/intent_high/new_7d 各自对照组
- contacts?segment= 过滤正确性（该群联系人在、他群不在）
- 既有 468 断言全保
