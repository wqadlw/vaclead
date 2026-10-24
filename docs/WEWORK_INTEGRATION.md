# P3 企微集成设计（对标 mochat，免费 API 边界内）

> 2026-10-25 依据 mochat 全链路深读产出。吸收记录见 refs/STUDY.md 第五轮。
> 本文档是 P3 动工时的实施契约：表结构、API 边界、闭环流程全部在此定稿。

## 0. 能力边界结论（先读这个）

**免费 API 足够覆盖全部需求**（只需"客户联系" secret + 一个自建应用）：
联系我二维码（contact_way）、add_external_contact 回调、send_welcome_msg（1 分钟窗口内）、
mark_tag 打标签、transfer_customer 客户继承、groupchat 群管理、JS-SDK 侧边栏。

**付费不可得（全部不采纳）**：会话存档（敏感词/聊天记录侧边栏，每席位付费+so 扩展）。

**最大边界**：⚠️ 企微**没有**服务端"手机号→加好友"API。
线索→私域的唯一正确路径（mochat ContactBatchAdd 实证）：
平台导入手机号并分配 → 销售侧边栏看到"待添加"→ JS-SDK `navigateToAddCustomer` 跳原生页 →
**销售手动确认发送申请** → 回调 `add_external_contact` 即确认加成功。
平台负责导入/分配/提醒/追踪，加好友动作永远由人完成（且每人每日有频控）。

## 1. 表结构（SQLite，vaclead P3 建这五张）

### wework_config（单企业，学 mochat mc_corp 但砍多租户）
```sql
CREATE TABLE wework_config (
  id INTEGER PRIMARY KEY,
  corp_id TEXT NOT NULL,               -- 企业 ID
  contact_secret TEXT NOT NULL,        -- 客户联系 secret（应用→客服→客户联系）
  agent_secret TEXT,                   -- 自建应用 secret（侧边栏 OAuth 用）
  token TEXT, encoding_aes_key TEXT,   -- 回调验签
  nickname TEXT,                       -- 企业简称（显示用）
  enabled INTEGER NOT NULL DEFAULT 1
);
```

### wework_contact（客户本体，学 mc_work_contact）
```sql
CREATE TABLE wework_contact (
  id INTEGER PRIMARY KEY,
  wx_external_userid TEXT UNIQUE NOT NULL,  -- 企微侧唯一 ID
  unionid TEXT,
  name TEXT, nickname TEXT, avatar TEXT,
  type INTEGER NOT NULL DEFAULT 1,          -- 1 微信用户 / 2 企微用户
  follow_up_status TEXT DEFAULT 'none',     -- none/following/won/repeat（对齐 lead 状态机）
  lead_id INTEGER REFERENCES lead(id),      -- ★ mochat 没有的：反向关联拓客线索
  created_at TEXT NOT NULL
);
```

### wework_contact_employee（客户×员工关系，必留 add_way/state）
```sql
CREATE TABLE wework_contact_employee (
  id INTEGER PRIMARY KEY,
  contact_id INTEGER NOT NULL REFERENCES wework_contact(id),
  wx_userid TEXT NOT NULL,             -- 跟进的员工
  remark TEXT, description TEXT,
  add_way INTEGER,                     -- 来源枚举（1扫描二维码/3搜索/...企微枚举）
  state TEXT,                          -- ★ 渠道参数：我们烧入 'lead-{线索id}'
  status INTEGER NOT NULL DEFAULT 1,   -- 1正常/2删除/3拉黑
  created_at TEXT NOT NULL,
  UNIQUE(contact_id, wx_userid)
);
```

### wework_channel_code（渠道活码，学 mc_channel_code 三 JSON 字段）
```sql
CREATE TABLE wework_channel_code (
  id INTEGER PRIMARY KEY,
  config_id INTEGER NOT NULL,          -- 企微 contact_way 配置 id（热更用）
  name TEXT NOT NULL,
  wx_qrcode_url TEXT,                  -- 企微返回的二维码
  lead_id INTEGER,                     -- ★ 比 mochat 更进一步：活码直接绑线索（展会/广告位每线索一码）
  tags_json TEXT NOT NULL DEFAULT '[]',       -- 扫码自动打的标签
  employee_wx_userids TEXT NOT NULL DEFAULT '[]',  -- 排班员工（轮询分配）
  welcome_message TEXT,                -- 欢迎语（text/link 类型）
  active INTEGER NOT NULL DEFAULT 1
);
```

