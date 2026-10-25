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

| 时刻 | 任务 | 说明 |
|---|---|---|
| 02:00 | 公海回收 | 超 N 天未跟进退回公海 |
| 03:30 | 每日增量采集 | ggzy 宽词 + ccgp 细词，近 7 天 |
| 04:00 | 中标画像刷新 | enterprise_stat 全量聚合 |
| 04:05 | 通知扫播 | 订阅企业新信号 → 站内通知 |
| 04:08 | **联系人回填** | ccgp_buyer 详情页抓项目联系人/电话/地址（每次 ≤30 页，幂等） |
| 04:10 | SQLite 备份 | backup API → `data/backups/`，滚动保留 7 份 |

水位查询：`SELECT * FROM sync_watermark`（失败任务 last_status=failed + stats）。

## 4. 备份与恢复

- 备份：`data/backups/vaclead_YYYYMMDD_HHMMSS_*.db`（每日 04:10 自动，保留 7 份）
- 恢复：停进程 → 用备份文件覆盖 `data/vaclead.db` → 启动
- 校验：`python -c "import sqlite3; print(sqlite3.connect(r'备份路径').execute('PRAGMA integrity_check').fetchone())"`

## 5. 数据采集合规守则（红线）

1. 只采官方主动公开数据（ggzy 数据服务 / ccgp 搜索与公告正文含法定联系方式）
2. 固定 UA、限速（ggzy 1.5s / ccgp 3-6s + 详情页 1s）、指数退避、限流即停
3. 不绕验证码、不破解 WAF、不爬登录后商业产品
4. 联系人数据全部带 evidence_claim 溯源到具体公告

**ccgp 限流应对**：触发"频繁访问"拦截页时冷却 ≥1 小时；手动全量扫描单日 ≤2 轮。

## 6. 已知约束与运维操作

- **schema 演进**：create_all 只建缺失表。**给已有表加列需手工执行**（或写一次性脚本）：
  `python -c "import sqlite3; sqlite3.connect(r'data/vaclead.db').execute('ALTER TABLE ...')"`
  并在本文件登记变更记录（下方 §8）
- **Meili 检索投影重建**：Meili 数据丢失/不一致后，调一次任意导入或采集任务即自动 sync_all；
  或 `python -c "from app.db import SessionLocal; from app import search; ..."` 手动全量
- **采集任务卡死**：进程重启会把 running 任务标记 error（启动恢复机制）；SSE 断线靠 checkpoint 轮询兜底

## 7. 监控要点

- `/api/health`：就绪度 + 计数 + next_action（带 token 调用）
- 响应头 `X-Process-Time-Ms`：>1500ms 的 /api 请求会打 slow log（vaclead.http logger）
- 日志 logger：vaclead.http / vaclead.jobs / vaclead.collectors.* / vaclead.auth

## 8. Schema 变更记录

| 日期 | 变更 | 方式 |
|---|---|---|
| 2026-10-25 | 全部建表 | create_all（13 表 + FTS） |

## 9. 账号

- admin/admin123（管理员）、sales/sales123（销售）——生产环境**必须立即改密**
