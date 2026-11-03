# SPEC-AF · 营销闭环与执行个性化（Agent B 认领）

> 立项：2026-11-04 W50 · PRODUCT_GAP 第四轮 P2 三件（邮件营销/跟进时机/看板个性化）
> + SPEC-AD 遗留欠账（合同入分析环）合并一轮。主题：让已有的触达基础设施
> **可度量**，让销售**知道什么时候动**，让工作台**长在用户手里**。

## 调研吸收（本轮依据）

- **listmonk 跟踪实践**：打开=1×1 像素（Apple MPP 预取会虚高）；点击=链接改写
  到自有域名 + 302 跳转（**点击比打开可靠**）；root URL 必须可配；按活动开关
- **销售时机研究**（Cirrus Insight/InsideSales/ZoomInfo 统计聚合）：
  新线索 5 分钟内响应资格率 ×21（平均 B2B 响应 42 小时=巨大差分空间）；
  周四/周三 16-17 点最佳外呼窗；周二 8 点/15 点邮件优；**5+ touches** 才成交而
  多数销售 1-2 次就放弃；行为信号（打开/点击）触发即时跟进
- **twenty 个性化**：视图/布局偏好存服务端（非 localStorage），跨设备一致

## AF1 邮件跟踪与活动报表（open/click → 报表）

- **新模块 `app/outreach/track.py`**：
  - 无状态 token（复用退订 token 形态：`base64(msg_id|mac)`，mac=sha256(盐:msg_id)[:16]）
  - `wrap_links(body)`：正文 URL 改写为 `/api/public/track/click/{token}?u=`（退订链接除外）
  - `to_tracked_html(body, base, token)`：纯文本→HTML（转义+换行+链接化）+ 注入像素
- **入站端点**：`GET /api/public/track/open/{token}` → 1×1 GIF + OutreachEvent(open)
  （每 message 首次计 open，后续事件照记防 MPP 虚高）；`GET /api/public/track/click/{token}?u=`
  → OutreachEvent(click, payload={u}) + 302
- **发送链注入**：Campaign 加 `track_enabled`（默认 1）；dispatcher 渲染后若开启
  则生成 html——emailengine 走 `submit_full(html=, track=False)`（自采事件防双计）；
  SMTP 走 `add_alternative(html)`（multipart/alternative）
- **报表端点** `GET /api/outreach/campaigns/{cid}/stats`：sent/unique_open/unique_click/
  replied/bounced/unsub + 率（open_rate/click_rate 按送达口径）+ 最近 10 条事件流
- **前端**：/outreach 活动列表加打开率/点击率列 + 详情 SideSheet 统计块

## AF2 跟进时机行动建议（新模块 `app/advice.py`）

`build_lead_advice(session, lead) -> list[{code,title,why}]`，按优先级：

| code | 触发 | 建议 |
|---|---|---|
| first_touch | 新线索 ≤48h 未首跟 | 首联黄金窗（响应研究 ×21） |
| hot_signal | 最新信号 <24h | 趁热跟进（附信号标题） |
| opened_no_reply | 7 天内有 open 无 reply | 今天 16-17 点外呼（Cirrus 窗） |
| at_risk_save | 生命周期=at_risk | 挽回动作（附健康分） |
| switch_channel | 跟进 ≥5 次无回复 | 换渠道/暂缓（5+ touches 研究） |

- 端点：`GET /api/leads/{lead_id}/advice`（owner/admin 可见）
- 前端：线索「记录」侧板顶部建议条（amber 高亮）

## AF3 工作台个性化

- **后端**：`GET/PUT /api/prefs/dashboard`——settings_kv 键 `pref:dashboard:u{id}`，
  JSON `{order: [section keys], hidden: [...]}`（服务端存，跨设备一致）
- **前端**：工作台各区块注册 key（kpi/trend/funnel/upcoming/signals…），
  「布局」齿轮弹层：显隐勾选 + 上移/下移；持久化到 prefs

## AF4 合同余款入分析环（SPEC-AD Phase 2 欠账收官）

- forecast 增 `contract_backlog`：active 合同 (amount − paid milestones) 合计；
  预测卡显示「在手合同余款」——赢单后金额不再从管线消失
- 前端预测卡补 backlog 行

## 边界

- 打开率受 MPP 预取虚高：报表以**点击率为可靠度优先**展示（口径注记）
- 跟踪依赖 outreach_public_base 可被收件人访问（公网部署前提，settings_kv 已有）
- AF3 **v1 只做显隐**（7 区块），上移/下移排序 P2——右列 order 化需先把区块
  提成组件，显隐已覆盖主要价值（实施记录见 WORKLOG W50）
