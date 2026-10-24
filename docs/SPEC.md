# vaclead 真空设备智能拓客平台 · 技术规格（SPEC v1.0）

> 本文档是全部开发 Agent 的唯一契约。所有实现必须严格遵循本文；发现矛盾以本文为准并报告。

## 0. 项目总览

- **定位**：真空设备行业自用智能拓客平台（筛选下游工厂 → 招投标意图信号 → 线索跟进闭环）
- **技术栈**：FastAPI + SQLAlchemy 2 + SQLite(WAL+FTS5) ｜ Next.js 14 + Ant Design 5 + Tailwind ｜ LangGraph（编排）｜ v1 无 LLM（规则实现，双后端可切换）
- **目录**：monorepo，`backend/`（Python 包名 `app`）+ `frontend/`（Next.js）+ `config/` + `docs/` + `scripts/` + `data/`
- **启动**：`scripts/dev.bat` 一键起 uvicorn(:8000) + next dev(:3000)
- **多租户预留**：所有业务表含 `tenant_id INTEGER NOT NULL DEFAULT 1`，所有查询自动带 tenant 过滤

## 1. 数据模型（SQLAlchemy 模型，SQLite DDL 级定义）

文件：`backend/app/models.py`。主键一律 `id INTEGER PK AUTOINCREMENT`。时间戳存 UTC ISO 字符串（TEXT）。

### 1.1 user
| 列 | 类型 | 说明 |
|---|---|---|
| username | TEXT UNIQUE NOT NULL | |
| password_hash | TEXT NOT NULL | bcrypt |
| role | TEXT NOT NULL | 'admin' / 'sales' |
| display_name | TEXT | |
| tenant_id | INTEGER DEFAULT 1 | |

种子数据：admin/admin123（role=admin）、sales/sales123（role=sales）。

### 1.2 enterprise（企业主表）
| 列 | 类型 | 说明 |
|---|---|---|
| name | TEXT NOT NULL | 标准企业名 |
| uscc | TEXT | 统一社会信用代码，可空，有则 UNIQUE |
| region_code | TEXT | cpca 解析出的行政区划代码，可空 |
| province/city/district | TEXT | 三段地名 |
| address | TEXT | 完整地址 |
| industry_key | TEXT | 下游行业 key（见 §5 vacuum.yaml downstream_industries.key），可空 |
| industry_name | TEXT | 行业显示名 |
| business_scope | TEXT | 经营范围 |
| registered_capital_wan | REAL | 注册资本（万元），可空 |
| status | TEXT | 'active'/'closed'，默认 active |
| source | TEXT | 'poi'/'tender'/'csv'/'manual' |
| lat/lng | REAL | 高德坐标 GCJ02，可空 |
| icp_score | REAL DEFAULT 0 | 0-100 |
| icp_reasons | TEXT (JSON数组) | 如 [{"rule":"行业命中:半导体(tier1)","points":40}] |
| intent_score | REAL DEFAULT 0 | 时间衰减意图分 |
| is_competitor | INTEGER DEFAULT 0 | 0/1 疑似同行标记 |
| poi_id | TEXT | 高德 POI id，可空 |
| tenant_id | INTEGER DEFAULT 1 | |
| created_at/updated_at | TEXT | |

FTS5 虚表 `enterprise_fts(name, business_scope, address, industry_name)`，触发器同步；企业查询走 FTS5 MATCH + facet 过滤。

### 1.3 enterprise_alias
`enterprise_id FK, alias TEXT, source TEXT`。归一命中别名也指向主企业。UNIQUE(enterprise_id, alias)。

### 1.4 signal（意图信号）
| 列 | 类型 | 说明 |
|---|---|---|
| enterprise_id | FK NOT NULL | |
| type | TEXT | 'tender'（v1 仅此）/ 'eia' / 'registry_change' 预留 |
| title | TEXT | 公告标题 |
| url | TEXT | |
| pub_date | TEXT (YYYY-MM-DD) | |
| matched_terms | TEXT (JSON) | 命中的设备词列表 |
| hit_part | TEXT | 'title'/'body' |
| raw_excerpt | TEXT | 命中上下文摘录 ≤200 字 |
| base_score | REAL | 70（标题命中）/50（正文命中） |
| created_at | TEXT | |

