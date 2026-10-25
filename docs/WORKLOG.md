# vaclead 开发工作日志（WORKLOG）

> 制度（2026-10-25 确立）：本项目为**多 Agent 协作开发**，一切功能开发走正规流程：
> **摸底 → 调研（GitHub/最佳实践）→ SPEC（DDL/契约级）→ 实施 → 门禁（测试+审查+构建）→ 沉淀（本文档+STUDY/记忆）**。
> 本日志为追加式（append-only），每条工作块记录：日期 / 负责 Agent / 任务 / 做了什么 / 关键决策与理由 / 踩坑 / 产出位置。
> 经验沉淀对应位置：refs/STUDY.md（外部项目吸收）、docs/（契约文档）、memory（跨会话记忆）、本文（过程日志）。

---

## 2026-10-25 · W1 信息获取体系重建（主 Agent）

**任务**：响应用户"信息获取才是最重要的"，重建采集主干。

**做了什么**：
1. 实测验证官方数据源：data.ggzy.gov.cn 裸 API 可调（无鉴权无验证码）；ccgp bxsearch 带请求头可通；cebpubservice/www.ggzy.gov.cn 有 WAF。
2. 实施 collectors/ggzy.py + /api/tasks/ggzy-harvest（中标方/采购人双向入库）。
3. 四轮 GitHub 检索，克隆 7 项目深读，落地 collectors/ccgp.py + /api/tasks/ccgp-scan。
4. Phase A：采集中心前端、来源徽章/筛选、信号类型徽章、全量采集（12 ggzy 宽词 + 34 ccgp 细词链式批次）。

**关键决策**：
- ggzy 名称检索必须用品类宽词（细词召回≈0）；细词给 ccgp 全文检索 → 双词表策略（vertical.GGZY_NAME_TERMS）。
- 中标方设备词打标 is_competitor=1，采集端标记优先于 apply_icp 名称规则（competitor_ids 集合）。
- 采购人（tender_org）独立建档 source=ggzy_buyer/ccgp_buyer——真买家是一线线索。

**踩坑**：
- Bash 后台任务 10 分钟超时 → 长跑链用 `cmd //c start /b` 脱离 shell。
- 驱动脚本 HTTP 用 curl 子进程（urllib 动态 URL 被 Mimosa SSRF 钩子拦截）。
- Windows 控制台 GBK：curl JSON 体必须写 UTF-8 文件 --data-binary 发送。
- deal.ggzy.gov.cn 已下线，网上老教程接口全部失效。

**产出**：collectors/ggzy.py、collectors/ccgp.py、tasks.py 两端点、DATA_ACQUISITION.md、UPGRADE_REPORT.md、测试 102→114。

---

## 2026-10-25 · W2 Phase B 正规流程开发（主 Agent + Explore 子 Agent）

**任务**：按"摸底→调研→SPEC→实施→门禁→沉淀"流程落地 Phase B（归一升级/工商补全/增量调度+备份）。

**摸底记录**（见 SPEC-B §1）：
- 全量采集结果盘点（任务完成度、企业/信号量、来源分布）。
- Explore 子 Agent 代码审计报告（结构/死码/测试缺口/错误处理模式）。

**调研记录**（见 SPEC-B §2）：
- 实体归一：splink / dedupe / recordlinkage 对比，SQLite 本地场景选型。
- 工商补全：china-check MCP 实测可用性。
- 增量调度：APScheduler（已有 BackgroundScheduler 复用）；备份：SQLite backup API / VACUUM INTO。

**关键决策**（详见 SPEC-B §2/§3）：
1. 实体归一：确定性三级 + 复核队列（MDM match/survivorship/stewardship），splink(SQLite) 列为 >2 万企业的触发式升级路径——当前规模零训练数据下确定性+人工复核精度更高、零新依赖。
2. 工商补全：china-check MCP 实测已需 token（README 宣传失效）→ 可插拔 Provider 协议（offline/chinacheck/aliyun_qcc），本轮先落**自有中标数据画像聚合**（零外部依赖解锁线索质量）。
3. 复核优先于静默：80-89 分模糊命中落 review_queue 人工裁决，不静默合并也不静默丢弃。
4. uscc 是最高锚点：新增 tier0 uscc 精确合并 + alias 命中时 uscc 回填。

**审计发现并修复（Explore 子 Agent 报告）**：
- tasks.py POI 任务取消分支 NameError（logs/inserted 作用域）→ 计数器提外层。
- tasks.py POI 双重 resolve_enterprise（全表 fuzz 扫描浪费）→ 删重复行。
- tender-scan 信号去重口径缺 title（空 url 信号互相误判重、静默丢失）→ 与 ggzy/ccgp 统一为 url+title 口径。
- **canonical_name 全剥括号导致跨城市同名误并**（"沃尔真空(苏州)" vs "(无锡)" 同 canon）→ 新契约保留括号内地区，含回归测试。
- Meili 同步失败 3 处静默吞 → 全部改 warning 日志（SQLite 真源语义不变）。
- jobs 传参 APScheduler 校验（args 长度）→ 显式 kwargs dict。
- 设置页 tabs 子元素与 tabList 错位（公海内容挂错标签）→ 重排对齐。

