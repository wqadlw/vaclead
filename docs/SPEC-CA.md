# SPEC-CA · 查重治理轮：安全自动化+批量复核通道（Agent B 认领）

> 立项：2026-11-07 W59 · 复核队列 5,400 对积压。构成分析（真库实测）：
> 90+ 高分带 51 对 / 80-89 带 5,225；POI-POI 对 5,190（同园区假阳性
> 大头）。**自动化边界用数据划死**：
> - ❌ 去括号同名自动合并被否决——安世(中国)/友嘉(绍兴)是独立法人，
>   阿玛尼两家店是不同门店，去括号同名≠同一主体
> - ✅ 唯一确定性安全规则：**两侧括号内容都以"店"结尾的 POI 门店对
>   → 自动拒绝**（分店建档是设计行为，不同店=不同到访点，合并=错）

## CA1 POST /tasks/review-auto-dismiss（admin，确定性规则执行器）

- 规则 poi_store：candidate/target 名称括号内容均以"店"结尾 →
  status='rejected'（reason 记 auto_poi_store），幂等只处理 pending
- 返回 {dismissed, scanned}；真库执行一次，队列瘦身

## CA2 复核对照表：导出+批量回写

- GET /review-queue/export：CSV（score/candidate/候选来源/target/
  目标来源/rid）——5,400 行线下 Excel 快过（粘贴导入成熟模式复用）
- POST /review-queue/bulk {items: [{id, action: merge|reject}]}：
  循环复用既有单条 merge/reject 语义（含信号迁移/审计），返回逐条
  成败——Excel 标完贴回来即完成消化

## CA3 复核页批量操作（settings 数据复核页签）

- 行选择 + 批量拒绝（安全动作）；合并保持逐条（危险动作单人确认）
- 「导出对照表」按钮

## CA4 测试 tests/test_spec_ca.py

- poi_store 规则（门店对 dismiss/非门店对不动/幂等）
- bulk 批量 reject+merge 逐条成败
- export CSV 行数与列

## 边界

- 自动规则只此一条，新规则须逐条安全论证（MDM 宁多复核不误并）
- merge 批量仍走单条语义循环（迁移逻辑不重写）

## 落地记录（2026-11-07 W59）

- 真库执行：5,400 扫描 → **129 对门店对确定性拒绝**（pending 5,271）；
  幂等重跑 0。
- CA2/CA3 全落地：export CSV（aliased 双 join——同表二次 join 必须
  aliased 否则 ambiguous column）、bulk 循环复用单条语义逐条成败、
  复核页行选择+批量拒绝+导出按钮。
- **方法论沉淀：自动化边界用真实数据划**——去括号同名自动合并被
  229 对样本否决（独立法人/不同门店反例），唯一安全规则过论证才上。
  剩余 5,271 对的消化路径=导出对照表线下过 → bulk 贴回。
- 门禁：pytest 332 + build + E2E 7 全绿。