### 1.5 lead（线索）
| 列 | 类型 | 说明 |
|---|---|---|
| enterprise_id | FK UNIQUE NOT NULL | 撞单保护：一企业一线索 |
| status | TEXT | 'new'/'following'/'intent'/'won'/'lost'/'pool' |
| owner_id | FK user | 领取人；pool 状态时为空 |
| grade | TEXT | 'S'/'A'/'B'，领取时按综合分快照 |
| lost_reason | TEXT | 放弃原因：no_need/competitor/unreachable/budget/other |
| claimed_at/last_followed_at | TEXT | |
| tenant_id | INTEGER DEFAULT 1 | |

状态机：`new→following→intent→won|lost`；`following/intent --30天未跟进--> pool`；`pool→following`（重新领取）。状态流转 PATCH 校验合法迁移，非法迁移 422。

### 1.6 followup
`lead_id FK, content TEXT NOT NULL, next_action TEXT, next_time TEXT, created_at`。

### 1.7 subscription
`enterprise_id FK, user_id FK, created_at`，UNIQUE(enterprise_id, user_id)。

### 1.8 agent_run（Agent 工作日志）
`agent_name TEXT, backend TEXT('rule'/'llm'), input_json TEXT, output_json TEXT, decision_summary TEXT, duration_ms INTEGER, task_id TEXT, created_at`。

### 1.9 task_checkpoint（任务断点）
`task_id TEXT UNIQUE, kind TEXT('poi'/'tender'), status TEXT('running'/'done'/'error'), state_json TEXT, progress INTEGER 0-100, created_at/updated_at`。

### 1.10 settings_kv（运行时配置）
`key TEXT PK, value TEXT`。存：高德 key、知了 key、agent backend 切换配置（JSON）。

## 2. REST API 契约（前缀 /api，JWT Bearer 认证）

错误统一：`{"detail": "..."}`；分页统一：`{"items": [...], "total": n, "page": p, "page_size": s}`。

| # | 方法 路径 | 请求 → 响应 | 权限 |
|---|---|---|---|
| 1 | POST /api/auth/login | {username,password} → {token,role,display_name} | 公开 |
| 2 | GET /api/enterprises | query: q(FTS), industry_key, province, city, district, capital_min, capital_max, signal_days, score_min, grade, is_competitor(默认0=排除同行), page, page_size → 分页。item 含全部企业字段+signal_count | 全部 |
| 3 | GET /api/enterprises/{id} | → 企业全字段 + signals[]（按 pub_date 倒序） | 全部 |
| 4 | POST /api/enterprises/{id}/lead | {} → lead（不存在则创建 status=new 并自动领取为 following；已存在且非 pool 422 撞单） | 全部 |
| 5 | GET /api/leads | query: status, owner, page → 分页，item 含 enterprise 概要 | 全部 |
| 6 | PATCH /api/leads/{id} | {status, lost_reason?} → lead（校验状态机） | 全部 |
| 7 | POST /api/leads/{id}/followups | {content, next_action?, next_time?} → followup（同时更新 lead.last_followed_at） | 全部 |
| 8 | GET /api/leads/{id}/followups | → 按时间倒序列表 | 全部 |
| 9 | POST /api/import/enterprises | multipart CSV（列：name,province,city,district,address,business_scope,registered_capital_wan,industry_key?）→ {inserted,nmerged,skipped} 走归一引擎 | admin |
| 10 | POST /api/import/tenders | multipart CSV（列：title,content,url,pub_date,enterprise_name?）→ {signals_created,enterprises_created,matched_terms_summary} 词表匹配+意图分重算 | admin |
| 11 | POST /api/tasks/poi | {city, industry_keys[], keywords_override?} → {task_id}；无高德 key 时 400 {detail:"缺少高德Key，请在设置页配置"} | admin |
| 12 | POST /api/tasks/tender-scan | {days?} → {task_id}（知了 API 可用时；否则 400 提示用 CSV 导入） | admin |
| 13 | GET /api/tasks/{id}/events | **SSE**：data: {"type":"progress","progress":50,"log":"..."} / {"type":"done"} / {"type":"error","message"} | admin |
| 14 | GET /api/agent-runs | query: agent_name, task_id, page → 分页 | admin |
| 15 | GET/PUT /api/settings | GET → {amap_key_masked, zhiliaoke_key_masked, agent_backends}；PUT {amap_key?, zhiliaoke_key?}（写 settings_kv） | admin |

