# SPEC-AI · 实时推送轮（Agent B 认领）

> 立项：2026-11-05 W53 · PRODUCT_GAP 第五轮 Top1：剑鱼"实时推送"付费点 vs
> 我们每 2 小时扫播。信号已经"早"，感知也要"快"。

## 调研吸收

- 本项目 tasks SSE 已具备成熟形态（AI_Find_Customer 吸收：快照回放+心跳+
  多订阅者）——**发现一个潜在 bug**：tasks 页 EventSource 无法带 Authorization
  头，SSE 端点的 admin_only 依赖实际一直在静默 401（E2E 只测页面渲染没测
  SSE 流）。本轮根治：**current_user 增加 query `?token=` 回退**（SSE 场景
  标准做法；单进程同源部署，风险可控）
- DB 轮询式 SSE（PK 游标 + 3s 间隔）优于跨线程内存桥：单进程部署下实现简单、
  崩溃自愈、天然多客户端

## AI1 认证回退（顺带修 latent bug）

- `current_user`：无 Bearer 头且无 X-Api-Key 时，回退 `?token=` query 参数
- tasks SSE 的既有 EventSource 从此真正通过认证

## AI2 信号实时流（GET /api/signals/live?token=）

- SSE：接入即回放最新 8 条（快照，晚加入不丢上下文）→ 3s 轮询 `id > 游标`
  增量推送 → 15s 心跳保活
- 载荷：{type:"signals", items:[{id,type,title,ent_name,pub_date,url}]}

## AI3 前端全局监听（AppShell）

- 登录态挂一个 EventSource（登录/登出生命周期管理）
- 前台：Toast「🔔 新信号」节流 10s；后台（document.hidden）：
  Web Notification（已授权时），点击聚焦窗口
- 关闭标签页自动断连（EventSource 原生行为）

## AI4 订阅即时命中

- daily_incremental / proposal_gd / proposal_hb 扫描尾部调
  run_subscription_rules（水位幂等，零重复）——订阅用户从"2 小时"变
  "扫描完成即达"

## 边界

- 不做 WebSocket（SSE 够用且零新依赖）；不做多进程广播（单进程部署前提）
- Web Notification 需用户授权一次（浏览器原生提示，拒绝则只走 Toast）
