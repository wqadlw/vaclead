# SPEC-BX · 省份回填轮：city→省 确定性映射（Agent B 认领）

> 立项：2026-11-07 W59 · /quality 缺口卡自己指出的第一个可行动项：
> 省份文本覆盖 204/27,421（0.7%），而 **city 覆盖 25,682（93.8%）**。
> 侦察实证：无 city 者 address 也为空（0 条）——city 归一化是唯一
> 大规模确定性路径；bbox 点位判定因边界误差降级 P2。

## BX1 geoutil 省份归一（纯函数+单测）

- `PROVINCES`：34 省级行政区全名集（值已是省级直接命中）
- `CITY_TO_PROVINCE`：本次真库 159 个 distinct city 逐一甄别后的
  确定性映射（含县级市代管关系：邳州→徐州/海门→南通/余姚→宁波…；
  直辖市辖区小表：徐汇/海淀/顺德/福田/金水/黄岛/北仑…→省）
- `province_from_locality(city)`：脏值规则（市辖区/纯数字码/未知区→
  None，**不发明**）→ 去后缀查表 → 省级直判
- 数据事实：city 值形态混杂（苏州/苏州市/广东省/徐汇区/319900/市辖区）

## BX2 jobs.backfill_province_geo() + POST /tasks/backfill-province

- 幂等只填空省；返回 {city_map: n, skipped: n}（按规则计数）
- 端点复刻 backfill-icp 模式（admin_only，同步执行——纯内存映射无网络）
- 真库执行一次；质量页缺口数字自动改善；未来 POI 新批重跑即可

## BX3 测试 tests/test_spec_bx.py

- 归一：苏州市/苏州→江苏省；广东省→广东省；市辖区/319900/徐汇区外的
  未知区→None；徐汇区→上海市
- 回填：只填空省（已有省不覆盖）；幂等重跑 0

## 边界

- 映射表只收录本次真库出现的地名（可评审的静态事实），不做全国全量
  行政区划表（YAGNI）
- 县级市映射=代管关系事实；冷门市辖区不猜（宁缺不发明）

## 落地记录（2026-11-07 W59）

- geoutil：PROVINCES 34 省级集 + CITY_TO_PROVINCE（真库 159 个 distinct
  city 逐一甄别，含县级市代管与直辖市辖区小表）+ province_from_locality
  脏值规则（市辖区/数字码/冷门区→None 不发明）。
- **真库战果：省份覆盖 204 → 23,820（0.7% → 87%）**，city_map 23,616、
  幂等二遍 0；Top 省=江苏 10,169/广东 8,513/浙江 2,456/安徽 2,420
  （与 POI 采集城市分布吻合——数据自洽交叉验证）。
- POST /api/tasks/backfill-province（admin，同步纯内存）——未来 POI
  新批次采集后重跑即可增量补齐；/quality 缺口卡数字自动改善。
- 门禁：pytest 329 + E2E 7（前端零改动未重 build）全绿。