附加：GET /api/dashboard → {total_enterprises, total_signals, signals_7d, my_leads_by_status, pending_followups[{lead_id,enterprise_name,next_action,next_time}]}（供工作台，sales 仅见自己）。
公海回收：APScheduler 每晚 02:00 把 last_followed_at 距今>30 天且 status∈(following,intent) 的 lead 置 pool、owner 置空。

## 3. Agent 框架契约（backend/app/agents/）

```
agents/
├─ base.py       # BaseAgent 抽象
├─ registry.py   # AGENTS 注册表 + backend 切换
├─ tools.py      # ToolRegistry（MCP 风格）
├─ runs.py       # agent_run 记录助手 record_run()
├─ graphs/hunt.py # LangGraph 狩猎图
└─ impl/         # 每 Agent：{name}_rule.py（v1 实现）+ {name}_llm.py（stub）
```

- `BaseAgent`：`name:str`；`input_schema/output_schema: type[BaseModel]`；`async run(data: dict) -> dict`；构造参数 `backend: Literal["rule","llm"]="rule"`。`registry.get_agent(name, backend)` 工厂。
- `ToolRegistry`：`@tool(name, description, input_schema: type[BaseModel])` 装饰器注册；`call(name, payload)`。工具清单：`search_enterprises / resolve_entity / upsert_signal / recompute_scores / create_lead / fetch_amap_poi / fetch_tender_csv / save_checkpoint / notify_todo`。schema 用 Pydantic 模型描述（MCP 风格：name+description+inputSchema）。
- 六个业务 Agent（impl/ 下成对文件）：
  1. `hunt_planner`：{city, industry_keys} → {plan:[{keyword, grid_cells}]}（规则：关键词×城市网格）
  2. `entity_resolver`：{name, context?} → {enterprise_id, is_new, confidence}（归一引擎：后缀清洗→别名表→rapidfuzz ≥90 合并）
  3. `signal_parser`：{title, content, pub_date, url, enterprise_name?} → {signals:[{enterprise_name, matched_terms, hit_part, excerpt, base_score}]}（设备词表匹配）
  4. `icp_judge`：{enterprise 档案} → {icp_score, icp_reasons[], is_competitor}（§5 权重表；竞对=名称命中设备强词且经营范围含制造/销售）
  5. `lead_qualify`：{icp_score, intent_score} → {grade, composite}（综合=ICP*0.4+intent*0.6，S/A/B）
  6. `query_translator`：{natural_text} → {filter_params}（v1 rule=空实现返回 unsupported，llm stub）
- `graphs/hunt.py`：LangGraph StateGraph，节点 plan→collect→resolve→parse→score→notify，evaluate 条件边（停止：目标达成/超 max_rounds=3/本轮新增<min_new=2）。HuntState TypedDict。checkpointer 用 SQLite（自实现 JSON 落 task_checkpoint 表即可，不强依赖 langgraph checkpointer）。每次节点执行调用 record_run()。
- 每次 SSE 任务（poi/tender）在后台线程跑狩猎图子流程或直接调 Agent，进度写 task_checkpoint 并通过 asyncio.Queue 推 SSE。

## 4. 采集器契约（backend/app/collectors/）

- `amap_poi.py`：`async collect_city(city, keywords, amap_key, progress_cb)`。高德 API：`/v3/place/text?keywords=&city=&citylimit=true&offset=25&page=1&key=`。单关键词超 200 条（4 页×25 且 hasMore）→ 调用者按行政区划县级细分重试（简化四叉树：区县粒度）。结果去重（poi_id），产出候选企业 {name,address,location,type_code}。GCJ02 坐标直接存。QPS 限 3/s。多 key 逗号分隔轮询。
- `tender.py`：知了标讯 API（有 key 时）`GET /open/api/list` 按设备词搜索近 N 天；无 key 时只支持 CSV 导入路径。两者都产出 {title,content,url,pub_date} 列表交给 signal_parser。
- 采集到的企业先过 entity_resolver → icp_judge → 入库；意图分重算 = 对该企业全部 signal 求和衰减。

## 5. 词表 config/vacuum.yaml（全文，Agent 直接读取使用）

