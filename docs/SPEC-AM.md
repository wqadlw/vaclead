# SPEC-AM · 回收站与导出中心（Agent B 认领）

> 立项：2026-11-05 W54 · 26k 数据池暴露两个企业级刚需：①POI 批采混入噪音
> （住宅区/ individual 店）却**没有任何删除端点**——脏数据只能看着；
> ②导出是同步 CSV，26k 行有 HTTP 超时风险。合规红线下的数据治理能力。

## AM1 软删除 + 回收站

- `enterprise.deleted_at` 列（幂等 ALTER；NULL=正常）
- **端点**：
  - `DELETE /api/enterprises/{eid}`（软删，admin 或 owner 语义——企业为团队资产，admin only）
  - `POST /api/enterprises/{eid}/restore`（恢复）
  - `DELETE /api/enterprises/{eid}/purge`（彻底删：级联 signals/contacts/leads/notes/tags，admin only，二次确认参数 confirm=true）
  - `GET /api/enterprises/trash`（回收站列表：deleted_at 倒序）
- **全链过滤**（deleted_at IS NULL）：
  enterprises 列表/搜索/详情/geo/推荐/dashboard KPI/bulletin join/dedupe；
  POI 采集 resolve 命中已删企业 → **跳过不复活不重建**（用户意愿优先）
- **30 天自动清空**：purge_deleted_sweep 每日 03:00（OPERATIONS 登记）

## AM2 导出中心（异步化）

- `POST /api/tasks/export-enterprises`：TaskCheckpoint 异步任务（筛选条件入参）
  → 查询流式写 `data/exports/export_{ts}.csv`（BOM+UTF-8）→ 进度 SSE
- `GET /api/tasks/exports`：导出历史（文件名/行数/大小/筛选摘要/状态）
- `GET /api/exports/{fid}/download`：下载（生成者本人或 admin）
- 企业列表导出按钮改走异步（toast 提示"已入队，完成后在导出记录下载"）
- 前端：设置页新增「导出记录」面板（列表+下载+删除文件）

## 调研依据

- EspoCRM/twenty 回收站模式：软删 + 30 天 Trash 自动清理（EspoCRM 官方行为）
- 大导出业界标准=异步任务+文件中心（ twenty/SFDC 均如此），不做同步大响应

## 边界

- 联系人/信号不单独软删（跟随企业）
- 导出文件保留 30 天（purge cron 一并清理）
- 软删企业的 signals 保留在库（彻底删才级联），bulletin 层隐藏
