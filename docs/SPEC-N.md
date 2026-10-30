# SPEC-N · 组织与分配（团队/用户管理/线索自动分配/业绩榜）

> 立项：2026-10-31 W36 · Agent A 认领。补 PRODUCT_GAP §一③"过程监管"+§一④"业绩排行榜"。
> 调研参照：EspoCRM（Teams/Role/Assignment Rules Round-Robin·Least-Busy，其论坛并发轮转
> 失效 bug → 本设计游标持久化）、SuiteCRM 团队 ACL、twenty RBAC。文件边界：避开 SPEC-M
> （signals/enterprises/imports/normalize/jobs/theme.ts）与 J4 已提交文件。

## N1 用户管理（admin）

### User 扩列（幂等 ALTER + OPERATIONS §8 登记）
- `email TEXT NULL`、`active INTEGER NOT NULL DEFAULT 1`、`team_id INTEGER NULL`（FK→team）
- 禁用语义：active=0 → 登录 401「账号已禁用」+ current_user 依赖同样拒绝（API Key 通道同步）

### 端点（app/api/org.py，全部 admin）
- GET /api/users：id/username/display_name/email/role/team_id/active/created_at
- POST /api/users：username/password/role(admin|sales)/display_name/email/team_id；
  用户名唯一 409；密码 ≥6 位 422
- PUT /api/users/{uid}：display_name/email/team_id/role/active/password(重置)
  - 防锁死：不可禁用自己；不可禁用/降级最后一个 active admin（422）
- DELETE 不提供（禁用即软删，审计与历史归属保全）

## N2 团队

- `team` 表：id/name(唯一)/leader_id FK user NULL/created_at
- 端点：GET/POST /api/teams；PUT /api/teams/{tid}（name/leader_id）；DELETE（无成员时）
- **线索可见范围扩展（leads.py，唯一语义变更点）**：
  - sales：自己的 + 公海（现状不变）+ **若为某 team.leader：该团队成员的**
  - admin：全库（不变）
  - 实现收口为 auth.py `visible_owner_ids(user, session) -> list[int] | None`
    （None=不限），leads.py 列表/统计两处调用
- 企业库不做行级隔离（拓客数据=全公司共享资产，探迹同构）

## N3 线索自动分配规则

- `assignment_rule` 表：id/name/conditions(JSON:
  {industry_key?,province?,source?} 全空=命中所有)/target_team_id FK NULL/
  target_user_ids(JSON 数组，按序)/strategy("fixed"|"round_robin")/enabled/
  _cursor INTEGER DEFAULT 0（RR 持久游标，修 EspoCRM 论坛并发旋转 bug）/priority/
  run_count/last_run_at/created_at
- 引擎 app/assign.py：
  - `match_rule(rule, ent) -> bool`（条件全命中才中；first-match 按 priority asc,id asc）
  - `pick_target(rule) -> int | None`：fixed=target_user_ids[0]；round_robin=
    target_user_ids[_cursor % len]，_cursor+=1（同 flush 落库，单进程 SQLite 串行安全）
  - `install_assign_hook()`：**before_flush** 遍历 session.new 中 owner_id IS NULL 的
    Lead → join enterprise 匹配 enabled 规则 → 命中即 owner_id+claimed_at=now+
    规则 run_count+=1/last_run_at——**覆盖全部线索创建入口**（导入/留资/自动化/手动），
    不碰 SPEC-M 文件；钩子内不得 query pending（用 session.new）且不得二次 flush
  - 无命中 → owner_id 保持 NULL（进公海，现状不变）
- 端点：GET/POST /api/assignment-rules；PUT/DELETE /api/assignment-rules/{rid}
  - POST /api/assignment-rules/dry-run：body=模拟 {industry_key,province,source} →
    返回命中规则+目标用户（**零写操作**，对齐 automation dry-run 惯例）

## N4 业绩排行榜（工作台）

- GET /api/analytics-leaderboard?month=YYYY-MM（org.py 内，admin 全员/普通用户全员可见——
  榜单是激励探迹同构）：每用户 Σ(won 线索 amount_wan 当月按 updated_at)/新增线索数/
  跟进次数（followup 当月 created_at），按成交额降序
- 前端工作台「本月业绩榜」卡：前三名徽章 + 自绘 SVG 条形（复用 charts.tsx BarList 风格，
  framed-sheet 禁渐变）

## 前端（设置页三面板 + 工作台一卡，AppShell 零改动避开 SPEC-M）

- 设置页「成员管理」：用户表（用户名/角色徽章/团队/邮箱/启用开关）+新建/编辑抽屉
  （含重置密码字段）
- 设置页「团队」：团队表+抽屉（名称/负责人下拉）+成员数反查
- 设置页「线索分配」：规则表（名称/条件摘要/策略徽章/目标/启用开关/run_count）+
  抽屉编辑 + dry-run 试运行预览（输入行业/省份/来源→显示命中规则与目标人）
- 工作台「本月业绩榜」卡（月切换下拉）

## 测试（test_spec_n.py ≥10）

用户 CRUD/重名 409/弱密码 422/禁用后登录 401/current_user 拒绝/最后 admin 保护/自禁拒；
团队 CRUD/leader 可见成员线索（三用户场景）/非 leader 不可见；
分配规则 fixed/round_robin 连续轮转（持久游标断言 _cursor 递增）/条件不命中跳过/
无规则不分配/dry-run 零写；leaderboard 聚合（won 金额/新增线索/跟进数）。
门禁：pytest 全量+build+E2E（E2E_PORT 默认 8010=Agent A 专用）+import smoke。