见同目录 vacuum.yaml（已写好）。要点：device_terms.strong（25 词，命中即高分）/weak（4 词，仅标题）；downstream_industries（6 类含 tier/industry_codes/keywords）；competitor 判定=名称含设备强词 且 经营范围含制造|生产|厂家|销售；icp_weights 与 signal_weights 数值见文件；lead_grading: S≥80/A≥60/B≥40；poi_cities 10 城。

## 6. 前端规格（frontend/，Next.js 14 App Router + AntD 5）

- 路由：`/login` `/`（工作台） `/enterprises`（筛选） `/leads`（看板） `/tasks`（拓客任务） `/settings`（设置，仅 admin）。布局：登录后 Sider 菜单（AntD Layout）+ Header（用户名/退出）。
- 认证：token 存 localStorage，fetch 封装 `lib/api.ts` 自动带头、401 跳登录。
- 页面线框：
  - **工作台 /**：4 个 Statistic 卡（企业总数/信号总数/近7天信号/我的线索数）+ 待跟进列表 Table（next_time 最近在前）
  - **企业筛选 /enterprises**：顶部 Form（Cascader 行业/地区、InputNumber 资本区间、Select 信号天数、Select 评分、搜索词 Input.Search）→ Table（名称[链接打开Drawer]/行业Tag[按tier着色]/地区/注册资本/ICP分/意图分/信号数/操作[详情][转线索])。Drawer：Descriptions 工商信息 + 评分理由 Collapse + 信号 Timeline + 转线索按钮。疑似同行行置灰+Tag。
  - **线索看板 /leads**：Tabs 按状态（我的新线索/跟进中/意向/成交/放弃/公海）+ Table（企业/评分快照 Tag/负责人/最近跟进/操作[跟进])。跟进弹窗 Modal（content 必填 TextArea + next_action + next_time DatePicker）+ 状态流转按钮（按状态机渲染）。公海 Tab 有"领取"。
  - **拓客任务 /tasks**：两个 Card——POI 采集（City Select(10城)+行业多选+开始按钮）与招标扫描（CSV 上传/知了说明）；下方任务进度区（EventSource 订阅 SSE，Progress+日志滚动 pre）。仅 admin 可见入口。
  - **设置 /settings**：Tabs——数据源 Key（高德/知了，密码框+保存）、词表预览（只读渲染 vacuum.yaml 要点）、Agent 日志（Table 分页）、后端切换（显示当前 backend=rule）。
- 组件按需引入 antd（`import { Table } from 'antd'`），Next.js 客户端组件 `'use client'`。

## 7. 测试与门禁

- 后端 pytest（backend/tests/）：词表加载、归一引擎（同名/别名/近似名）、signal_parser 命中（标题/正文/不命中）、icp_judge 权重与竞对判定、lead 状态机合法/非法迁移、API 冒烟（FastAPI TestClient + 临时 SQLite）、意图时间衰减。
- 前端构建门禁：`npm run build` 通过。
- 全部门禁命令写入 `scripts/check.bat`（pytest + next build）。

## 8. 各开发 Agent 的文件所有权（S2 并行边界，禁止越界写文件）

- **Agent-A 数据层**：`backend/app/{models.py,db.py,normalize.py,seed.py}` + `backend/tests/test_{models,normalize}.py`
- **Agent-B Agent框架**：`backend/app/agents/**` + `backend/tests/test_agents.py`（可用 mock 表层）
- **Agent-C 采集器**：`backend/app/collectors/**` + `backend/tests/test_collectors.py`（httpx mock）
- **Agent-D API层**：`backend/app/{main.py,api/**,auth.py}` + `backend/tests/test_api.py`
- 公共依赖：A 先产出 models.py（S2 启动时由主 Agent 先写好 models.py 骨架再并行，避免 B/C/D 等待）→ 实际执行：主 Agent 预置 models.py + vacuum.yaml 后再 fan-out。

## 9. 交付物清单（S4）

- `scripts/dev.bat` / `scripts/check.bat`、`README.md`（启动/架构/目录说明）、`docs/SETUP.md`（高德 Key、知了标讯注册指引）、示例数据 `data/sample_enterprises.csv`（50 条真空下游企业）、`data/sample_tenders.csv`（20 条招标公告，其中 ≥8 条命中设备词）、端到端验收报告（写入 docs/ACCEPTANCE.md）。
