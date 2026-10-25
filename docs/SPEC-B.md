# SPEC-B · Phase B 数据质量升级（DDL/契约级）

> 立项：2026-10-25 · 状态：已批准（自主流程） · 上游：docs/UPGRADE_REPORT.md §三 Phase B
> 流程：摸底 → 调研 → 本 SPEC → 实施 → 门禁（pytest 全绿 + 构建 + 审计对齐）→ 沉淀（WORKLOG/STUDY/memory）

---

## §1 摸底结论

**数据现状（2026-10-25 全量采集中）**：企业 270+（ggzy 114 / ggzy_buyer 62 / ccgp 16 / ccgp_buyer 24 / csv 54，持续增长），信号 267+；uscc 覆盖 114；icp>0 仅 54（CSV 独占）→ 官方渠道企业评分饥饿确认。

**审计发现**（Explore 子 Agent 报告，摘要）：
- `normalize.py` 归一已有 rapidfuzz≥90 自动合并，但**缺 uscc 精确层**；且 80-89 分的"疑似同企"被静默新建，无人复核。
- `search.py`（Meili 层）与 `emailing/warmup.py` 无测试覆盖（记录为已知缺口，本轮不扩）。
- 任务线程 session 管理：`SessionLocal()` with 块均正确关闭；collectors 代理环境变量清理重复出现在 3 处（可容忍，不重构）。

## §2 调研结论

