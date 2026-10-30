# SPEC-J · 企业级功能扩展轮（GitHub 调研驱动）

> 立项：2026-10-29 · 用户判定"功能太少，不像企业级项目"，要求 GitHub 找可用方案大力发展
> 调研：子 Agent 完成企业级 CRM 功能差距调研（EspoCRM/Krayin/SuiteCRM/listmonk/Chatwoot/twenty 对标，
> 报告结论入 refs/STUDY.md）。硬约束：无外部 Key 可落地优先、SQLite 单进程、Semi+framed-sheet、
> 合规红线（不爬天眼查/企查查、不逆向登录态产品、触达必带退订）。

## 功能面现状（避免重复）

已有：双主干官方采集、10 维筛选+快捷筛选、ICP 四段评分、线索/商机管线、公海规则、
联系人表（公告溯源）、规则引擎动作链+dry-run、通知渠道层+digest、工作台分析+SSE 任务体系、
暗色三态、152 后端测试 + 6 E2E。

## 扩展项（按调研排序，价值 × 无 Key 可落地性）

### J1 审计日志 + API Key（S，企业级信号最强/成本最低）→ 参照 espocrm 审计 + twenty api-key
- `audit_log` 表：id/user_id/action/entity/entity_id/before_json/after_json/created_at
  + ix_audit_entity(entity,entity_id) + ix_audit_user
- SQLAlchemy `before_flush` 事件钩子：对 ENTERPRISE/LEAD/CONTACT/SIGNAL/QUOTE 变更统一落审计
  （insert→after=None；update→diff 字段级；delete→before 保留），上限 5 万行滚动清理入每日调度
- `user_api_key` 表：id/user_id/name/key_hash(sha256)/scopes(read|rw)/last_used_at/revoked
  + `X-Api-Key` FastAPI 依赖：与 JWT 并行的机器认证通道（只读/读写两种 scope）
- 端点：GET /api/audit?entity=&page=（admin only）；POST/GET/DELETE /api/api-keys
- 页面：设置页新增「审计日志」「API 密钥」两个面板；OpenAPI 文档（/docs）即开放平台门面
- DoD：改一条企业名 → audit 有 diff；API Key 可 curl /api/enterprises；tests ≥6

### J2 联系人中心深化（M）→ 参照 espocrm Contacts-Accounts / twenty person
- contact 扩列：email/phone(独立于 title 槽位)/decision_role(决策人角色)/verify_state
  (verified|inferred|none)/title_clean
- 抽取源（全部合规公示信息）：中标公告"项目联系人"（已有）+ 公告正文邮箱正则 + 采购人电话
- 联系人级聚合：投标次数/中标金额合计/最近一次（SQL 聚合进企业画像侧）
- 情报页「联系人」页签升级：决策人标记 + 验证态徽章 + 邮箱展示；规则引擎新条件
  `has_email_decision`（有邮箱的决策人直通 J5 触达）
- DoD：真库回填 email≥5 条；tests ≥5

### J3 产品目录 + 报价单（S/M）→ 参照 espocrm Quotes/Krayin
- 表：product(name/model/spec_json/price/price_unit/tenant_id) + quote(lead_id/status
  draft|sent|won|lost/valid_until/total/remark) + quote_item(quote_id/product_id/qty/price/subtotal)
- 端点：/api/products CRUD；/api/quotes(+items，status 流转，total 由 items 汇总)
- 页面：产品列表页 + 报价单详情（items 可编辑行 + 打印视图 /quotes/{id}/print 纯 HTML 免依赖）
- 漏斗升级：报价数成为 intent→won 之间的新环节进工作台漏斗
- DoD：建产品→建报价→改状态→打印页可开；tests ≥6

### J4 邮件序列触达闭环（L，二期）→ 契约已定，独立轮实施
- 五表：email_account/campaign/sequence/email_message/outreach_event
  （UNIQUE campaign+lead 防重发、scheduled_at+status 索引）
