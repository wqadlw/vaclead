# SPEC-AR · 运营可靠性轮：水位看门狗（Agent B 认领）

> 立项：2026-11-05 W55 · 健康检查发现 03:30 daily_incremental 在 POI 长批
> 并发窗口丢失（misfire 未自愈）——靠人肉体检发现靠人肉补跑不是企业级。
> 本轮把"发现异常→自动修复→通知"做成系统能力。

## AR1 水位看门狗（核心）

- `run_watermark_watchdog()`：每日 06:30 检查五个关键日批
  （daily_incremental/proposal_gd/proposal_hb/health_refresh/backup_sqlite）
  ——`last_run_at` 非今日 → **自动补跑**（_run_job_with_watermark）+
  通知 admin（notify_dispatch，"X 任务昨日未执行已自动补跑"）
- 补跑串行执行（SQLite 单写者——看门狗本身也是单线程顺序）
- 补跑结果写 watchdog 自己的水位（自检可观测）

## AR2 长批错峰

- poi_batch.py 启动时检查当前时间：落在 02:00–05:30 cron 密集窗口 →
  打印警告并 sleep 到窗口结束（防呆：脚本运行 30+ 分钟会横跨整个窗口）

## AR3 MCP 写工具（受控开放）

- `convert_lead(eid)` 工具加入 mcp/call（复用 AO2 逻辑）——Agent 可以
  直接把推荐转成线索
- **令牌桶限流**：每 API Key 10 次/分钟（内存桶），超限 429——写操作
  开放的护栏
- tools 清单同步更新 + scope 注记

## AR4 情报页删除入口

- 详情页头部「删除」（admin，确认弹窗）——此前只有列表页能删，
  看着噪音企业进详情页却没手动删（回收站链路最后一米）

## 边界

- 看门狗只管"日批是否执行"，不管业务结果对错（那是水位 failed 的告警域）
- MCP 写工具仅 convert_lead 一个（最小写面），更多写操作按需加