| 主题 | 结论 | 依据 |
|---|---|---|
| 实体归一选型 | **确定性三级 + 复核队列**（MDM match/survivorship/stewardship）；splink(SQLite 后端) 作为企业数 >2 万或精度下滑时的触发式升级路径，本轮不引入（零训练数据下确定性+人工复核精度更高、零新依赖） | [Splink 文档](https://moj-analytical-services.github.io/splink/index.html)、[Tilores 四库对比](https://tilores.io/content/best-open-source-entity-resolution-and-record-linkage-libraries-splink-zingg-dedupe-and-when-to-move-beyond-them)、dedupe/recordlinkage 均为 pandas 系 |
| 工商补全 | china-check MCP 实测已要求 token（README 与实际不符）→ **可插拔 Provider 模式**：协议就绪、适配器按注册情况启用；本轮先落**自有中标数据画像聚合**（零依赖即可解锁线索质量） | 实测 curl initialize → invalid_token |
| 调度 | 复用已有 APScheduler BackgroundScheduler（main.py lifespan 已跑公海回收 02:00 cron） | 代码现状 |
| 备份 | SQLite backup API（`Connection.backup`，WAL 安全）+ 保留 7 份滚动 | SQLite 官方推荐（比文件复制安全） |

## §3 设计决策

1. **复核优先于静默**：归一置信度分三档——`≥90 自动合并 / 80-89 落复核队列 / <80 新建`。宁可多一条人工复核，不做静默误合并。
2. **uscc 是最高优先锚点**：新增 tier0（uscc 精确）+ alias 命中时的 uscc 回填（官方来源带 uscc，命中已有无 uscc 企业则补上，永久提升后续归一）。
3. **画像聚合用自有数据**：enterprise_stat 表由中标信号聚合（bid_count/累计金额/最近中标/头部采购人）——不发明数据，全部可溯源。
4. **工商 Provider 协议化**：`GongshangProvider` Protocol + OfflineProvider（回填 est_date 等）+ ChinaCheckProvider/AliyunProvider 存根（token/key 配置即启用），settings_kv 持有凭证。
5. **调度全部落水位表**：sync_watermark 记录每任务 last_run/last_status/stats，失败不静默。

## §4 B1 企业归一升级 + 复核队列

### DDL（models.py 新增）

```python
class ReviewQueue(Base):
    __tablename__ = "review_queue"
    __table_args__ = (UniqueConstraint("candidate_id", "target_id", name="uq_review_pair"),)
    id: int PK
    candidate_id: int  FK enterprise.id   # 疑似重复的新企业
    target_id: int     FK enterprise.id   # 建议归并到的主企业
    score: float                          # rapidfuzz 0-100
    reason: str  default="fuzzy_name"     # fuzzy_name | uscc_diff_name
    status: str   default="pending"       # pending | merged | rejected
    created_at/updated_at
```

### resolve_enterprise 升级（normalize.py）

```
tier0  uscc 精确（新）：来源记录带 uscc → 查 enterprise.uscc 命中即合并（score 1.0）
tier1  精确名（现状）
tier2  别名表（现状）+ 命中时 uscc 回填（目标无 uscc 且来源 uscc 非空 → 写入）
tier3  rapidfuzz：≥90 自动合并（现状）；80-89 → 新建候选企业 + 写 review_queue
       （每新企业只落一次：UniqueConstraint 防重；target=最高分者）
tier4  新建（现状）
```

### API（api/review.py 新路由）

```
GET  /api/review-queue?status=pending        → {items:[{id, candidate:{...}, target:{...}, score, reason}]}
POST /api/review-queue/{id}/merge            → 把 candidate 的 alias/signal 全部迁到 target，
                                               删 candidate，status=merged；signal 迁移后重算 intent
POST /api/review-queue/{id}/reject           → status=rejected（两企业确认为不同主体）
```

权限：admin_only。merge 事务内完成迁移+删除+intent 重算。

## §5 B2 企业画像增强 + 工商 Provider 协议

### DDL

```python
class EnterpriseStat(Base):
    __tablename__ = "enterprise_stat"
    enterprise_id: int PK FK enterprise.id
    bid_count: int default=0           # 中标次数（tender_win 信号数）
    bid_amount_total_wan: float default=0.0
    bid_amount_max_wan: float | None
    last_win_date: str | None
    top_buyer: str | None              # 按成交额最大采购人
    updated_at
```

### 行为

- `enrich_stats(session, enterprise_id)`：从 signal(type=tender_win) + evidence/原文聚合（金额从 raw_excerpt 正则回抽 `成交价([\d.]+)万元`）。
- `POST /api/enterprises/{id}/refresh-stat`（admin）：单企业刷新；全量刷新并入 B3 每日任务。
- 前端：EnterpriseDrawer 展示"中标画像"区块。

### 工商 Provider（collectors/gongshang.py）

```python
class GongshangProvider(Protocol):
    name: str
    def enrich(self, uscc: str) -> dict | None: ...
      # 返回 {registered_capital_wan?, business_scope?, industry?, est_date?}；查不到/未配置返回 None
```

- `OfflineProvider`：仅整理库内已有字段（noop 语义，供测试与降级）。
- `ChinaCheckProvider(token)` / `AliyunQccProvider(key)`：适配器骨架 + 明确 NotImplemented-when-no-credential，settings key：`gongshang_provider` / `chinacheck_token` / `aliyun_qcc_key`。
- `POST /api/tasks/enrich-gongshang`（admin）：对有 uscc 且缺注册资本的企业批量跑当前 provider（未配置凭证时返回 400 提示， OfflineProvider 可跑）。

## §6 B3 每日增量调度 + SQLite 备份

### DDL

```python
class SyncWatermark(Base):
    __tablename__ = "sync_watermark"
    job_name: str PK
    last_run_at: str | None
    last_status: str | None           # ok | failed | partial
    stats_json: str default="{}"
```

### APScheduler 新增任务（main.py lifespan）

| 时刻 | 任务 | 内容 |
|---|---|---|
| 03:30 | daily_incremental | ggzy 宽词 days=7 + ccgp 34 细词 days=7（复用现有 harvest/scan 后台函数，同步等待小批次） |
| 04:00 | refresh_enterprise_stats | 全量 enrich_stats + Meili sync |
| 04:10 | backup_sqlite | backup API → `data/backups/vaclead_YYYYMMDD_HHMM.db`，保留最近 7 份 |

全部任务写 sync_watermark；失败写 failed + stats。执行函数放 `app/jobs.py`（纯函数，可被测试直接调用）。

## §7 测试与门禁

- test_normalize.py：tier0 uscc 合并 / uscc 回填 / 80-89 落队列 / ≥90 自动合并（现有用例保绿）。
- test_review.py：队列 CRUD、merge 迁移完整性（signals/alias 移动+candidate 删除+intent 重算）、reject、幂等。
- test_enrich.py：金额回抽正则、聚合正确性、provider 协议（Offline 命中/未配置 400）。
- test_jobs.py：水位写入、备份文件生成且可打开（PRAGMA integrity_check）、保留 7 份滚动。
- 门禁：pytest 全绿 + next build + 审计报告对齐（不新增裸 except）。

## §8 验收标准（DoD）

1. 同 uscc 异名企业合并为一档；80-89 分疑似进复核队列而非静默建档。
2. 复核页可完成 merge/reject，merge 后信号与证据不丢失。
3. 官方中标企业抽屉出现"中标画像"（次数/累计金额/最近中标/头部采购人）。
4. 三个定时任务手动触发跑通且水位表有记录；备份文件通过 integrity_check。
5. 全量测试绿；WORKLOG/STUDY/memory 已更新。
