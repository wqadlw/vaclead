# SPEC-BQ · 联系人中心轮：跨企业拨号工作台（Agent B 认领）

> 立项：2026-11-07 W59 · 第九次调研"使用侧差距"的直接开发回应——
> "打 10 通电话"缺一个日常作业面。现状：14,028 条联系人只能钻进
> 单个企业详情页看，**没有跨企业的拨号工作台**。头部平台（Apollo/
> Revenue.io）的 daily call list 正是这个形态：可拨优先、未触达优先、
> 意图分排序、边打边标。

## BQ1 GET /api/contacts（新文件 api/contacts.py）

- 跨企业联系人工作列表：Contact × Enterprise（未删），cap 2000
- 过滤：call_status（untouched=有电话且未拨；touched；或 7 态直选）/
  has_phone / decision_role / province / q（联系人或企业名）/ limit/offset
- **排序（作业面语义）**：可拨优先 → 企业意图分降序 → 未拨优先 → id 降序
- 每行：联系人（姓名/角色/title/决策链/验证态/号码库/触达结果/证据）+
  企业（名称/省市/行业/意图分/角色）
- stats：可拨数/未触达/已触达/有意向（口径与 SPEC-AZ untouched 一致：
  未拨 = call_result 为空或 pending）

## BQ2 写路径复用

- 快标复用既有 POST /contacts/{cid}/call-result（SPEC-AN，7 态受控枚举）
  ——本轮零新写端点

## BQ3 前端 /contacts「联系人中心」

- KPI 四卡：可拨联系人 / 未触达 / 已触达 / 有意向
- 筛选：触达状态/省份/决策链/关键词
- 行：姓名（→企业详情）+ 角色/决策链标 + **号码 chips（复制+tel: 拨打，
  SPEC-BO 同款）** + CallResultTag 快标 + 企业名/意图分 + 证据句
- **CallResultTag 抽共享组件** components/CallResultTag.tsx（view 页同步
  改导入——SPEC-Z 教训：共享组件不放页面文件）
- 侧栏「联系人中心」（Phone 图标，排在两雷达之后）+ PAGE_TITLES

## BQ4 测试 tests/test_spec_bq.py

- 种：有电话未拨（高意图企业）/ 有电话有意向 / 无电话 / 已删企业的
  联系人 / 决策链人
- 断言：stats 数字、untouched 口径、排序（可拨+高意图在前）、
  已删企业排除、q 跨姓名/企业名、decision_role 过滤

## 边界

- 不做批量标记（SPEC-Y 模式可后补）；不做外呼录音（采购件）
- 电话只在 UI 明文（导出/文档脱敏红线不变）

## 落地记录（2026-11-07 W59）

- `api/contacts.py` 工作列表端点：Python 侧过滤+作业面排序（可拨→
  意图分→未拨→id），stats 口径与 SPEC-AZ untouched 一致；写路径零新增
  （快标复用 SPEC-AN /contacts/{cid}/call-result）。
- **CallResultTag 抽共享组件** components/CallResultTag.tsx（CALL_RESULTS
  常量同迁），view 页改导入——组件归位消除页面文件复制源。
- 前端 /contacts：KPI 四卡（未触达 amber/有意向 green）+ 五维筛选 +
  拨打名单（号码 chips 复制+tel: 拨打+快标 7 态+企业/意图分/证据句）。
  侧栏「联系人中心」（PhoneCall）排在两雷达之后。
- 测试 test_spec_bq.py 2 条：stats 口径+已删排除 / untouched 过滤+
  排序语义+决策链+q 跨名。修一处测试自身漏 has_phone=0（端点行为
  正确）。门禁：pytest 316 + build + E2E 7 全绿。
