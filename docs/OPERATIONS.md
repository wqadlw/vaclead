# vaclead 运维手册（OPERATIONS）

> 配套 README.md。面向部署与日常运维，2026-10-25 版。

## 1. 部署形态

**单进程生产部署**（推荐）：前端静态导出由 FastAPI 托管，一个 8000 端口承载全部。

```bat
:: 首次/发版：构建静态前端（产物 frontend/out）
cd frontend && npx next build

:: 生产启动（后端自动托管 out 并做 SPA 回退）
cd backend
set VACLEAD_JWT_SECRET=<随机32字节hex>
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

开发模式（双进程热更新）：`scripts\dev.bat`（后端 :8000 / 前端 :3000）。

## 2. 环境变量

| 变量 | 必填 | 说明 |
|---|---|---|
| `VACLEAD_JWT_SECRET` | 生产必填 | 未设置时用进程级随机密钥（**重启即全体会失效**，启动日志会告警） |
| `VACLEAD_DB` | 否 | SQLite 路径，默认 `data/vaclead.db` |

## 3. 定时任务（随进程启动）

| 时刻 | 水位名 | 说明 |
|---|---|---|
| 02:00 | recycle_leads | 公海回收：超 N 天未跟进退回公海 |
| 03:30 | daily_incremental | ggzy 宽词 + ccgp 细词，近 7 天 |
| 04:00 | refresh_enterprise_stats | 中标画像：enterprise_stat 全量聚合 |
| 04:05 | notify_sweep | 订阅企业新信号 → 站内通知 |
| 04:07 | automation_sweep | 自动化规则扫播（动作链） |
| 04:08 | notice_contacts | ccgp_buyer 详情页抓联系人/电话/地址（每次 ≤30 页，幂等） |
| 04:10 | backup_sqlite | backup API → `data/backups/`，滚动保留 7 份 |
| 08:05 | digest_flush | 静默期通知聚合出舱（SPEC-G） |

**调度治理（SPEC-I I0）**：全部任务统一经 `_register_cron` 注册——
①注册前 `inspect.signature().bind()` 防呆，参数不匹配的任务跳过注册并打 error 日志
（曾有 refresh_all_stats 漏传 session 静默 failed 三天才被发现）；
②全部任务落 `sync_watermark`（失败 last_status=failed + 错误详情），不再有静默任务；
③统一 `misfire_grace_time=3600, coalesce=True, max_instances=1`。
新增定时任务必须走 `_register_cron`，需要 DB 会话的写成自开 SessionLocal 的零参 sweep。

水位查询：`SELECT * FROM sync_watermark`（失败任务 last_status=failed + stats）。
启动补增量：距上次 >26h 或从未跑过（且有数据）→ 后台自动补一轮 daily_incremental（G4）。

## 4. 备份与恢复

- 备份：`data/backups/vaclead_YYYYMMDD_HHMMSS_*.db`（每日 04:10 自动，保留 7 份）
- 恢复：停进程 → 用备份文件覆盖 `data/vaclead.db` → 启动
- 校验：`python -c "import sqlite3; print(sqlite3.connect(r'备份路径').execute('PRAGMA integrity_check').fetchone())"`

## 5. 数据采集合规守则（红线）

1. 只采官方主动公开数据（ggzy 数据服务 / ccgp 搜索与公告正文含法定联系方式）
2. 固定 UA、限速（ggzy 1.5s / ccgp 3-6s + 详情页 1s）、指数退避、限流即停
3. 不绕验证码、不破解 WAF、不爬登录后商业产品
4. 联系人数据全部带 evidence_claim 溯源到具体公告

**ggzy 宽词分级（SPEC-L）**：`ggzy_name_terms_core`（20 词，每日 03:30 增量用）+
`ggzy_name_terms_extended`（25 词长尾，全量扫描/手动任务用）——每词一次请求成本
（1.5s 限速），勿把 extended 塞进每日增量。词表全在 `config/industrial.yaml`
（工业领域垂直，2026-10-31 起；vacuum.yaml 归档可回滚），类目=买家行业、强词用复合词。

**ccgp 限流应对**：触发"频繁访问"拦截页时冷却 ≥1 小时；手动全量扫描单日 ≤2 轮。

## 6. 已知约束与运维操作

- **schema 演进**：create_all 只建缺失表。**给已有表加列需手工执行**（或写一次性脚本）：
  `python -c "import sqlite3; sqlite3.connect(r'data/vaclead.db').execute('ALTER TABLE ...')"`
  并在本文件登记变更记录（下方 §8）
- **改表级约束（UNIQUE 等）必须重建表**（SQLite 不支持 ALTER CONSTRAINT），官方 12 步：
  `PRAGMA foreign_keys=OFF` → 建 `X_new`（含新约束）→ `INSERT INTO X_new SELECT ... FROM X`
  → `DROP TABLE X` → `ALTER TABLE X_new RENAME TO X` → 重建索引 → `PRAGMA foreign_key_check`
  （非空则回滚）→ 提交。**先建新后删旧**（先 rename 旧表会破坏触发器/外键引用）。
  手工执行后务必登记 §8 并核对 `sqlite_master` 的 DDL
- **Meili 检索投影重建**：Meili 数据丢失/不一致后，调一次任意导入或采集任务即自动 sync_all；
  或 `python -c "from app.db import SessionLocal; from app import search; ..."` 手动全量
- **日志**：`data/logs/vaclead.log` 按天滚动保留 14 份（logrotate.py，随进程启动挂载）；
  logger：vaclead.http / vaclead.jobs / vaclead.collectors.* / vaclead.auth
- **采集任务卡死**：进程重启会把 running 任务标记 error（启动恢复机制）；SSE 断线靠 checkpoint 轮询兜底

## 7. 监控要点

- `/api/health`：就绪度 + 计数 + next_action（带 token 调用）
- 响应头 `X-Process-Time-Ms`：>1500ms 的 /api 请求会打 slow log（vaclead.http logger）
- 日志 logger：vaclead.http / vaclead.jobs / vaclead.collectors.* / vaclead.auth

## 8. Schema 变更记录

| 日期 | 变更 | 方式 |
|---|---|---|
| 2026-10-25 | 全部建表 | create_all（13 表 + FTS） |
| 2026-10-29 | signal 唯一约束 (url,enterprise_id,type) → **(url,enterprise_id,type,title)** | 表重建（12 步手工，SPEC-I I1） |
| 2026-10-30 | contact +email/decision_role/verify_state；automation_rule +cond_has_email_decision | 幂等 ALTER（PRAGMA table_info 检查后 ADD COLUMN，SPEC-J J2） |
| 2026-10-30 | 新增表：audit_log/user_api_key/user_target/subscription_rule（J1/J6/J7）、product/quote/quote_item（J3）、lead_form/lead_capture_log（J5） | create_all 自动建（重启生效） |

## 9. 账号

- admin/admin123（管理员）、sales/sales123（销售）——生产环境**必须立即改密**