- 发送走 emailengine_client stub（四配置键激活），SMTP 直连后备；warmbly 预算链
  min(邮箱cap, campaign限额, ramp)；回信双链匹配（In-Reply-To↔Message-ID+主题 fallback）+
  _is_auto_reply 过滤；listmonk 式模板变量 + 每信退订链接 + unsubscribe 名单表（合规）
- 端点 /api/outreach/{campaigns,sequences,messages,inbox}；页面：序列编排器 + 会话收件箱
  （回复落 lead 时间线）

### J5 留资表单 web-to-lead（S）→ 参照 espocrm Lead Capture
- 公开端点 POST /api/public/lead-capture/{token}（token=渠道表单，可建多张），
  honeypot 字段 + IP 限流防垃圾；入库走归一引擎（企业匹配/撞单）；来源徽章进时间线
- 管理端：/api/lead-forms CRUD + 表单提交列表；落地页 HTML（iframe 嵌码）

### J6 销售预测与月度目标（S）→ 参照 espocrm Forecasts
- 加权管道 Σ(amount×win_rate) 按预计成交月分组（现有 opportunity 字段全够用，零新表）
- user_target(month, user_id, amount)；工作台新增「本月预测」卡（加权值/目标/达成率）
- DoD：工作台出预测卡；tests ≥3

### J7 订阅获客提前落地（S）→ PRODUCT_GAP P1.5，条件已熟
- subscription 扩 kind=keyword|region（现有订阅监控只按企业）；
  每日增量采集尾：新信号按关键词/地区匹配订阅 → 走现成 digest 通知通道
- 设置页「订阅」面板升级：可加关键词/地区订阅

## 实施顺序（本轮）

**Wave 1（本轮落地）**：J1 审计+API Key → J6 预测卡 → J7 订阅获客（三个 S 项，后端为主）
**Wave 2（下轮）**：J2 联系人中心 + J3 产品报价
**Wave 3（独立轮）**：J4 触达闭环（L 规模，契约已定）
**穿插**：J5 web-to-lead（随 Wave 2）

门禁不变：pytest 全绿 + build + E2E + import smoke；每项带 tests；WORKLOG W32 记录。

## J4 实施细化契约（W34 落地版，2026-10-31）

> 摸底结论：emailengine_client（submit/list_messages/is_configured）与 warmup.py
> （WarmupConfig/apply/classify_health）已是现成地基；J4 在其上建触达层。

### 表（models.py 追加，全部含 tenant_id/utcnow 惯例）

- `email_account`：id/name/email/provider("emailengine"|"smtp")/smtp_host/smtp_port/
  smtp_user/smtp_pass/warmup_started_at(ISO)/daily_cap(int,默认20)/status(active|paused)/
  health(healthy|watch|throttled|quarantined|blocked,默认 healthy)
- `campaign`：id/name/sequence_id(FK)/email_account_id(FK)/status(draft|running|paused|done)/
  daily_limit(int,默认50)/target_filter(JSON:{"score_min":?,"industry":?,"source":?})/
  owner_id/started_at
- `sequence`：id/name/steps(JSON 数组:[{"delay_days":0,"subject":"...","body":"..."},...])
  （listmonk 式：body 支持 {{lead.company}}/{{contact.name}}/{{enterprise.city}}/
  {{sender.name}}/{{unsubscribe_url}} 变量）
- `email_message`：id/campaign_id/lead_id/step_idx/account_email/to_email/subject/body/
  status(queued|sent|failed|bounced|replied)/scheduled_at(Index)/sent_at/message_id/
  thread_id(=首封 message_id)/error + **UNIQUE(campaign_id,lead_id,step_idx) 防重发**
  + Index(status,scheduled_at)
- `outreach_event`：id/message_id/type(sent|open|click|reply|bounced|unsubscribed)/
  payload(JSON)/created_at
- `unsubscribe`：id/email UNIQUE/lead_id/reason/created_at —— 退订名单，dispatch 前必查

### 发送链（app/outreach/ 新包）

