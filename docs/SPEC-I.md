# SPEC-I · P0 生产故障修复 + 规模隐患（第三次审计驱动）

> 立项：2026-10-26 · 审计 Agent 发现每日增量管道实际已断
> 调研补充：克隆 litellm（★62k）研究 completion(response_format=Pydantic) 统一 LLM 入口
> （P2 激活参照，记入 STUDY）

## 审计关键发现

| # | 级别 | 发现 |
|---|---|---|
| I0 | **P0** | 接手复核新发现：`refresh_enterprise_stats` 水位亦 failed（10-26 04:00）——`refresh_all_stats(session)` 被调度注册时漏传 job_kwargs，包装器以无参调用 → TypeError。根因是**调度注册无签名防呆**：`add_job` 散落、三种风格混用（带水位包装/裸函数/带 kwargs），一个注册笔误只能等任务 failed 三天才被发现；且 recycle/notify/digest/automation/notice-contacts 五个 cron 失败不落水位（违反 SPEC-B §6"失败不静默"） |
| I0b | **P0** | `_notice_contacts_daily` 用 `from ..api.tasks`（双点越顶层导入）——每日 04:08 必 ImportError 且无水位静默失败，**从未成功过** |
| I0c | **P1** | 测试套件非确定性挂（`database is locked`）：G4 启动补跑在测试库点火——agents 模块建档后，后续模块 TestClient lifespan 满足"有数据+无水位"→ 后台对真网络跑每日增量并**持写事务跨 HTTP**，与用例并发写 SQLite → 导入端点随机 500。修：`VACLEAD_DISABLE_JOBS=1` 环境开关（tests/conftest.py + e2e/server.py 设置），lifespan 跳过补跑与调度注册 |
| I1c | **P1** | （生产级根因）run_daily_incremental ggzy 段持写事务跨网络调用（resolve 落库后开着的写锁去做秒级 HTTP）——SQLite 单写者被长期占住，正是 I0c 锁竞争的生产形态。修：三段式（建档短事务→网络抓取→入库短事务），单主体明细失败降级不炸管道 |
| I1 | **P0** | daily_incremental 水位=failed（10-26 03:30）：signal 唯一约束 (url,enterprise_id,type) **不含 title**，而 jobs.py 每日增量 ggzy 段写 url="" 的 tender_win——同企业第二条不同项目中标即 IntegrityError。04:00/04:10 后续任务水位也未更新（管道连锁中断）。G4 启动补跑反复撞同一错 |
| I1b | **P0** | 真库实跑复现的残余缺陷：ggzy 段按企业攒批、批尾一次 commit，`autoflush=False` 下批内同名记录（同一成交公示重复出现）exists 查不到 pending 写入 → 双双入库撞新约束。真库 10-29 实跑插 9 条信号后在企业 113（泰和县土地成交公示重复行）崩。修：批内 seen_titles 去重 + 单批次 IntegrityError 降级（rollback 跳过该企业，不再炸整条管道） |
| I2 | P0 | 通知列表不过滤 digested=2——flush 后原行与汇总行**重复展示** |
| I3 | P1 | normalize._resolve O(N²)（全表 fuzz/行）——10 万家导入必崩 |
| I4 | P1 | gitignore 缺 frontend/test-results 与 frontend/e2e/tmp（e2e.db 640K 未忽略） |
| I5 | P1 | has_contact limit(5000) 硬截断静默漏企业；导出 Contact 无上限全载 |
| I6 | P2 | LeadCard/leads 不读 enterprise_stat（中标画像未进商机卡）；agents 框架 6 llm stub 零消费（确认保留待激活）；无日志轮转；无 PWA |

## 工作项

### I0 调度治理（接手日新增，先于 I1-I6 收尾）
方案（APScheduler 官方模式 + 失败不静默原则）：
- **统一 sweep 闭包风格**：需要 DB 会话的任务一律包一层零参 sweep 自开 SessionLocal
  （`_refresh_stats_sweep` 补上；对齐既有 `_notify_sweep`/`_digest_flush`/`_automation_sweep`）
- **注册前签名防呆**：提取 `_cron_signature_ok(fn, kwargs)`（inspect.signature().bind()），
  不匹配的任务跳过注册 + error 级响亮告警（防止再靠"failed 三天"才发现注册笔误）
- **失败落水位全覆盖**：全部 6 个 cron 注册统一走 `_run_job_with_watermark`
  （recycle_leads/notify_sweep/digest_flush/automation_sweep/notice_contacts 补齐水位名）
- 回归测试：`_cron_signature_ok(refresh_all_stats, {})` 必须为 False（本 bug 的最小复现）；
  refresh_all_stats(session) 有参调用走 mark_watermark 翻 ok

### I1 信号约束修复（代码+真库迁移已完成，接手日复核确认）
- 唯一约束迁移为 (url, enterprise_id, type, **title**)——与全库 5 处查重口径一致
- 真库迁移已生效：signal 表 322 行，DDL 含 title（sqlite_master 实证）
- 回归测试：同企业两条空 url 不同 title 的 tender_win 必须共存（test_spec_i.py）
- 验证：约束列断言测试同步更新 + 水位翻 ok（与 I0 联合验证）

### I2 通知列表过滤 digested（10 分钟）
- misc list_notifications 排除 digested=2

### I3 normalize 会话级规范名缓存（40 分钟）
- _resolve 第 3 层：缓存 (id, canonical) 列表到 session 级，rapidfuzz.process.extractOne(score_cutoff) 替代逐条 token_sort_ratio
- 测试：等价合并结果不变 + 性能断言（千级名 <100ms）

### I4 gitignore 补全（5 分钟）
- frontend/test-results/ 与 frontend/e2e/tmp/（*.db 已有全局但 tmp 目录确认）

### I5 快捷筛选/导出上限修正（20 分钟）
- has_contact：distinct 全量（去 limit，加 EXISTS 半连接更优）
- 导出 contacts：只按本页 enterprise ids 查询

### I6 记档（不改码）
- STUDY：litellm completion 签名（response_format=type[BaseModel]）+ agents 框架保留判定
- WORKLOG W31 + 记忆 + push
