# SPEC-BRF：客户作战简报（打单前一页纸）

> 第十四次功能轮预研（第十二次差距调研"AI 研究员"域的**确定性版**——
> 不卡 GLM Key，纯本地八源数据组装）。调研依据：Attio/头部 CRM 2026 主题
> （account-based selling + 售前售后数据统一，attio.com/setup）；
> 打印页技术路径沿用 /api/proposal/print 成熟先例（HTML + @media print）。

## 1. 产品定义

销售要打一个大单/久攻不下的客户时，点一下"生成作战简报"，
**10 分钟读完的一页纸**：这家公司是干嘛的/在买什么/为什么现在打/打给谁/
谈资/下一步。数据全部来自平台已有采集（零新依赖；GLM Key 到位后可追加
"AI 摘要"段，本期预留占位注释）。

## 2. 数据契约

### 2.1 `GET /api/enterprises/{id}/brief`（登录）

大聚合 JSON（单请求拿全，供前端卡片预览与未来 AI 段复用）：

```json
{
  "enterprise": {name, industry_name, province, city, registered_capital_wan,
                  business_scope, uscc, icp_score, intent_score, is_competitor},
  "buying": {                    // 他们在买什么
    "wins": [ {title, amount_wan, pub_date, buyer} × top8 ],   // 中标史
    "top_buyers": [ {buyer, amount_total_wan, count} × top5 ], // 采购体系
    "amount_total_wan": 1234.5
  },
  "why_now": {                   // 为什么现在打
    "signals": [ {type_cn, title, pub_date, base_score} × 8 ],
    "intent_score": 68, "intent_band": "high|mid|low",
    "proposals": [ {project, pub_date} × 3 ]                   // 拟建/备案
  },
  "who": {                       // 打给谁
    "contacts": [ {name, title, phones, channel, evidence_claim} × top6
                   （可拨优先+决策链优先排序） ],
    "has_phone_count": 12
  },
  "leverage": {                  // 谈资
    "installations": [ {model, installed_at} × 5 ],
    "competitor_wins_30d": [...],          // 竞对最近拿了他什么单
    "similar_won": [ 同行业我方成交案例 ×3 ]  // "同行谁在用我们"
  },
  "next": {                      // 下一步
    "followups": [ {content, created_at} × 5 ],
    "lead_status": "following|null",
    "todo_hint": "字符串建议（确定性规则生成，非 AI）"
  }
}
```

### 2.2 `GET /api/enterprises/{id}/brief/print`（登录）

后端渲染打印 HTML（学 proposal.py:78 模式：完整 HTML + 内联 CSS +
`@media print` + `.noprint` 提示行），A4 一页。分区即 2.1 六块 +
页眉（企业名 + 生成时间 + intent 徽章）。**末段预留**
`<!-- SPEC-BRF-AI: GLM Key 到位后在此注入 AI 摘要段 -->`。

## 3. 后端实现要点

- 新文件 `app/api/brief.py`（router 挂 /api，main.py include）
- 聚合查询全部走现有表（enterprise/signal/notice_contact/contact/
  installation/followup/lead），**无新表无新列**
- 排序规则：contacts 可拨优先+决策链优先（复用 contacts 列表口径）；
  wins 按 pub_date desc
- `similar_won`：同 industry_key 且有 won lead 的企业（排除自身）
- `todo_hint` 确定性规则：无跟进→"先拨第一个可拨联系人"；
  有跟进无下一步→"补记下一步动作"；装机存在→"查耗材时钟是否到期"等
- intent_band：≥60 high / ≥30 mid / else low

## 4. 前端实现要点

- 详情页（enterprises/view）头部动作区加「生成作战简报」按钮
  （theme=outline，与"生成节能账"并排）→ `window.open(brief/print)`
  （桌面新窗口打印；移动 IAB 同页跳转已知行为，可接受）
- 不做前端预览页（打印页即最终形态，一页纸）

## 5. 测试

- 单测 test_spec_brf.py：六块数据组装（种子企业挂中标/联系人/装机/
  跟进）断言结构与排序；intent_band 三档；print 端点 200 + HTML 含
  六分区标题；404（不存在企业）
- E2E：详情页按钮存在 → 新页可打开（或 url 200）

## 6. 非目标

- 不做 AI 摘要（GLM Key 未到位，占位预留）
- 不做 PDF 服务端导出（浏览器 Ctrl+P 够用）
- 不做邮件发送简报
