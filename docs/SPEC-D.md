# SPEC-D · 企业情报页（深度子系统第一轮）

> 2026-10-25 · 触发：用户"项目像玩具"——停止功能宽度，把存量数据做成有纵深的产品面
> 目标：企业详情从"抽屉几张卡"升级为**企查查式情报页**，全部基于已验证的 342 家存量数据

## 深度清单（本轮交付）

1. **采购关系图谱**（独有数据优势，头部平台没有）
   - API：`GET /api/enterprises/{id}/graph` → 从 tender_win/tender_buy 信号抽
     `本企业 ↔ 对手方（采购人/中标方）` 边，带累计金额/次数/最近日期
   - 前端：详情页 SVG 力导向布局（无第三方库，纯 SVG），节点=企业，边宽=金额
2. **关键联系人**（contact 表，全部带 evidence_claim 溯源，不发明数据）
   - 模型：`contact(id/enterprise_id/name/role/title/source/evidence_claim/created_at)`
   - 数据来源三路：ggzy 法定代表人（legal_rep 补入库+回填任务）、ccgp 代理机构、
     采购人企业侧自动生成"采购负责人（单位）"占位
   - 回填任务：`POST /api/tasks/backfill-contacts`（用已验证 ggzy 通道按企业名补法人）
3. **关联企业**（同买家/同供应商视角）
   - API：`GET /api/enterprises/{id}/related` → 跟本企业共享同一采购人的其他供应商、
     共享同一供应商的其他采购人（竞争/互补情报）
4. **详情页改版**：抽屉 → 独立页 `/enterprises/[id]`（信息架构：头部档案条 →
   图谱 → 画像/联系人/信号时间线 → 关联企业），保留列表页抽屉为快速预览

## 门禁
test_features 扩：graph 边构建（含金额聚合）、related 互斥性、contacts 回填幂等。
全量回归 + build + 真库冒烟（淄博水环图谱应有 ≥2 条带金额边）。

## 明确不做（本轮）
电话/邮箱（合规源未配置）、LLM 摘要、风险司法数据——按 PRODUCT_GAP P1/P2 顺序。
