# SPEC-BZ · 全局搜索轮：Ctrl+K 三实体 typeahead（Agent B 认领）

> 立项：2026-11-07 W59 · 命令面板已存在但只搜企业。头部平台标配是
> **全局搜索**（HubSpot/attio：一个框搜一切）。升级为三实体分组。

## BZ1 GET /api/search?q=（新文件 api/search.py）

- 企业（名称 contains，意图分降序，6 条：id/name/行业/省市/同行标）
- 联系人（姓名 contains，join 企业未删，5 条：id/name/企业）
- 标讯（标题 contains，5 条：id/title/type）
- 空词返回三空组；contains() 参数化（Mimosa 认可形态）

## BZ2 命令面板升级（AppShell CommandPalette）

- 三分组渲染（企业→详情页直达 / 联系人→其企业详情 / 标讯→标讯中心
  带关键词），页面跳转组保留
- 标题/占位符更新"全局搜索"

## BZ3 测试 tests/test_spec_bz.py

- 三实体各自命中 + 分组隔离 + 空词三空组 + 已删企业不泄露

## 边界

- typeahead 规模（≤16 行/次），LIKE 全扫描在小表毫秒级，不做 Meili 依赖
- FTS5 深度搜索留 P2（现有 contains 已覆盖前缀/子串日常场景）

## 落地记录（2026-11-07 W59）

- `api/search.py` 三实体 typeahead（企业意图分降序 6/联系人 5/标讯 5，
  contains() 参数化，已删企业全链路排除）。
- CommandPalette 升级三分组：企业行带行业·省市·同行标并**直达详情页**
  （原为列表页带关键词）；联系人→其企业详情；标讯→标讯中心带词。
- E2E 同步：journey 第 10 步占位符与企业命中行为更新（改 UI 须同步
  E2E 断言——本轮又验证一次）。
- 门禁：pytest 330 + build + E2E 7 全绿。