### wework_callback_log（回调幂等+审计）
```sql
CREATE TABLE wework_callback_log (
  id INTEGER PRIMARY KEY,
  msg_id TEXT UNIQUE,                  -- 企微 MsgId（幂等去重）
  event_type TEXT, change_type TEXT,   -- event/change 拼路径
  payload TEXT NOT NULL,               -- 解密后原始 XML/JSON
  processed INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
```

标签体系简化：P3.1 不做标签镜像表——直接用企微标签作 source of truth，
本地只在 wework_contact 上存 `tags_json` 冗余快照（自用规模够用，回调变更时刷新）。

## 2. 回调接入（学 mochat 三件套：验签→队列→事件映射）

```
GET  /api/wework/callback?cid=        # 验签 echostr（URL 配置时企微验证）
POST /api/wework/callback?cid=        # 事件接收
```
- 验签：token + timestamp + nonce 字典序 SHA1（mochat 走 EasyWeChat，我们手写 ~20 行）
- 解密：AES-CBC + base64（encoding_aes_key），标准 WeWork 加密协议
- 幂等：msg_id 查 wework_callback_log，重复直接 200
- 异步：FastAPI BackgroundTasks 投递（自用规模不用队列），消费者按
  `change_type` 映射：add_external_contact / del_follow_user / change_external_tag /
  add_half_external_contact ...
- **add_external_contact 处理链**（学 mochat 闭环）：
  1. upsert wework_contact + contact_employee（存 add_way/state）
  2. 解析 state：`lead-{id}` → 给 lead 自动打"已加微"标记 + 溯源确认
  3. state 匹配 channel_code → 打活码标签
  4. WelcomeCode **1 分钟内**调 send_welcome_msg 发欢迎语
  5. 记 callback_log processed=1

**errcode 统一处理**（mochat 没做，我们补）：42001/40014 token 过期→强制刷新重试 1 次；
45009 频控→退避 60s；其余非 0 记 llm_calls 式错误日志不重试。

## 3. 闭环流程（vaclead 视角）

### 线索→私域（批量导入加好友）
```
lead 状态到 intent → 一键"生成待添加清单"（含手机号的线索）
→ wework_import_batch 表（学 mochat：手机号+标签+分配销售）
→ 销售侧边栏"待添加"列表 → navigateToAddCustomer 跳原生页
→ 人工确认申请 → 回调确认 → lead 自动流转 + 溯源
```

### 展会/内容获客（渠道活码）
```
创建活码（绑 lead_id 或 tags）→ 二维码印出去 → 扫码加微
→ 回调 state 溯源 → 自动打标签+欢迎语 → 平台侧 lead 状态联动
```

### 侧边栏（销售在企微里看拓客画像）
```
FastAPI 托管 H5 单页 → OAuth 拿员工身份 → getCurExternalContact
→ 查 vaclead：这是不是我们的线索？评分/信号/跟进建议/证据句直接展示
→ 可选 sendChatMessage 把企业档案发到聊天
```
这是 mochat 没有的差异化：**侧边栏展示拓客平台的挖掘结果**（评分理由、意图信号、
跟进建议），把平台价值带到聊天现场。

## 4. 实施分片（P3 动工时按此切）

| 片 | 内容 | 依赖 |
|---|---|---|
| P3.1 | wework_config + 回调验签 + contact/contact_employee 落库 + 幂等 | 企微自建应用 |
| P3.2 | 渠道活码 CRUD + state 溯源 + 欢迎语 | P3.1 |
| P3.3 | 待添加清单（lead→手机号导入→分配）+ 回调确认联动 lead | P3.1 |
| P3.4 | 侧边栏 H5（OAuth + jssdk 签名 + 画像展示） | P3.1 + 前端可信域名 |
| P3.5 | 客户继承（transfer + 轮询 transfer_result） | P3.1 |

## 5. 频控与合规提醒

- 加好友：每人每日上限（企微侧控制），平台侧排队分天发放
- 欢迎语：WelcomeCode 1 分钟窗口，回调处理必须快（异步投递）
- 不做会话存档类灰色能力；群发 API（add_msg_template）每月每客户 1 条硬限制，慎用
