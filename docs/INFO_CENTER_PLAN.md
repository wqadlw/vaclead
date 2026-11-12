# 信息中心功能组 · 详细开发文档（INFO-CENTER PLAN）

> 第二十次功能轮设计稿（用户指令：信息中心升级为功能组，下挂多个信息
> 功能页；调研对标 Tableau Catalog / Microsoft Purview / Salesforce
> Data 360 统一血缘 / Adobe 导入历史——大厂数据管理门户四大标配：
> **数据目录、血缘追溯、新鲜度监控、导入历史**）。
>
> 现状：`/data` 数据资产总览已落地（来源矩阵/批次台账），本计划把它
> 升级为**功能组**并补齐缺失的信息功能页。

---

## 一、愿景与用户故事

**昊志机械（山东）获客场景下，"信息"的用户故事：**

| 角色 | 故事 | 对应功能 |
|---|---|---|
| 销售 | "我今天要打的名单从哪批数据来的？号码可靠吗？" | 批次台账 + 字段溯源 |
| 销售 | "上周导入的那批对照表，跳过了哪些、为什么？" | 导入历史 |
| 运营 | "昨天的采集任务都跑成功了吗？哪个源停摆了？" | 采集运行史 + 新鲜度 |
| 运营 | "企查查查到的工商信息从哪个口录入最顺？" | 信息源矩阵 + 回填通道 |
| 管理员 | "全库 2.7 万家企业的信息质量全貌如何？谁在维护？" | 数据质量（已有）+ 溯源查询 |
| 管理员 | "销售要的名单导出去了吗？谁导的？" | 导出中心 |

**一句话定位**：信息中心 = 昊志获客数据资产的**总台账 + 总调度台**，
让每一条信息"有来源、有批次、有时效、有去处"。

---

## 二、功能组结构（侧栏导航重构）

```
信息中心（组，替代现"信息中心"单页入口）
├── 数据资产    /data            ✅ 已落地（总览带/来源矩阵/批次台账）
├── 导入历史    /data/imports    🆕 一期（批次详情/行级成败/文件记录）
├── 采集运行史  /data/jobs       🆕 一期（水位可视化/任务运行日志/失败告警）
├── 字段溯源    /data/prov       🆕 二期（字段级来源查询/来源分布统计）
├── 导出中心    /data/exports    🆕 二期（统一导出入口+导出历史）
└── 数据质量    /quality         ✅ 已落地（链接跳转，不重复建）
```

**导航语义**：侧栏"工具"组中的"信息中心"变为子组展开（或独立一级组，
视条目总数定——>4 页升级独立组，与"情报雷达"组并列）。

---

## 三、页面详细规格

### 3.1 数据资产 `/data`（✅ 已落地，本轮微调）

来源矩阵（11 源：企业数/可拨/最早最近入库/批次数）+ 批次台账 + 总览四卡。
微调项：来源行点击 → 下钻该来源批次列表（页内锚点滚动，v1 无新端点）。

### 3.2 导入历史 `/data/imports`（🆕 一期）

**用户价值**：导入失败/跳过原因不再随 Toast 丢失——行级成败可追溯
（Adobe Import History 模式）。

**数据模型（新表 import_log）**：

```sql
CREATE TABLE import_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,            -- enterprises | gongshang_backfill | tenders | paste
    filename TEXT,                 -- 上传文件名（粘贴为 paste）
    operator TEXT NOT NULL,        -- 操作人
    total_rows INTEGER,
    inserted INTEGER, merged INTEGER, skipped INTEGER,
    skipped_details TEXT,          -- JSON [{row, reason}] 全量（不再 ≤10 截断）
    mapping TEXT,                  -- JSON 列映射（复现当时解析）
    created_at TEXT NOT NULL
);
```

**写入点**：imports.py 三个导入端点 + gongshang-backfill 成功路径
统一记 log（dry_run 不记）。

**页面**：
- 列表（时间倒序）：时间 / 类型 Tag / 文件 / 操作人 / 新增·合并·跳过
  / 详情按钮
- 详情 Modal：skipped_details 全量滚动区 + mapping 回显（审计语义：
  当时的解析决策可复现）
