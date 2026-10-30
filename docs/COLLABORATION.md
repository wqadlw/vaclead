# 多 Agent 协作协议（vaclead）

> 背景：本项目由多个 Agent 会话共同开发（用户正式委托）。所有 Agent 遵守同一套
> 正规流程与文件纪律。本文件是协作的单一事实源，开工前必读。

## 1. 单一事实源

- **契约**：docs/SPEC-*.md（每轮一个 SPEC，DDL 级表结构/接口/页面/DoD）
- **日志**：docs/WORKLOG.md（W 编号递增，谁做什么、坑、门禁结果，完成后补齐）
- **运维**：docs/OPERATIONS.md（schema 变更必须登记 §8；调度任务 §3）
- **参照库**：refs/STUDY.md（外部项目吸收记录）
- **测试**：backend/tests/test_spec_*.py（每轮带回归测试）

## 2. 任务认领（防撞车）

- 开工前：在 WORKLOG 写**认领条目**（"WXX · 认领人 X · 做 SPEC-J §JN · 动哪些文件"）
- 进行中：在 SPEC 对应段落写实施细化契约（参照 SPEC-J §J4 的 J4 实施细化契约格式）
- 冲突裁决：后认领者让路；同一 SPEC 段落只允许一个认领者
- 当前分工（2026-10-31）：
  - **J4 邮件触达闭环** → 认领 Agent A（SPEC-J.md 已有其实施细化契约，勿动其文件：
    app/outreach/ 全部、api/outreach.py、models.py 追加段、jobs.py worker 段）
  - **J3 收尾/漏斗接线 + 日志轮转 + 协作协议** → Agent B（analytics.py、page.tsx、
    app/logrotate.py、本文件）

## 3. 文件纪律

- **共享文件**（models.py / main.py / jobs.py / WORKLOG.md）：只做**追加式**修改，
  不重排他人段落；每次 Edit 前重新 Read 现场状态（他人可能已改）
- **新功能文件**：优先开新文件（api/<模块>.py、components/<组件>.tsx），把对共享文件的
  改动压缩到最少（路由 include 一行、cron 注册一段）
- **不覆盖他人未提交改动**：git status 看到陌生的未提交 diff 时，读 diff 判断归属，
  归属他人的 WIP 不动、不提交（除非其内容与本轮门禁直接冲突且已在 WORKLOG 声明）

## 4. Git 纪律

- 小步提交（一轮一提交），提交信息沿用现有风格（SPEC 轮次 + 内容清单 + 测试计数）
- push 前：`git fetch origin && git pull --rebase origin main`（远端可能有同事的提交）
- **绝不 force push**、绝不 reset 他人提交
- push 后核对 `git log origin/main -1` 是自己的 HEAD

## 5. 门禁（提交前全绿，无一例外）

1. `python -m pytest -q`（backend，全绿）
2. `npx next build`（frontend，out/index.html 生成）
3. `npx playwright test`（NO_PROXY=127.0.0.1,localhost，6 用例全绿）
4. 真库操作后 PRAGMA integrity_check

**互斥约定（实测教训）**：pytest/build/E2E 会争用 node_modules、.next、:8010 端口与
CPU——两位 Agent **不要同时跑门禁**。跑门禁前若 WORKLOG 显示同事可能也在跑，先等
或先做纯编码工作；E2E 出现"test() called here"/超时类瞬时错误，先排除并发再查代码
（干净复跑即过 = 并发竞争，不记为回归）。

## 6. 已知环境坑（新会话必读，详见 WORKLOG 各轮坑段）

- tests/conftest.py 已全局设 VACLEAD_DISABLE_JOBS=1；**新测试模块必须自带
  VACLEAD_DB setdefault**（漏了打到真库）
- E2E 前清代理（Clash 劫持 127.0.0.1 探针）+ 杀 :8010/:3000 残留实例
- autoflush=False：add 后同 session 查询不可见（本项目已三次踩）——批内查重用
  seen 集合，断言前 s.flush()
- SQLite 单写者：**写事务绝不跨 HTTP 调用**（三段式模式，见 jobs.py run_daily_incremental）
- sync 依赖里 set contextvar 不回传请求上下文（认证依赖必须 async）
- real DB 加列走幂等 ALTER（PRAGMA table_info 检查）+ OPERATIONS §8 登记

## 7. 沟通

- 每轮结束在 WORKLOG 写清"门禁结果 + 遗留项 + 下一步建议"
- 破坏性操作（真库迁移、大规模删除、force push）禁止单方面执行