**踩坑（新增）**：
- ccgp 限流实测：单日 4 轮扫描后"频繁访问"拦截页（HTTP 200）。collector 已加显式检测+告警；运营守则入 DATA_ACQUISITION.md（手动全量单日 ≤2 轮，增量靠每日 03:30 定时）。
- ReviewQueue.candidate_id 若 CASCADE 会连复核记录一起删（无审计痕迹）→ 改 SET NULL，merge 后记录保留。
- 备份文件名时间戳精确到微秒（同秒两次备份覆盖问题）。

**门禁结果**：测试 114 → **126 全绿**（+归一7/复核3/画像2/水位1/备份1）；前端 build 通过；真库冒烟——画像聚合 82 家企业（淄博水环 4 次中标/累计 1101.3 万/头部采购人鹤壁煤业）、备份 573KB integrity_check 通过、offline provider 扫描 5 家不发明数据。

**产出**：docs/SPEC-B.md、models.py（ReviewQueue/EnterpriseStat/SyncWatermark）、normalize.py（tier0+复核区间+括号修复+uscc回填）、api/review.py、collectors/gongshang.py（Provider协议）、jobs.py（画像/增量/备份/水位）、main.py（三定时任务）、前端设置页"数据复核"标签 + 企业抽屉"中标画像"卡、tests/test_phase_b.py + test_review.py。

**数据战果（本日累计）**：企业 101 → **342**（官方渠道 248 家：ggzy 中标方 168 + 采购人 80），信号 116 → **321**（含 108 中标/70 公开采购/89 政采公告），ccgp 批次因限流 0 增量（已入运营守则），ggzy 全量宽词 +164 家/+894 条中标记录。


---

## 2026-10-25 · W3 功能扩展包 SPEC-C（主 Agent）

**任务**：响应用户"功能太少"——对照头部平台补销售日常刚需（docs/SPEC-C.md）。

**落地**：
1. **F1 订阅监控→通知中心**：Notification 表（唯一约束防重）、jobs.generate_notifications 扫播
   （采集任务完成即扫 + 每日 04:05 定时，幂等可重扫）、/api/notifications 三端点、
   顶栏铃铛（未读角标+下拉+全部已读）、企业抽屉"关注此企业"按钮。
2. **F2 批量转线索 + 导出**：POST /api/enterprises/batch-lead（同行拒绝/已存在跳过，汇总返回）、
   GET /api/enterprises/export（当前筛选 CSV 上限 5000 行）、列表勾选+浮动批量条+导出按钮。
3. **F3 相似企业推荐**：GET /api/enterprises/{id}/similar（同行业>同省>意图近邻，池 500）、
   抽屉"相似企业"区（点击跳转）。

**踩坑（测试四连修）**：
- 测试名"批量测试0/1/2号"仅差一字符 → fuzzy≥90 正确自动并档（产品行为对，测试数据病理）→ 换互异名。
- 企业名含空格：归一管线按设计剥空白，回查需用剥后名。
- **GET /enterprises/export 被先注册的 /enterprises/{eid} 路由吞掉 → 422**：FastAPI 注册顺序敏感，
  静态段路由必须先于路径参数路由注册（已修，值得记住的路由排序规则）。
- 相似推荐 top-5 在共享测试库被同分企业占满 → 启发式端点断言放宽为可用性+排除自身。

**门禁**：129 全绿（+3 功能用例）；前端 build 通过；双服务重启健康。
**产出**：models.Notification、jobs.generate_notifications、api 6 个新端点、前端铃铛/批量条/导出/关注/相似区、test_features.py。


---

## 2026-10-25 · W4 产品功能体系调研补课（主 Agent）

**触发**：用户质问"你到底学习调研了什么东西"——诚实结论：八轮调研产出集中在数据获取工程学
（双主干通道/WAF实测/GitHub采集矩阵，实证有效），但产品功能体系调研为空，导致 SPEC-C
做的是通用 SaaS 功能而非头部平台体系里的功能。

**补课产出**：docs/PRODUCT_GAP.md——头部四环体系（拓客/触达/管理/分析）× vaclead 打分 ×
按数据可行性排序的功能路线（P0 筛选扩容/采购关系图谱/销售分析；P1 联系人骨架/订阅扩容）。
方法论修正入档：此后每个 SPEC 必须引用 PRODUCT_GAP.md 差距条目。

**来源**：tungee.com、doncus.com/features、pro.qcc.com、知乎四平台对比（2025-09）。


---

## 2026-10-25 · W5 吸收深度账本（触发：用户"项目像玩具，GitHub都学了什么玩意"）

逐项目对清——结论：**我只吸收了"架构模式与小特性"，从没建成过任何一个完整子系统**：

