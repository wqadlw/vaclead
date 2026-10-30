# SPEC-P · Webhooks 出站（开放集成，配 J1 API Key 组成开放平台故事）

> 立项：2026-10-31 W37 · Agent A 认领。参照 refs/STUDY.md §开放集成调研
> （EspoCRM webhooks + svix 最佳实践）。文件边界见 WORKLOG 认领条目。

## P1 数据模型（models.py 追加两表）

- `webhook`：id/name/url/event_types(JSON ["lead.create","enterprise.update",
  "signal.create","quote.update",...])/secret(创建时服务端生成 token_hex(24))/
  active INTEGER 1/created_by FK user/created_at
- `webhook_delivery`：id/webhook_id FK CASCADE/event/payload TEXT(JSON)/
  status("pending"|"ok"|"failed")/attempts INTEGER 0/last_error TEXT NULL/
  next_attempt_at TEXT NULL（重试退避）/delivered_at NULL/created_at
  + Index("ix_wd_status", "status", "next_attempt_at")

## P2 事件总线（app/webhookbus.py）

- `emit(session, event: str, payload: dict)`：对每个 active 且订阅该事件的 webhook
  插入 webhook_delivery(pending, next_attempt_at=now)；事件白名单外忽略
- 事件源：**before_flush 钩子**（第三处，与 audit/assign 并列，main.py lifespan 安装）——
  enterprise/lead/signal/quote 的 create/update/delete → emit 对应事件
  （entity.create/entity.update/entity.delete）；update 只在**业务字段变化**时发
  （排除 updated_at/last_followed_at 等机器字段，对齐 EspoCRM changed-attributes）
  - payload：{"event": "...", "id": ..., "entity": "lead", "data": {变更后字段}}，
    update 额外 "changes": {field: [before, after]}（复用 audit 的 diff 采集思路但
    **不 import audit 内部**，钩子内自行取 history）
  - 钩子内禁止网络调用/flush——只插 delivery 行（随业务事务原子入队，
    EspoCRM 队列表同构）
- `flush_deliveries(session, *, post_fn=None, now=None) -> dict`：投递引擎
  - 取 pending 且 next_attempt_at<=now（≤50 条）逐条投递
  - POST JSON **数组**（对齐 EspoCRM 批量语义，单事件为一元素数组）+
    头 X-Vaclead-Event / X-Vaclead-Signature: hex(HMAC-SHA256(secret, raw_body)) /
    X-Vaclead-Timestamp（svix 防重放惯例，接收方可验 5 分钟窗）
  - httpx timeout 5s；2xx=ok(delivered_at)；410 Gone=永久停用该 webhook；
    其他失败 attempts+1，next_attempt_at=now+2^attempts 分钟（指数退避），
    attempts>=4 → failed 终态（不再重试，记录 last_error）
  - post_fn 注入点（测试替身）；单条失败不阻断批
- SSRF 防护：`_url_allowed(url)`——默认拒绝 localhost/私网/链路本地
  （ipaddress.is_private/is_loopback/is_link_local + 非 http(s) scheme 拒）；
  settings_kv `webhook_allow_private=1` 显式放行（对齐 EspoCRM
  webhookAllowedAddressList，供本机联调 emailengine 等场景）

## P3 端点（app/api/webhooks.py，admin 管理端）

- GET /api/webhooks：列表（secret 打码显示前 4 位）
- POST /api/webhooks {name,url,event_types}：创建，**响应一次性返回明文 secret**
  （对齐 API Key 惯例）；URL 过 _url_allowed 校验 422
- PUT /api/webhooks/{wid}：name/event_types/active
- DELETE /api/webhooks/{wid}
- GET /api/webhooks/{wid}/deliveries?page=：投递日志（状态/尝试数/错误/时间）
- POST /api/webhooks/{wid}/test：投递一封 {"event":"ping"} 测试事件（走真实投递引擎）
- POST /api/tasks/webhook-flush：手动触发投递（复用任务体系 SSE）；调度：
  main.py `scheduler.add_job(..., "interval", minutes=10)`（_register_cron 是
  hour:minute 粒度，分钟级用 interval；写水位 webhook_flush）

## P4 前端（设置页「Webhooks」面板 + 独立下发，AppShell 零改动）

- webhook 表（名称/URL/事件徽章/启用开关/最近投递状态点）+ 抽屉（name/url/
  事件多选）+ 创建成功弹窗一次性展示 secret（提示"仅此一次"）
- 投递日志 SideSheet（状态/尝试/错误/时间戳）+ 「发送测试」按钮 + 手动投递按钮

## 测试（test_spec_p.py ≥9）

事件白名单外忽略/emit 入队 pending/钩子：建企业→delivery 行自动产生/更新含 changes/
机器字段变更不发事件/_url_allowed 私网拒+公网过+白名单开关/flush 成功投递（post_fn
替身收到的 body 是数组+签名可验证）/410 停用/4 次退避终态 failed/端点 CRUD+secret
打码+test ping 投递/无 webhook 时钩子零开销短路。
门禁：pytest 全量+build+E2E(8010)+import smoke。
