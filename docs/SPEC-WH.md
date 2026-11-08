# SPEC-WH：信号出站 Webhook 渠道（群机器人）

> 第十三次功能轮（用户指令开轮）。目标：订阅动态/自动化规则命中 → 推送到
> 企微/钉钉/飞书**群机器人**——销售不打开平台，群里就能看到新信号。
> 群机器人只需建群拉机器人拿一个 URL，**不卡任何外部凭据**（对照第十二次
> 调研"采购差距"清单——这是唯一不依赖凭据/钱的触达腿）。

## 1. 架构定位

notify/channels.py 是 Novu 式渠道注册表（in_app/email/wechat），本次新增
第 4 渠道 `webhook`。偏好/静默时段/digest 全部复用现有机制，零新表
（配置进 settings_kv 两键）。

## 2. 配置存储（settings_kv）

| key | 值 | 说明 |
|---|---|---|
| `notify_webhook_url` | `https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx` | 完整机器人地址 |
| `notify_webhook_type` | `wecom` \| `dingtalk` \| `feishu` | 默认 wecom |

## 3. 后端契约（Agent A）

### 3.1 channels.py

- `CHANNELS["webhook"] = {"label": "群机器人", "enabled": True}`
- `render_group_message(title, body) -> str`：`**[vaclead] {title}**\n{body}`
  超 3800 字符截断（企微 markdown 4096 字节限）。
- `_deliver_webhook(session, user_id, *, kind, title, body, ref_type, ref_id)`：
  1. url 未配置 → `"skipped:not-configured"`
  2. **去重**：Notification 表存在同 `(ref_type, ref_id)` 行 →
     `"skipped:duplicate"`（群消息不可轰炸；调用方水位防重之上再兜一层）
  3. payload 按 type 构造：
     - wecom：`{"msgtype": "markdown", "markdown": {"content": msg}}`
     - dingtalk：`{"msgtype": "markdown", "markdown": {"title": title, "text": msg}}`
     - feishu：`{"msg_type": "text", "content": {"text": msg}}`（纯 text 兼容最稳）
  4. `requests.post(url, json=payload, timeout=10, proxies={"http": None, "https": None})`
     ——**显式禁代理**（继承"死代理毒链路"教训：后台出站请求必须绕系统代理）
  5. 响应校验：wecom/dingtalk 看 `errcode==0`；feishu 看 `code==0`；非 0 →
     返回 `f"failed:{errcode}"`；异常直接抛（notify_dispatch per-channel try 兜住）
- `_deliver_webhook_test(session, url, type) -> dict`：发固定测试消息
  （"[vaclead] 测试消息——群机器人通道已打通"），返回 `{"ok": bool, "detail": str}`。
  供 test 端点复用。

### 3.2 API（misc.py）

| 端点 | 权限 | 行为 |
|---|---|---|
| `GET /api/notify-preferences` | 登录 | 响应扩展：webhook 项 `available = url 已配置`；新增 `"webhook": {"url": "<脱敏>", "type": ..., "configured": bool}`（url 中段打码：保留头 60 尾 12） |
| `PUT /api/notify-webhook` | admin | body `{url, type}`；校验 `https://` 开头 + type 白名单 → 写两键 → `{ok: true}`；非法 422 |
| `POST /api/notify-webhook/test` | admin | 真发测试消息 → `{ok, detail}`（HTTP 层不 500，失败也 200 返回 ok:false） |

### 3.3 单测（tests/test_spec_wh.py）

- payload 构造三格式
- 未配置 → `skipped:not-configured`
- dedup：预置同 ref Notification → `skipped:duplicate`
- monkeypatch requests.post：成功（errcode 0）/ 业务失败（errcode 非 0）/ 超时
- PUT 端点：非 https 422；GET prefs 脱敏断言

## 4. 前端契约（Agent B）

settings 页「通知偏好」tab：

1. 渠道列表自动多出"群机器人"行（channels 来自 API，available 由后端判定；
   未配置显示"未配置"黄字——现有渲染逻辑零改动）。
2. 新增配置卡（渠道列表下方，admin 可见）：
   - 类型 Select：企微 / 钉钉 / 飞书（回填已存 type）
   - URL Input（回填脱敏 url；编辑时覆盖保存）
   - 「保存配置」→ PUT → 成功 Toast + 重新拉 prefs（available 翻绿）
   - 「发送测试消息」→ POST test → ok ? Toast.success : Toast.error(detail)
3. 移动端可用（settings 页已有响应式，无需特调）。

## 5. 非目标

- 不做 per-user 多机器人（全局一个群，第一版够用）
- 不做签名版钉钉（加签机器人后续迭代）
- 不做 email engine 配置（沿旧契约 P2）

## 6. 验收

1. 配置真实企微群机器人 URL → 测试按钮 → 群里收到测试消息
2. 订阅某企业 → 新信号入库 → 群里出现 `[vaclead] 中标：xxx` markdown 卡
3. 同一信号不重复推群（dedup）
4. 349+ 后端全绿、E2E 12 全绿、移动 390 双零不回退