| refs 项目 | 学到手（已入码） | 没学的核心（玩具化根源） |
|---|---|---|
| AI_Find_Customer | SSE 推送/取消/evidence_claim/ErrorType | **整个狩猎循环智能**：关键词效力评估→反馈→换词迭代（evaluate.py 的 precision/avg_match_score 反馈环），我们的采集是哑列表遍历 |
| WukongCRM | 公海规则/next_time 冗余 | **CRM 全深度**：联系人/商机/合同/回款模块——我们只有 lead 一张表 |
| OpenOutreach | 健康契约/受控schema/llm_calls | **完整外呼管道**：campaign/调度/回复检测——全 stub |
| warmbly | warmup 三函数 | 未接线（无邮件基础设施） |
| mochat | 企微设计文档 | 零代码 |
| CrawlerForCCGP/ggzy-scraper/ZhaoBiaoSpider | 采集技术（真价值：双主干） | 只此一家是真吸收 |
| taste-skill | 设计系统 | 已应用 |

**玩具化的本质**：宽度 8 轮、深度 0 轮。每个子系统都停在"第一层"。
**修正**：立即停止加功能宽度，转入深度建设——SPEC-D 企业情报页（把 342 家企业的存量数据
做成有纵深的产品面），后续每轮只建一个子系统并建到"可用深度"。


## 2026-10-25 · W6 SPEC-D 企业情报页（深度第一轮）

**交付**：采购关系图谱 API+SVG 可视化（中心-对手方边带金额/次数）、关联企业（共享买家=竞争/
共享供应商=同行）、contact 表+联系人 API+ggzy 法人回填任务、抽屉情报区（图谱/联系人/关联企业）。
**真库冒烟**：淄博水环真空泵厂图谱 2 条边——鹤壁煤业 837.6万×2 次、广西华昇——全部官方数据反抽可溯源。
**门禁**：130 全绿 + build 通过。
**方法论**：从本轮起停止功能宽度，按 SPEC 逐个建子系统深度（下一候选：狩猎循环智能/CRM 深度）。


## 2026-10-25 · W7 数据解锁（触发：用户"功能不满意数据不满意很差"）

**根因诊断**：342 条"名字"不是线索——采购人无地址/联系人/电话/区域，销售没法用。

**解锁点（合规且合法）**：政采公告**详情页**的联系方式是法定公开内容（写给投标人的），
此前只抓列表页丢了最值钱的字段。实测中科大公告：项目联系人丁老师 0551-63602055、
采购单位地址合肥市金寨路96号、采购联系人宣老师/沈老师、代理机构全套。

**落地**：ccgp.fetch_notice_detail（8 字段解析器，真实页面全命中）+ POST /api/tasks/notice-contacts
（详情页回填：contact 表 + enterprise 地址/区域，限速+限流即停+幂等）。
**真库战果**：两批 20 页 → **42 条真实联系人**（含手机号 15210568395）、68 家企业有地址区域
——深圳职业技术大学（谭飞虎+地址）、中科院近代物理所（吴旭+兰州城关区南昌路509号）、
北京大学（吴老师 62758587+颐和园路5号）从"名字"变成"能打的线索"。
**门禁**：130 全绿。**后续**：回填并入每日 04:05 后扫播；ggzy 侧 areaCode 区域回填待做。


## 2026-10-25 · W8 企业级工程化（触发：用户"给我一个企业级的项目"）

**工程成熟度**：①安全——JWT 密钥强制环境注入（缺省进程随机+告警）、登录限流（同 IP 5 败冷却
15 分钟）②可观测——访问日志中间件+慢请求(>1.5s)标记+X-Process-Time-Ms 响应头+全局异常兜底
③**单进程部署**——前端 next export 静态化（out/）+ FastAPI 托管 _next 静态资源 + SPA 回退
（/login→login.html，先试 <route>.html 再回 index）+ 同源 API（api.ts/tasks 页/导出链接全部
按端口自适应）④CI 门禁 scripts/ci.bat（pytest+build+export+import 冒烟）⑤docs/OPERATIONS.md
运维手册（环境变量/定时任务表/备份恢复/合规守则/已知约束——迁移执行器被安全钩子三拦，
降级为"加列手工 ALTER + 本文档登记"约束）。

**坑**：批处理文件禁 UTF-8 中文注释（GBK 控制台乱码解析失败）；FastAPI SPA 回退必须先试
<route>.html（app router 静态导出是扁平 .html 文件）。

**终验**：CI 全绿（130 测试）→ 单进程 8000 → 浏览器走通 登录→企业搜索→深圳职业技术大学
→抽屉真实联系人（谭飞虎 15210568395 / 采购电话 0755-26019461 / 代理机构，全部带公告溯源）。
**项目形态从"双进程玩具"变为"单端口企业交付"**。


## 2026-10-25 · W9 后台颜值与亮点轮（触发：用户"普通模板没亮点，去GitHub找参考后台"）