- **筛选**：类型 / 操作人 / 日期范围

**API**：`GET /api/imports/history?kind=&operator=&days=`（分页）

### 3.3 采集运行史 `/data/jobs`（🆕 一期）

**用户价值**："昨天的采集都成功了吗"一屏见底；失败任务有日志可查。

**数据**：sync_watermark 全量（freshness 已做）+ **task_checkpoint
运行明细**（task_id/kind/status/progress/state_json——采集任务级运行
史，现无 UI）。

**页面**：
- 上半：水位 SLA 表（从 quality/freshness 复用数据源，含状态色点）
- 下半：**任务运行明细**（task_checkpoint 倒序）：
  task_id / kind（采集源）/ status（running/done/failed）/ progress
  / 开始时间 / 耗时
- **筛选**：kind / status / 最近 N 天
- 失败行展开 state_json 错误详情

**API**：`GET /api/data/job-runs?kind=&status=&days=`（新读
task_checkpoint，分页）

### 3.4 字段溯源查询 `/data/prov`（🆕 二期）

**用户价值**：SPEC-PROV 溯源目前只在单企业详情可见——**全局视角**：
"全库的法定代表人都是哪来的？"→ 评估来源可信度。

**功能**：
- 来源分布统计：各字段（法人/注册资本/经营范围/uscc）× 各来源
  （风鸟搬运/对照表回填/公告采集）的计数矩阵
- 查询：按来源反查企业清单（如"看所有对照表回填的字段"）
- 时效视角：按打标时间分布（回填频率趋势）

**API**：`GET /api/data/prov-stats`（prov_json 全表聚合）；`GET
/api/data/prov-entries?field=&source=`（明细分页）

### 3.5 导出中心 `/data/exports`（🆕 二期）

**功能**：统一导出入口（联系人/企业/标讯/简报四类，带当前筛选提示）
+ **导出历史**（谁/何时/导了什么/行数——导出动作记 import_log 同表
kind=export）。企业级审计需求（数据外发可追溯）。

### 3.6 数据质量 `/quality`（✅ 已落地，保持独立）

覆盖漏斗/缺口卡（CTA 已精准直达）/共享号码/查重复核/新鲜度仪表盘。
信息中心以链接卡引流（"数据质量运维 →"）。

---

## 四、数据模型汇总

| 表 | 状态 | 说明 |
|---|---|---|
| import_log | 🆕 一期 | 导入/导出历史（全量 skipped_details 持久化） |
| task_checkpoint | ✅ 已有 | 采集任务运行史（加 UI 即可） |
| sync_watermark | ✅ 已有 | 水位（freshness 已用） |
| enterprise.prov_json | ✅ 已有 | 字段级溯源（SPEC-PROV） |
| enterprise.source_batch_id | ✅ 已有 | 批次追溯（POI 补打批次 = 一期增强） |

---

## 五、分期计划

| 期 | 内容 | 工程量 |
|---|---|---|
| 一期 | import_log 表 + 三端点打点 + 导入历史页 + 采集运行史页 + 导航组重构 + POI 批次补打 | 中（1-2 轮） |
| 二期 | 字段溯源查询页 + 导出中心（含导出历史） | 中 |
| 三期 | 源健康度评分（产出效率/失败率）/ 数据保留策略管理 | 小 |

**依赖**：无外部凭据依赖，全部本地数据。

---

## 六、非目标

- 不做数据目录元数据管理全量能力（Purview 级——单团队获客系统用不到）
- 不做实时血缘（字段级 WHERE 变更追踪——SQLite 触发器成本高，溯源
  按写入点打标已够）
- 不做权限分级的数据视图（全员可见数据资产，导入/导出操作已有
  admin/sales 权限）

---

## 七、验收

1. 侧栏出现「信息中心」功能组，五个入口可达
2. 导入一批对照表 → 导入历史出现该批次（含 skipped 明细全量可查）
3. 采集任务失败 → 采集运行史可见失败行+错误详情
4. 全量门禁（后端 pytest + E2E + 390 双零）持续绿