- `templates.py`：render(subject_tpl, body_tpl, lead, contact, sender) —— {{var}} 正则替换，
  未知变量替换为 ""；unsubscribe_url 指向 GET /api/public/unsubscribe/{token}
  （token=unsubscribe 表按 email 预生成或 sha256(email+secret)），命中后写名单+outreach_event
- `budget.py`：纯函数 `available_budget(account, campaign, date_iso, session)` =
  min(account.daily_cap, campaign.daily_limit, warmup.apply(...))，
  当日已发数 = COUNT(email_message WHERE account_email=? AND sent_at 当日 AND status='sent')，
  返回 max(0, 预算-已发)；warmup 起始天数由 account.warmup_started_at 推
- `sender.py`：send_one(session, message) —— provider=emailengine 走 emailengine_client.submit；
  provider=smtp 用 smtplib(STARTTLS, 登录, EmailMessage)；返回 (ok, message_id, error)
- `dispatcher.py`：run_outreach_dispatch(session) worker ——
  ①收件同步 sync_inbox：emailengine list_messages → In-Reply-To↔message_id 匹配 + 主题
  "Re: {subject}" fallback → 命中则 message.status=replied + outreach_event(reply)
  + Followup(content="[自动回信] ...") 落 lead 时间线；_is_auto_reply 过滤（主题/正文含
  auto reply|自动回复|out of office|unsubscribe confirmation 等词表则只记事件不落跟进）
  ②发送：campaign(status=running) × queued message（scheduled_at<=now，join lead/contact
  取邮箱，contact 无邮箱用 enterprise 线索邮箱跳过）——按预算链逐条 send，
  已退订跳过并标 failed(error=unsubscribed)；发送即 outreach_event(sent)
- 复用 api/task_runtime.py spawn：POST /api/tasks/outreach-dispatch（SSE 可观测+取消）；
  jobs.py 注册 CRON 每日 09:30/14:30 两次 dispatch（工作时段发送）
- 入队：POST /api/outreach/campaigns/{id}/enqueue —— 按 target_filter 选 lead
  （join enterprise：icp_score>=score_min/industry/source），逐 lead × sequence steps
  生成 email_message（首步 scheduled_at=now，后续=前步+delay_days），UNIQUE 撞重跳过；
  退订名单内 lead 直接排除

### 端点（app/api/outreach.py，router prefix /api/outreach）

- GET/POST /campaigns；PUT /campaigns/{id}（含 status 翻转 running|paused）；
  POST /campaigns/{id}/enqueue（返回 {created,skipped_dup,skipped_unsub}）
- GET/POST /sequences；PUT/DELETE /sequences/{id}（被 campaign 引用时 409）
- GET /messages?campaign_id=&status=&page=（联 lead/enterprise 名）
- GET /inbox（会话视图：message 按 thread_id 分组，status=replied 的在前，含回信正文
  ——从 outreach_event(type=reply).payload 提取）
- GET /unsubscribe-list；DELETE /unsubscribe-list/{email}
- 公开：GET /api/public/unsubscribe/{token}（无鉴权，返回确认页文案 HTML）+ POST 同路径
  （表单确认，写名单幂等）

### 前端（frontend/app/outreach/）

- /outreach 页：双区——序列编排器（sequence 列表+步骤编辑抽屉：delay_days/subject/body
  文本域+变量提示 chips）+ 活动列表（名称/关联序列/账户/状态徽章/今日已发/预算/
  启停按钮/enqueue 按钮+SSE 任务卡复用现有任务页模式）
- /outreach/inbox 页：左会话列表（联系人/主题/最后时间/replied 徽章）右会话详情
  （sent/replied 时间线+回信正文+「去跟进」跳线索页）；AppShell 导航加「触达」组两项
- framed-sheet 约束：无渐变无阴影，青蓝 #1b6c80 强调，Semi 组件 + 现有 lib/api.ts

### 门禁

test_spec_j4.py（预算链纯函数/模板渲染/退订端点/UNIQUE 防重发/回信匹配+auto_reply 过滤/
dispatch 用 stub sender 注入跑通 sent+reply 全链）≥8 用例；
pytest 全量+build+E2E+import smoke 全绿；WORKLOG W34。
