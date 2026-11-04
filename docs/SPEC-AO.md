# SPEC-AO · Agent-Native 接口 + 推荐漏斗收口（Agent B 认领）

> 立项：2026-11-05 W55 · PRODUCT_GAP 第六轮：企查查 2026.3 已转向 MCP 智能体
> 数据平台——把企业数据变成任意 AI 助手可调用的工具是行业新方向。vaclead 的
> 26.6k 池/信号流/推荐引擎是同类资产。**复用 J1 APIKey 通道，零新依赖**。

## AO1 MCP 风格工具端点（Agent 数据基座）

- `GET /api/mcp/tools`：工具清单（名称/描述/参数 schema）——Agent 平台发现用
- `POST /api/mcp/call` {tool, args}：统一调用入口（**X-Api-Key 认证强制**，
  read scope 即可——只读工具集）
- 四个只读工具（复用现有引擎，薄封装）：
  - `search_enterprises(q, industry_key, limit)` → 企业列表
  - `get_enterprise(eid)` → 详情+信号+联系人（脱敏：电话仅 rw scope 返回）
  - `list_signals(type, days, limit)` → 最新信号流
  - `get_recommendations(limit)` → 今日推荐（含理由）
- 响应统一 {ok, data} / {ok: false, error}——Agent 平台好解析

## AO2 推荐漏斗收口：一键转线索

- `POST /api/recommendations/{ent_id}/convert`：
  已有 following/intent 线索 → 复用返回 lead_id；无 → 创建 following 线索
  （owner=当前用户）+ 自动建跟进待办「推荐转化：48h 内首联」
- 推荐引擎排除已转线索？不排除——推荐卡状态变化（"跟进中"徽标），
  理由"尚未分配跟进"消失（引擎自动）
- 前端推荐卡加「转线索」按钮（已转显示"跟进中"）+ 端点返回后即时刷新

## 边界

- MCP 走 HTTP 端点而非 stdio SDK（零依赖；企查查同为 HTTP 接入形态）
- 工具集只读（写操作留给人工/UI——Agent 写入的审计与限流 P2）
- convert 每企业幂等（复用已有线索，不重复创建）
