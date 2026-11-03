# SPEC-AB · 拟建项目通道：广东备案公示（Agent B 认领）

> 立项：2026-11-03 W46 · 剑鱼核心付费点"拟建项目"的开源对标——比招标公告再早
> 1-3 个月的"早"的极致。通道预研（AA4）：国家平台死、**广东省级平台可达**
> （tzxm.gd.gov.cn，官方备案公示 API 裸调成功）。

## 通道实测（2026-11-03）

- `POST https://tzxm.gd.gov.cn/tzxmspweb/api/publicityInformation/selectConsutinCpyPage`
  body `{"pageNo":1,"pageSize":10}`（gateway 前缀 /tzxmspweb 来自 /js/common.js）
- 返回：`{code:"0", data:{list:[{deptName, baTime, baNum, baCode, workTime, ...}],
  totalRow, totalPage, ...}}`——**官方主动公开的备案公示**（合规 ✓）
- baCode 形如 `91440101MA5AKF2675-26`（前缀=信用代码+后缀，非标准 18 位，不作锚点）
- 列表无项目名/投资额（详情接口未暴露）——本轮做**单位级信号**（该单位上月在
  扩产备案=设备采购前兆），项目级 P2（需逆向 Vue 详情页）

## AB1 采集器（collectors/proposal_gd.py）

- `collect_proposals_gd(max_pages=5)`：翻页拉公示 → 每条：
  - `resolve_enterprise(deptName, source="proposal_gd")`（baCode 非标准 18 位，
    **不作 uscc 锚点**；靠名称归一）
  - Signal(type=**"proposal"**，title=`广东项目备案：{deptName}（{workTime}年度）`，
    url=公示列表页，evidence=`该单位于 {baTime} 在广东省投资项目在线审批监管平台
    完成项目备案（编号 {baCode}）`)
- 幂等：url+title+enterprise 唯一约束天然去重
- 限速 2s/请求；每日 cron 05:00（早于 06:00 前无冲突）

## AB2 前端接入

- 标讯中心：TYPE_CN/TYPE_TAG 加 proposal（「项目备案」，amber 色）
- 快捷 chip「🎯 拟建·备案」（type=proposal）——与「招标中」并列的行动窗口
- 语义标注：备案=扩张前兆，离采购还有数月——**培育线索**非即时行动

## AB3 调度与文档

- main.py cron：`_register_cron("proposal_gd", sweep, 5, 0)`
- DATA_ACQUISITION §4.3：拟建行更新为"🟡 广东通道落地（tzxm.gd.gov.cn 官方公示 API）"
- OPERATIONS §3 登记 05:00 任务

## 测试与门禁

- test_spec_ab.py：mock httpx → collect → 建档+信号+幂等；标讯中心 proposal 筛选
- 全量门禁 + E2E（E2E_PORT=8017）

## 边界

- 项目级详情（名称/投资额/建设内容）P2（逆向 Vue 详情页）
- 单省（广东）；他省按此模板复制（P1 逐省）
- 不做微信推送（P3）
