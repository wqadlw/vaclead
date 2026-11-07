# SPEC-BT · 客户旅程时间线轮：全源合流（Agent B 认领）

> 立项：2026-11-07 W59 · 详情页现有「信号时间线」只有信号单源；头部 CRM
> （HubSpot engagements/twenty timeline，refs/ 已深读）的形态是**全源合流
> 按时间排好**——打开客户即见完整故事：公告/电话/跟进/报价/合同/回款/
> 装机/备注。Playbook"客户资产账本"的载体落地。

## BT1 GET /api/enterprises/{eid}/timeline（新文件 api/timeline.py）

八源合流，统一 {id: "type:数字", type, ts, title, detail, meta, url}：
- signal（pub_date 优先）/ call（Contact.called_at+7 态）/ followup
  （经线索）/ note（EnterpriseNote）/ quote（状态+金额）/ contract
  （金额+状态）/ payment（分期 label+金额+状态）/ unit（装机型号+
  install_date）
- ts 归一 ISO 前 19 位，降序合并；每源取 40-80 条上限，全局 limit 60
- type 过滤参数；Mimosa 安全构造（== 链/or_ 动态）

## BT2 前端：详情页「信号时间线」升级为「客户旅程」

- 左轨时间线（border-l + 类型色点 + lucide 图标）：Megaphone 信号/
  Phone 电话/MessageSquare 跟进/StickyNote 备注/FileText 报价/
  ScrollText 合同/Receipt 回款/Wrench 装机
- 统一渲染：类型徽标+标题+ts+证据/详情行；信号保留原文链
- tab 改名「客户旅程」，数据独立 fetch（不塞 detail 大对象）

## BT3 测试 tests/test_spec_bt.py

- 种八源数据 → 合流数量/降序/ts 归一/type 过滤/404

## 边界

- Todo 不入时间线（待办是未来不是历史）；只读端点零写路径
- 每源上限防止长尾客户拉爆；时间粒度=秒级字符串比较（同项目全 ISO 口径）

## 落地记录（2026-11-07 W59）

- `api/timeline.py`：八源合流端点（signal/call/followup/note/quote/
  contract/payment/unit），统一条目结构 ts 降序；线索族经 lead_ids
  or_ 链挂接；Todo 刻意不入线（待办是未来不是历史）。
- `components/JourneyTimeline.tsx`：左轨色点+图标时间线（framed-sheet
  无阴影，信号类型徽标复用；禁紫令牌约束下八色映射）。
- 详情页「信号时间线」升级「客户旅程」（itemKey 保持 signals 连续），
  数据独立 fetch 不塞 detail 大对象。
- 测试坑（AX 教训两次命中）：seed=False 下 enterprise_note.user_id
  FK——user 表空，user_id=0 撞 FK；修法=显式建 User + 隔离清单补
  User。门禁：pytest 323 + build + E2E 7 全绿。
