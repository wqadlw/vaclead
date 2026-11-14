# SPEC-MULTI：多源采集扩容地基 + 工商深解析 + 任务结果可观测

> 2026-11-14 · 调研来源：两路摸底（采集架构 recon + refs/ 参考库扫描）+ 网上调研。
> 采纳模式：keelead SourceManager（注册表/单源故障隔离/getStats）、china-company-check-mcp
> 字段契约（data-fields.md）、fire-enrich 结果契约（RowEnrichmentResult 失败明细）。
> 红线遵守：DATA_ACQUISITION §4.2（不爬登录后商业产品）；keelead 源实现是造数演示只搬架构。

## 背景（摸底实锤）

1. **source 无注册表**：`Enterprise.source` 自由 Text，来源字面量散布后端 15 文件 76 处；
   新增一个源要改 12 处枚举点（标签/SLA/通道归并/徽章/筛选）。四省 gd/hb/js 的水位
   SLA 就因漏登记而永远不上仪表盘（quality.py:208 skip 未登记 job）。
2. **工商快照浅解析**：`gongshang.py::_parse_chinacheck` 只取 4 字段（注册资本/经营范围/
   行业/成立日期），漏 registrationStatus（存续状态——**注销/吊销=线索死刑，最值钱的字段**）、
   legalRepresentative、staffSize、paidInCapital、registeredAddress、formerNames；
   `search_chinese_company` 按名搜索工具完全未用（enrich Stage A 按名找 uscc 的免费通道）。
3. **潜伏 bug**：`api/intel.py:908` 给 `Signal(...)` 传 `source="recon:ccgp"`，但 Signal 模型
   无 source 列 → TypeError → 被外层 except 吞掉 → **侦察产出的信号从未落库**。
4. **任务结果只有计数**：totals 无新采企业清单（上轮实测断点"采完看新面孔要去企业库筛"）；
   enrich 失败只 `totals["failed"]+=1` 无明细（fire-enrich RowEnrichmentResult 契约缺口）。
5. **四省 job 四份复制粘贴**：jobs.py:300-588 同构 ~250 行，抽基类后新增省 ~20 行。

## S1 — Source 注册表（地基，keelead 模式）

### 新文件 `backend/app/sources.py`

```python
@dataclass(frozen=True)
class SourceDef:
    key: str                    # 主 source 值，如 "ggzy"
    label_cn: str               # 信息中心/徽章中文，如 "政采网"
    category: str               # tender | proposal | poi | import | enrich
    aliases: tuple[str, ...] = ()   # 同源变体，如 ("ggzy_buyer",)
    sla_job: str | None = None      # freshness 仪表盘登记的 job_name
    cron_desc: str = ""             # 定时巡采说明（设置页展示）

SOURCES: dict[str, SourceDef]   # 唯一注册表；所有来源必须注册
def source_label(key: str) -> str     # 未注册回退 key 本身（现状行为）
def channel_variants(key: str) -> tuple[str, ...]  # enterprises 筛选/归并用
```

### 初始注册（全量收编现有来源）

| key | label_cn | category | aliases | sla_job |
|---|---|---|---|---|
| ggzy | 政采网·公共资源 | tender | ggzy_buyer | hourly_tender |
| ccgp | 中国政府采购网 | tender | ccgp_buyer | hourly_tender |
| tender | 知了标讯 | tender | — | hourly_tender |
| amap | 高德地图 POI | poi | — | — |
| proposal_gd | 广东备案 | proposal | — | proposal_gd |
| proposal_hb | 湖北备案 | proposal | — | proposal_hb |
| proposal_js | 江苏备案 | proposal | — | proposal_js |
| proposal_zj | 浙江备案 | proposal | — | proposal_zj |
| csv | 表格导入 | import | — | — |
| gongshang | 工商补数 | enrich | — | enrich_pipeline |
| manual | 手工录入 | import | — | — |

**注意**：`_FRESHNESS_SLA` 现有键以 job_name 为键（hourly_tender 等），registry 的 sla_job
必须与 quality.py 现键对齐；新增补齐 proposal_gd/hb/js 三行（现有遗漏修复）。

### 消费方改接线（行为保持，硬编码→注册表）

- `api/data.py:18-31` SOURCE_CN → registry（label_cn）
- `api/quality.py:19-39` _FRESHNESS_SLA → registry sla_job 派生 + 保留 registry 外的
  非 source 类 job（daily_incremental/backup 等运维项仍在 quality 本地）
- `api/enterprises.py:246-248,707-710` + `search.py:201-204` channel map → channel_variants()
- `api/analytics.py:96` buyer 归并 → channel_variants()[0]
- `api/proposals.py:88` 通道徽章 map → registry（按 key 精确匹配，**废除 js 兜底**，
  未匹配回退 key 前 2 字母或 key）

### 新端点 `GET /api/sources`（keelead route.ts 模式）

```json
{ "sources": [ { "key": "ggzy", "label": "政采网·公共资源", "category": "tender",
    "enterprise_count": 1704, "last_harvest": "2026-11-14 03:30", "enabled": true } ] }
```
- enterprise_count：`GROUP BY source` 动态（含 aliases 归并到主 key）
- last_harvest：该 source 最近 created_at（取 max），无则 null
- enabled：SettingsKV `source_disabled`（JSON array of key），默认全启用
- `POST /api/sources/{key}/toggle` → 切换 enabled；disabled 的源：jobs.py 对应 sweep
  函数入口短路（cron 与手动任务 worker 均检查，返回 totals 带 `skipped_disabled: true`）；
  enrich-gongshang 同理查 gongshang 注册态