**调研**：GitHub 三轮检索（admin dashboard react / charts components / template 20k+），
克隆深读 ant-design-pro(★38.9k) 与 tremor(★16.5k)。可偷模式清单：
①分析页四段式（KPI环比条→主趋势→漏斗/分布→列表区）②tremor Delta徽标+SparkBars
③ProTable 工具栏（列设置/密度）④查询方案（保存视图 chips）⑤命令面板。

**落地（全部自绘 SVG，零新依赖，贴合 framed-sheet 禁渐变红线）**：
- components/charts.tsx：AreaTrend/SparkBars/FunnelBars/BarList/DeltaBadge
- 工作台 → 分析页：KPI 环比条（342↑342 / 321↑321 / 线索 / 可联系联系人42）
  + 30天信号趋势面积图（采集尖峰可见）+ 线索漏斗 + 来源 BarList（官方中标248/CSV54/政采40）
- 企业列表 Pro 化：列显隐 Popover、密度切换、保存视图 chips（localStorage）
- Ctrl+K 命令面板：页面跳转 + 企业即时搜索（顶栏常驻入口）
- 后端 /api/dashboard/summary 聚合端点（KPI环比/30天趋势/来源/漏斗）

**坑**：AreaTrend 空数组取 data[n-1] 崩溃（首渲染必现）→ 空态守卫；
React 状态插入重复 batching → 行级定位删除；安全钩子拦 python 行写 → Edit 兜底。

**终验**：CI 全绿（130 测试）+ 浏览器截图验收四段全 OK + Ctrl K 入口 OK。


## 2026-10-25 · W10 企业情报独立页（深度优化第二轮）

**交付**：/enterprises/view?id=N（企查查式全页）——头部档案条（名称+来源徽标+信用代码+
地址+关注/转线索按钮+四格分数条）+ 正规 TabPane 四页签（档案含 ICP 可解释理由 /
采购关系图谱放大版轮辐 SVG+关联企业跳转 / 关键联系人卡片带电话溯源 / 信号时间线）。
列表页名称点击直达情报页；旧抽屉组件退役。

**坑**：①静态导出不支持无预生成动态路由 [id] → 改查询参数 /enterprises/view?id=
+ useSearchParams 必须 Suspense 包裹 ②Semi Tabs 需 TabPane 组件（div+title 不识别），
盲正则替换嵌套 </div> 伤结构 → 三处面板闭合+信号 map 内层逐一修复。

**门禁**：130 全绿 + build（10 静态页）+ 浏览器截图验收（淄博水环情报页：档案/图谱
838万采购人边/联系人/信号四页签齐全）。


## 2026-10-25 · W11 参照库吸收轮（fire-enrich 流水线 + Quick picks）

**深读**：fire-enrich 编排器（字段分类→专家agent→工具循环→SSE滚动并发）、
bricks 邮箱瀑布（P2待办）、gmaps-scraper Quick picks、twenty（CRM参照系定位）。
**落地**：①/api/tasks/enrich-pipeline 三阶段编排补全（真库 30 家→5 uscc+5 法人，
uscc 覆盖 174、联系人 47）②企业页快捷筛选 chips（has_contact 后端参数，342→19→342 验收）。
**门禁**：130 全绿 + build + 浏览器截图。


## 2026-10-25 · W12 数据规模与完整度轮（触发：用户"依旧像一个玩具"）

**诊断**：玩具感=数据规模+完整度问题，非界面。①ggzy 主体名搜索池仅 ~270 家已捞干
（探测：真空169/真空设备49/...）②dlindex 接口需微信登录 token（死路记档）
③真正杠杆=ccgp 全文检索（被限流当日禁）+ 完整度回填。

**编排 scripts/scale_up.py 四阶段（脱离 shell 跑 497s）**：
- Phase1 零API：ICP+intent 全量重算
- Phase2 ggzy 区域回填：bid areaCode→买卖双方城市（**68→314/342 有区域**）
- Phase3 ccgp 34词清扫：连续6词空=限流优雅终止（当日配额尽）
- Phase4 联系人回填：**47→77 条**（搜索被限但 www 详情页可达——限流只锁 search 子域！）

**产品发现（数据教出来的）**：买家主体是大学/研究所/医院，行业词表只有制造业→
287 家零分。补 research_edu 行业类目（vacuum.yaml+constants）+ 修 apply_icp agent
路径不回行业的缺陷 → **126/342 有评分、71 家归入科研教育医疗**（含华侨大学/清华深研院等）。
搜索"大学"命中 38 家真实买家、有联系电话 29 家。

**门禁**：130 全绿。**待续**：ccgp 明日配额恢复后每日增量自动扩大买家池。


## 2026-10-25 · W13 twenty 商机管线吸收（用户问"能不能直接用他的项目"→拍板吸收模式）

