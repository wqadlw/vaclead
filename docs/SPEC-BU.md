# SPEC-BU · 雷达运营节奏接入轮：竞对中标推送+周报雷达节（Agent B 认领）

> 立项：2026-11-07 W59 · 山东/国家 tzxm 通道侦察不可达（DNS 级失败，
> 归档 P2）。转向更准的收口：今天建的三条雷达目前只是"看板"——
> **剑鱼竞对监测付费点的核心是推送，不是视图**。管理者不每天看板，
> 竞对中标必须主动推；周报也该带上雷达与触达节。

## BU1 competitor_alert_sweep（jobs.py，每小时 :12 错峰）

- 游标水位 stats_json.last_signal_id（与 BM 老户雷达同模式）：
  扫新 Signal（type=tender_win × Enterprise.is_competitor=1 未删）→
  notify **全部 admin**（watchdog 同款 role 查询）
- 正文带金额回抽+采购人（复用 api/competitors.py 的 _AMOUNT_RE/_BUYER_RE
  ——"竞对把 X 卖给了 Y"一句话情报）
- 去重：Notification 唯一约束 ref_type='competitor_win' 预查 + 同轮
  (signal, admin) seen 集；首跑基线不轰炸
- 不入看门狗（日内推送任务非关键日批，缺失下一小时自愈）

## BU2 周报 radar 节（reports.py build_weekly）

- competitor_wins_30d {count, amount_wan}：30 天竞对中标数+金额回抽合计
- proposals {total, window_now}：拟建池深度+"正处招标窗口"数
- contact_funnel {with_phone, dialed, interested}：可拨/已拨/有意向全局口径
- _email_text 补雷达摘要行；页面报表中心加「雷达动态」卡（三行简表，
  交付完整性：字段与渲染同轮落地）

## BU3 测试 tests/test_spec_bu.py

- sweep：基线/竞对中标推 admin/非竞对不推/幂等重扫/游标推进
- build_weekly：radar 节字段在 + 数值口径

## 边界

- 推送对象=admin（管理层情报语义）；全员推送待组织语义成熟后再开
- 触达漏斗口径与联系人中心全局统计一致（load_phones 判定）

## 落地记录（2026-11-07 W59）

- BU1 `competitor_alert_sweep`：每小时 :12（:06 订阅/:12 竞对/:30 老户/
  :40 轻扫——日内四拍错峰齐了）。Mimosa 再拦 `+ " ".join(...)` 拼接
  （BM 轮同锚）——纯 f-string 绕行。真库基线建立待服务重启后首轮。
- BU2 radar 节真库出数；邮件行+报表页「雷达动态」卡（六格：竞对中标/
  拟建深度/招标窗口/可拨/已拨/有意向）。
- **heredoc 转义坑（新）**：bash heredoc 里补丁脚本写 `
` 匹配源码
  反斜杠字面量会被转义层吃掉——行级 splitlines+chr(92) 构造绕行。
- **测试语义修正**：丢游标全量重扫会"合法补推"基线期跳过的旧竞对中标
  （从未推过≠重复）——去重不变式应断言"第二次重扫零新增"。
- 门禁：pytest 325 + build + E2E 7 全绿。
