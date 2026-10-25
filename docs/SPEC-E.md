# SPEC-E · 结构治理与测试补全（第二轮正规流程）

> 立项：2026-10-26 · 基于第二次全面审计（Explore Agent）+ FastAPI 官方 full-stack 模板结构对照
> 原则：**行为零变更**的重构 + 测试补全，每步 140 全绿门禁

## 审计核心发现（驱动本 SPEC）

| # | 发现 | 级别 |
|---|---|---|
| A1 | tasks.py 1219 行：4 个 worker 高度同构（生命周期三分支/信号查重/尾部钩子 **逐字重复** 5 处） | 结构债 |
| A2 | **Todo 唯一约束炸弹**：uq_todo_dedup 含 done 列，同标题二次完成必撞 IntegrityError（todos.py:95） | **真 bug** |
| A3 | **通知撞约束**：automation notify 与订阅扫播对同一 (signal,user) 各插一条，后到者 IntegrityError 且 email 渠道无幂等依据 | **真 bug** |
| A4 | generate_notifications 不走 notify_dispatch（渠道层半接线第二次出现） | 接线债 |
| A5 | 零测试：run_automation_rules/automation API/todos/analytics/intel.contacts | 测试债 |
| A6 | 前端巨页：enterprises 1005 行 / leads 646（LeadCard 内嵌组件每次渲染重建）/ settings 472 | 结构债 |
| A7 | jobs.py:372 notify user_id 硬编码 1 | 设计缺陷 |

## 官方模板对照（fastapi/full-stack-fastapi-template）

路由按域一文件（users 232 行封顶）/ core+crud+models 分层 / alembic 迁移。
vaclead 对齐策略：**不搬目录结构**（我们的域划分已合理），吸收"文件职责单一+大小上限"纪律。

## 工作项（顺序执行）

### E1 真 bug 修复（最高优先）
- A2：toggle_todo 改 toggle 语义为 UPDATE done 列翻转（同标题唯一活动行），或约束去掉 done 列改为 partial unique index（done=0 时唯一）——选后者 + 数据迁移
- A3：channels.py `_deliver_in_app` 预查重 + 撞约束捕获返回 "dedup"（幂等语义）；automation 与扫播竞争路径收敛起见：generate_notifications 改走 notify_dispatch（同修 A4/A7，user_id 从订阅行取）

### E2 后端结构治理（tasks.py 瘦身）
- 提取 `app/api/task_runtime.py`：任务生命周期包装器（cancelled/failed/done 三分支 + 尾部钩子序列 + collector_health 落账 + Thread 启动）
- 提取信号查重 helper `signal_exists(session, ent_id, url, title)`
- 四个 worker 迁移到包装器，tasks.py 预期 1219 → ~700 行
- **行为零变更**：SSE 事件序列/checkpoint 字段逐项对齐

### E3 测试补全（零测试模块）
- test_automation.py：规则 CRUD/链执行/else 分支/dry-run/水位幂等
- test_todos.py：CRUD/toggle 二次完成（复现 A2 修复）/automation 来源
- test_analytics.py：summary 聚合数字正确性
- 预期 140 → 165+

### E4 前端结构治理
- enterprises/page.tsx：抽 hooks（useFilters/useViews/useInlineEdit）+ 子组件（QuickPicks/SummaryBar/AdvancedFilterModal）→ 目标 ≤500 行
- leads/page.tsx：LeadCard 提文件（修内嵌重建）→ 目标 ≤500 行

### E5 沉淀
- WORKLOG W27 / STUDY（官方模板对照结论）/ 记忆 / git 提交

## 门禁
每工作项后 pytest 全绿 + build；E2 后真库采集任务回归（SSE 事件序列对比）。