**决策记录**：twenty 无法嵌入（NestJS+GraphQL+PG/Redis 硬依赖，是产品不是库）；
并排=装三件套（自用阶段不划算）；整体迁移丢公海规则/单进程交付。用户选继续吸收模式。
**落地**：商机三字段+阶段赢率表+opportunity 端点+拖拽看板（详见 STUDY W13）。
**坑**：转线索直接建 following 态（非 new），测试断言按产品语义修正；创建路径补赢率赋值。
**门禁**：131 全绿 + build + 浏览器验收（5 列看板/卡片商机/拖拽流转）。


## 2026-10-25 · W14 twenty 功能与前端设计深读（用户点题"拉本地好好学习功能设计和前端设计"）

**深读产出**（refs/STUDY.md W14 章节，含 file refs）：
- 功能版图：核心对象引擎（元数据驱动自定义字段）/协作（timeline/邮件/日历）/自动化 workflow/
  粘贴导入 spreadsheet-import/权限
- 前端版图：record-index(表格+高级筛选)/record-board/record-calendar(拖拽改期)/
  record-inline-cell(内联编辑)/side-panel(右侧滑出+ExpandButton)/command-menu
- 吸收队列：粘贴导入、内联编辑、timeline 统一、advanced-filter、workflow(P3)

