# SPEC-AN · 触达运营闭环（Agent B 认领）

> 立项：2026-11-05 W54 · 13,389 个可拨电话就位，但"打了之后"是空白。
> 调研（Revenue.io/Trestle/Salesloft）：受控 picklist（非自由文本）、
> 连接类与对话类分离、**结果驱动自动下一步**、结果关联管道漏斗。

## AN1 电话结果标记（Contact 级受控枚举）

- `Contact.call_result`（受控枚举，pending 默认）+ `called_at`
- 枚举（连接类→对话类分层）：
  - `pending` 未拨 / `no_answer` 无人接 / `voicemail` 语音信箱 / `invalid` 空号错号
  - `connected` 已接通 / `interested` 有意向 / `not_interested` 无需求
- `POST /api/contacts/{id}/call-result` {result}——更新+时间戳
- contacts 序列化带 call_result/called_at

## AN2 推荐引擎接入（结果驱动自动下一步）

- 全部号码 invalid 的企业 → **排除**（数据卫生工作流）
- 任一联系人 interested → **+25 且理由**「已表示兴趣——建议立即转线索」
- no_answer ×2 → 降权 10（换时段再试提示由 AF2 行动建议承担）

## AN3 触达漏斗（结果关联管道）

- `GET /api/analytics/call-funnel`：近 30 天 dials→connected→interested
  →invalid 率（Salesloft 连通率分析语义）
- 工作台推荐卡头显示本周触达小计（dials/connected）

## AN4 POI 噪音批量清洗（用 AM1 回收站能力）

- `POST /api/enterprises/bulk-cleanup` {patterns, dry_run}——模式列表
  （苑/家园/小区/便利店/餐饮/宿舍…）匹配企业名 → 预览计数或执行软删
- 真库跑一次（住宅区/个体店首获批量清理），数字入 WORKLOG

## 边界

- 不做外呼系统集成（手动拨打+标记；自动化外呼是 P3）
- 通话时长/录音不做（合规与自用定位）