## S2 — chinacheck 工商快照深解析 + 按名搜索

### 模型/迁移（db.py 幂等 ALTER 清单 + models.py）

| 新列 | 类型 | 说明 |
|---|---|---|
| biz_status | TEXT | 工商存续状态：存续/在业/注销/吊销/迁出/清算… |
| biz_status_at | TEXT | 状态核验时间 ISO |
| staff_size | TEXT | 规模带，如 "100-499人" |
| paid_in_capital_wan | REAL | 实缴资本（万元） |
| former_names | TEXT NOT NULL DEFAULT '[]' | JSON 数组，曾用名 |

### `gongshang.py::_parse_chinacheck` 扩展

- registrationStatus → biz_status（+ biz_status_at=now ISO）
- legalRepresentative → legal_rep（只补空）
- staffSize → staff_size（只补空）
- paidInCapital → paid_in_capital_wan（万元归一，只补空）
- registeredAddress → address（只补空）
- formerNames → former_names JSON（只补空）
- **每个写入字段 mark_prov(field, source="工商补数·chinacheck", by="enrich")**（PROV 机制复用）
- 现有 4 字段解析行为不变（回归保护）

### `ChinaCheckProvider.search_by_name(name) -> dict | None`

- 走 tools.md 的 `search_chinese_company`（JSON-RPC 同通道）
- enrich-pipeline Stage A（按名找 uscc）：ggzy 失败后追加 chinacheck 按名兜底，
  命中回填 uscc 并继续后续 enrich 阶段；totals 记 `stage_a_chinacheck_hits`

### biz_status 业务规则

- 解析回填时若 biz_status ∈ {注销, 吊销, 迁出, 清算}：企业 `tags` 追加 `biz_dead`
  （幂等）；**不动 intent_score**（历史信号仍有效），由前端红标警示
- 企业详情/列表接口透出 biz_status（enterprises.py 列表 serializer + 详情）

## S3 — 任务结果可观测（fire-enrich 契约 + 新面孔预览）

### worker 侧产出（state_json 新增键）

- ggzy/ccgp/POI/enrich-* worker：`new_enterprises: [{id, name, province, industry_name}]`
  （cap 50，按入库顺序）
- enrich-gongshang / enrich-pipeline worker：`failures: [{enterprise_id, name, stage, error}]`
  （cap 50，error 截 200 字）；失败同时 SSE `_log` 一条明细
- 兼容：旧 totals 键全部保留；results 端点对缺键返回空数组

### 新端点 `GET /tasks/{task_id}/results`

```json
{ "task_id": "...", "kind": "ggzy_harvest", "status": "done",
  "new_enterprises": [{"id":123,"name":"青岛海诺泰科","province":"山东省","industry_name":"检验检测"}],
  "failures": [], "totals": { ... } }
```

### intel.py:908 bug 修复

- 删除 `Signal(...)` 的 `source=` 形参（Signal 无此列；溯源由 recon 报告与 url 去重承担）
- 回归测试：recon 流程后 Signal 表确有新信号（现路径 TypeError 被吞的实证断言）

### 前端 tasks 页 done 分支

- done 事件后拉 `/tasks/{task_id}/results`，任务卡片渲染「本次新采企业」列表
  （名称+省份+行业，行点击进企业详情，上限展示 50）；failures 非空则红字明细列表
- 复用既有列表视觉（framed-sheet，无新组件库）

## S4 — ProposalScanJob 基类（行为保持重构）

- jobs.py 抽 `run_proposal_scan(job)` 配置驱动：source_key / fetch_page / unit_extractor /
  title_fmt / province / with_subscription(gd,hb=True) / unit_from_project(js,zj)
- 四个 run_proposal_*_scan 缩成 ~15 行配置子类；**测试不改一字全绿为验收标准**
- 收益：下一轮新增省政采站点（ZhaoBiaoSpider 图谱 33 端点）边际成本 90→20 行

## 验收标准（门禁）

1. 后端 pytest 全量通过（现 415 + 新增；四省 job 重构零测试改动）
2. `npm run build` 通过
3. 新测试覆盖：registry 完备性（DB 实际 source 值 ∈ 注册表）、/api/sources 形状与
   toggle、chinacheck 深解析（含 prov_json 断言）、search_by_name 兜底、results 端点、
   enrich failures、recon 信号落库回归
4. 行为回归红线：来源矩阵/质量分层/企业筛选/分析归并输出与重构前一致（同 SQL 口径）
5. Mimosa 审查通过（走 Write/Edit 提交）

## 不做（下一轮）

- boss-zhipin 招聘信号源（CDP 进程外架构决策值得独立一轮）
- 新省政采站点（S4 完成后边际成本降 4 倍再做）
- TaskCheckpoint 真断点续跑（现在语义是观测恢复，够用）
- 风险数据（处罚/失信）—— chinacheck 免费快照不含，合规路径仍是风鸟人工通道