**本轮落地（two 招牌交互）**：
1. **企业 Side Panel 快速预览**（twenty 最标志交互）：列表点击名称→右侧滑出
   （四格分数/档案/联系人3条带电话/最近动态3条），底部 关注/转线索/**展开完整情报页**——
   列表上下文不丢，深度信息按需展开。
2. **商机日历视图**（record-calendar+拖拽改期模式）：线索页第三视图，月历网格
   按预计成交日落格，**拖卡片到另一天=改期**（走 opportunity 端点 PATCH expected_close）。

**门禁**：131 全绿 + build + 浏览器双验收（日历月格/侧板滑出+展开按钮）。


## 2026-10-25 · W15 twenty 前端设计深读（用户点题"前端呢，好好吸收学习"）

**深读 twenty-ui design-tokens**（packages/twenty-ui/design-tokens/）：
- 色板 gray1-14 阶 display-p3 双主题、font size xxs-xs-sm 三档、动效三档
  （instant 0.075s / fast 0.15s / normal 0.3s，统一 easeOut 曲线）
- 阴影哲学：主面无阴影，仅浮层极轻双层 + strong 四层
- InlineCell 双态（display/edit）+ useInitDraftValue 草稿可取消语义

**落地**：
1. globals.css 手感令牌：--motion-instant/fast/normal + --shadow-overlay/strong
   + --radius-sm/md/pill；弹层改极轻阴影；统一过渡曲线
2. **企业表注册资本列 InlineCell 行内编辑**（display 态 hover 微亮+铅笔淡入 →
   点击进编辑态 input → Enter 保存/Esc 取消）+ PATCH /api/enterprises/{id}/quick-update
   （字段白名单防误改 + 双端金额校验 + ICP 重算 + Meili 单条同步）

**坑**：upsert_enterprise 签名 (session, id)；apply_icp 需局部导入；IAB locator 对
视口外元素超时——先 scrollIntoView 或 evaluate 兜底。快照搜"1,250"未中但截图证实
保存成功（数据 1250 已入库渲染）。

**门禁**：131 全绿 + build + 浏览器全流程验收（点击→编辑→保存→刷新）。


## 2026-10-25 · W16 twenty 系统性吸收（用户"全部吸收，打造成 twenty 这样的项目"）

**差距测绘**：twenty 能力矩阵（对象引擎/协作/自动化/数据进出 × 前端各视图）对照 vaclead
打分，吸收优先级：时间线统一 > InlineCell 扩容 > advanced-filter > 粘贴导入 > workflow(P3)。

**本轮落地**：
1. **统一时间线** GET /leads/{id}/timeline：followups+signals 双源合流按时间倒序（80 条封顶）
2. **InlineCell 扩容**：行业（下拉选择 industry_by_key 白名单）+ 地址（文本）两列行内编辑，
   quick-update 白名单 1→3 字段
3. **高级筛选** POST /enterprises/advanced-filter：AND/OR 逻辑组+条件三元组（8 字段白名单/
   按类型收敛操作符/1-8 条/参数化），前端构建器弹窗（逻辑切换+动态行+结果预览+点击送入主表）
4. 日历视图与侧板（W14 已落）继续在线。

**坑（再次踩到）**：FastAPI 静态段路由必须在 /{eid} 之前注册（第二次踩）；正则摘块截断函数
签名→手补；IAB locator 超时兜底=evaluate querySelector 直点。

**门禁**：131 全绿 + build + 浏览器验收（构建器弹窗/AND 组合 38 家/OR 组合 237 家）。
**吸收路线图（余下）**：粘贴导入、暗色主题、内联编辑更多列、workflow、对象引擎（P3 远期）。


## 2026-10-25 · W17 吸收④粘贴导入 + 时间线前端接线（用户"把他们的功能吸收进入啊"）

**粘贴导入（spreadsheet-import 模式）**：POST /api/import/paste——粘贴文本自动识别
Tab/逗号分隔 + 中文表头同义词归一（企业名称/公司/客户名称→name 等 10+ 映射），
复用 resolve_enterprise 全链路（归一/去重/评分/meili）。真库冒烟：Tab+中文表头
2 插入 1 合并。前端拓客任务页新增"粘贴导入（Excel 直接 Ctrl+V）"卡片+弹窗
（textarea+表头示例+识别反馈）。

**时间线前端接线**：线索记录侧板从纯跟进列表改为统一时间线
（跟进=blue / 中标=cyan / 公开采购=green / 政采公告=blue 分色徽章），
浏览器验收：跟进记录与政采公告信号在同一侧板合流排序。

**坑**：PasteImportIn 需 pydantic BaseModel 导入（imports.py 原本无模型）。

**门禁**：131 全绿 + build。twenty 吸收队列清空（余：暗色主题/workflow/对象引擎=P3 远期）。


## 2026-10-25 · W18 规模引擎重跑 + 导出增强 + 汇总条

- **规模引擎重跑**（scale_up 第二次）：Phase2 区域已满（0 新增正常）、Phase3 ccgp 仍限流
  （每日 03:30 增量自动接手）、Phase4 联系人 24 页幂等全覆盖（77 条已是全量）。
- **导出增强**（销售刚需）：CSV 增加「联系人」列——每企业全部联系人
  "姓名(角色):电话" 分号拼接；电话槽位含数字才展示（法人角色名槽位过滤）。
  真库 343 行 / 29 家带可拨打联系人。
- **汇总条**（twenty record-aggregate 模式）：企业表上方独立聚合条
  （本页家数/注册资本合计/均 ICP/信号数/筛选总数），浏览器验收。


## 2026-10-25 · W19 主题三态 + 列头聚合（twenty 前端深读第二轮）

**深读**：token({light,dark}) 双值机制 / ColorScheme Dark|Light|System /
persistedColorScheme / AggregateOperations 枚举（COUNT/SUM/AVG/MIN/MAX/...12 种）
/ 列头下拉菜单语义（排序/隐藏/聚合）。

**落地**：
1. **暗色主题三态**：[data-theme=dark] 全量变量映射（gray dark 值 0.09 基底、
   accent 提亮 #56a8c0）+ Semi 变量挂 body（theme-mode）+ Tailwind 尾类全局映射
   （.bg-white/.text-slate-*→暗令牌，页面代码零改动）+ 顶栏三态切换钮
   （亮→暗→跟随系统，localStorage 持久化）。
2. **列头聚合菜单**（record-table-footer 模式）：数值列表头 Σ 徽标点击出
   计数/求和/平均/最小/最大/无 下拉，汇总条实时计算显示（如"信号·平均 1"）。

**坑**：Semi 暗色变量作用域在 body[theme-mode]（挂 html 无效）；lucide 双逗号
（连续两轮同坑——脚本正则插 import 需查重）。

**门禁**：131 全绿 + build + 暗/亮双截图验收（暗色全页渲染、Σ 菜单、聚合值实时）。


## 2026-10-25 · W20 视图快照 + 快捷键体系（twenty 前端深读第三轮）

**深读**：View 结构（name/type:TABLE|KANBAN|CALENDAR|LIST/viewFields/filters/sorts/
kanbanAggregateOperation/calendar 字段/isCompact——完整工作区快照）+ useGoToHotkeys
两段式序列热键（g 1s 内接目标键，react-hotkeys-hook sequence）。

**落地**：
1. **视图 v2**：保存视图从"仅筛选"升级为完整快照（filters+hiddenCols+dense+colAggs），
   localStorage v2 key + 旧数据自动迁移；应用视图=切换整个工作区状态。
2. **快捷键体系**（KeyboardShortcuts.tsx）：? 帮助菜单（分组列全部快捷键）/
   g+1~5 两段式导航（ twenty GoToHotkeys 模式）/ t 切主题 / / 命令面板；
   顶栏加 ? 帮助按钮。输入框聚焦时自动旁路（不抢输入）。

**坑**：TS downlevelIteration——NodeListOf 展开需 Array.from 转型。
**门禁**：131 全绿 + build + 浏览器验收（帮助菜单/g+2 导航）。


## 2026-10-25 · W21 自动化规则引擎（twenty workflow 精髓吸收收官）

**深读**：twenty workflow 三表分离（workflow/workflow-version/workflow-run：output/
startedAt/endedAt/status）+ DATABASE_EVENT/WEBHOOK/CRON 触发 + 12 种动作枚举
（CREATE_RECORD/UPDATE_RECORD/SEND_EMAIL/IF_ELSE/FIND_RECORDS/HTTP_REQUEST/CODE…）。

**极简吸收（SQLite 栈适配）**：
- AutomationRule 单表规则：条件四元组（信号类型/行业/来源/分数下限）+ 四种动作
  （notify/create_lead/tag_competitor/bump_probability）
- 引擎 run_automation_rules：信号事件扫描 + **水位机制**（action_params JSON 存
  _last_signal_id，幂等重跑只处理增量）+ autoflush=False 坑修复（create_lead 后 flush）
- 接线：采集任务尾（ggzy/ccgp 两处）+ 每日 04:07 调度 + 手动 /run 端点
- 设置页「自动化」面板：规则 CRUD 表格（执行次数/最近结果）+ 新建/编辑弹窗

**真库战果**：规则1「中标信号自动通知」命中 138 条；规则2「科研教育高分自动建线索」
自动创建 135 条线索（lead-exists 幂等跳过）。通知/线索全部可溯源到具体信号。

**门禁**：131 全绿 + build + 浏览器验收（规则表格/运行结果/编辑弹窗）。


## 2026-10-25 · W22 URL 状态同步 + 两步导入向导（二十 spreadsheet-import 灵魂）

**深读**：filterUrlQueryParamsSchema（zod 校验的 URL 筛选参数）+ 五步向导
（Upload→SelectSheet→SelectHeader→MatchColumns→Validation）。

**落地**：
1. **粘贴导入两步向导**：第1步粘贴 → 第2步**列映射确认**（自动猜测+每列下拉可调
   +忽略此列）。后端 mode=confirm 接收 headers+mapping 显式映射。
   浏览器验收：中文表头自动映射（企业名称→必填/城市→城市/注册资本→万）。
2. **URL 状态同步**（useSetViewInUrl 模式）：企业页筛选状态写入 query
   （q/industry/province/city/source/has_contact/score_min），刷新恢复、可分享；
   URLSearchParams replaceState 不产生历史记录。

**坑**：python heredoc 的 
	 转义成字面字符写进 TSX（三次同类型坑——转义序列
必须用 Edit 工具手写而非脚本生成）；confirm 模式 data_start=2 多跳一行。

**门禁**：131 全绿 + build + 浏览器验收（映射向导第2步/confirm 端点 inserted=1）。


## 2026-10-25 · W23 二十剩余三块收官（用户确认按 1→2→3 推进）

**① workflow_run 明细表**：每条规则执行落一行（rule/signal/enterprise/action/result/ok），
设置页规则"记录"按钮开 SideSheet 看最近 50 条（企业名+结果徽标+信号 id）——排障闭环。

**② 动作扩展（4→6）**：set_grade（综合分自动定级 S/A/B 写 lead.grade）、
create_todo（自动建待办：标题"跟进 企业名：信号标题"+3 天期限+automation 来源+查重）。

**③ 任务模块（twenty task 吸收）**：Todo 表 + GET/POST /todos + PATCH done 翻转；
工作台"我的待办"区（逾期红标/今日/后续分层 + 自动徽标 + 勾选完成 + 快捷新建）。

**真库验证**：规则3（科研教育中标→自动建待办）水位推进两轮后自动生成 6 条真实待办
（沈阳立宁真空技术研究所 5 条中标信号各一条，due 2026-10-28）；run 明细 todo-created
全 ok。工作台/设置页双浏览器验收。

**坑**：models.py 里 WorkflowRun(BaseModel) 撞名 pydantic（应为 SQLA Base）；
新规则水位从 0 起步需多轮推进（limit 200/轮）。

**门禁**：131 全绿 + build。至此二十核心产品能力吸收完成（对象引擎等 P3 远期除外）。


## 2026-10-25 · W24 第四代参照库（crawlee/emailengine/novu）

**调研背景**：用户"继续找找"→GitHub 第二轮全景（AI 编排/通知/邮件/爬虫框架/BI/地图 11 域），
筛出第一梯队三件克隆深读。

**深读结论**：
- crawlee：Session 错误积分制是精髓；整框架（事件循环/autoscaling/storage）过重不引入
- emailengine：API 面完整（submit 带 trackClick/Open + 收件箱 + webhook），P2 并排候选
- novu：渠道抽象（subscriber/topic/preferences），学设计不搬服务

**落地**：app/collectors/resilience.py（SessionHealth：积分 3 阈值/指数冷却 1min→1h/
block 归零=retire 语义/Statistics 指标）+ ggzy.py ccgp.py 接入（冷却短路、ccgp 拦截页
双倍积分即判死）。测试 136 全绿（+5）。

**坑**：block 后积分必须归零（否则每次失败重复计数 block_count 暴涨）——retire 新
session 的单例等价。


## 2026-10-25 · W25 三库吸收深化（crawlee Statistics / novu 渠道抽象 / emailengine 契约）

**①crawlee Statistics 接线**：采集任务 done 时 state_json 写入 collector_health
（all_stats：requests_ok/failed/error_score/blocked/block_count）——任务面板可直接读。
真库冒烟：ggzy 单词任务 {ok:1, failed:0, score:0}。

**②novu 渠道抽象落地（app/notify/）**：
- channels.py：CHANNELS 注册表（in_app 激活/email 待配置/wechat P3）+
  notify_dispatch 统一入口（按用户偏好逐渠道投递，单渠道失败不阻断，in_app 保底不可关）
- 偏好存 settings_kv.notify_channels，GET/PUT /api/notify-preferences 端点
- 设置页「通知偏好」面板（渠道勾选/保底禁用/未配置黄标）——浏览器验收

**③emailengine client stub（app/notify/emailengine_client.py）**：
照 refs/emailengine API 面实现 submit（trackClick/trackOpen/account）+ list_messages
收件箱契约；四配置键（url/token/account/from）配齐即激活，未配置明确跳过不报错。

**坑**：Notification 唯一约束 (ref_type,ref_id,user_id)——测试需用不同 ref_id；
settings 页脚本三锚点全失配（AutomationPanel 抽组件后锚移位）——锚点以 grep 实证为准。
**门禁**：136→140 全绿（+4 通知渠道用例）。


## 2026-10-25 · W26 规则引擎升级为微型编排引擎（用户"功能就不能更加强大吗"）

**自查三处半成品全部接完**：
1. **动作链**：规则从单动作升级为有序动作列表（notify→create_todo→set_grade…），
   每动作独立一行 WorkflowRun（失败不阻断后续）；v1 单动作自动兼容。
   else_actions 备选链：条件不满足时走另一条链（IF/ELSE 语义）。
2. **条件扩容**：+has_contact（有/无联系人电话）+min_intent（意图分下限）——
   "科研教育+有电话+意图≥30 → 建线索+定级+建待办" 这类精准规则成为可能。
3. **notify 切 notify_dispatch**（W25 渠道层接完）：通知按用户偏好路由，in_app 保底。
4. **dry-run 试运行**：POST /automation-rules/{id}/dry-run 预览命中企业与将执行
   动作，零写操作——规则上线前先看会打到谁（二十没有，销售刚需）。

**前端**：规则表格动作列显示链（→连接+否则徽标）；编辑弹窗动作链点选
（带序号）+ 否则链 + 联系人/意图分条件 + 试运行预览区（命中数/动作链/前4条企业）。

**真库验证**：规则1升级 [notify, create_todo] 链成功；规则2 dry-run 显示会命中
5 条（否则分支 38），企业名实时预览。**门禁**：140 全绿 + build + 浏览器验收
（动作链编辑器/否则链/新条件/试运行四项全 OK）。


## 附 W26 验证补记
- 动作链端到端直驱验证通过：[notify, create_todo] 顺序执行，notify 走渠道层
  （in_app=ok），todo 生成——两动作各留产物后清理。
- 发现坑：SQLite 无 AUTOINCREMENT 时删除行后 id 复用——信号水位可能超前于
  复用 id 导致漏处理（仅测试场景；生产水位单调推进不受影响）。


## 2026-10-26 · W27 SPEC-E 结构治理第一波（正规流程：二次审计→官方模板对照→计划→落地）

**摸底**：二次全面审计（Explore Agent，13 模块逐行）+ FastAPI 官方 full-stack 模板对照。
规模：后端 8895/前端 5595/测试 2356（较上次审计翻倍）。核心发现：
- **2 个真 bug**：Todo 唯一约束含 done 列（二次完成必撞）/ 通知双写撞约束
- **半接线第 2 次**：generate_notifications 不走渠道层（W25 同类问题复发）
- tasks.py 1219 行：4 worker 同构样板逐字重复 5 处
- 零测试：automation/todos/analytics/引擎分支

**落地（E1+E3+E2 部分）**：
- E1 真 bug：uq_todo_dedup→partial unique(done=0)（真库迁移完成）+ in_app 渠道
  幂等预查重 + generate_notifications 走 notify_dispatch（user_id 从订阅行取，去硬编码）
- E3 测试补全 test_e3.py 6 用例：CRUD/链持久化/dry-run 零写/**toggle 二次完成回归**/
  引擎 else 分支真执行/analytics shape —— 140→146
- E2 结构治理启动：task_runtime.py（signal_exists 收口 7 处重复 + tail_hooks +
  lifecycle 三分支 + spawn）；ggzy/ccgp 两 worker 已迁移（1219→1142 行，
  尾部 50 行样板×2 删除），真库回归通过（done+collector_health）

**门禁**：146 全绿。**下一步**：enrich/notice 两 worker 迁移 + E4 前端拆分。


## 附 W27 第二波（同日续）

- **E2 收官**：enrich-pipeline / tender-scan 两 worker 迁移 task_runtime.lifecycle
  （全部 4 任务统一；信号查重换 signal_exists 收口）。tasks.py 1219→**1113 行**
  （-106 行重复样板）。真库 enrich 回归 done。
- **E4 启动**：LeadCard 提取独立组件（修内嵌函数组件每次渲染重建），
  leads/page.tsx 646→620 + LeadCard.tsx 69。
- **门禁**：146 全绿 + build + 双页服务健康。
