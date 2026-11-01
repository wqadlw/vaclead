# SPEC-W · 数据质量轮：采集建档的 ICP 分类补全（Agent B 认领）

> 立项：2026-11-01 W40 · 摸底发现企业池暴涨至 1122 家（新工业词表下每日增量自动扩池，
> 信号 3423）但 **991 家"未分类"**——run_daily_incremental 建档后从未调用
> apply_icp（该函数只在导入/enrich 链路）。行业分类缺失 → 标讯中心行业筛选/
> ICP 评分/research_edu 加成/完整度语义全部失效，是企业信息的最大质量窟窿。

## W1 增量链路补分类（根治）

- jobs.py run_daily_incremental 的 ggzy 段与 ccgp 段：resolve 后调 `apply_icp(ent)`
  + `recompute_intent(s, ent.id)`（与导入链路同语义）
- 同行打标语义保持自洽：中标方（卖方）名称含设备词+制造销售词 → apply_icp 打
  is_competitor=1（采集端语义，正确）；采购人名称不含设备词自然不打标
- recompute_intent 在段尾已有全量循环 ✓（只补 apply_icp）

## W2 一次性存量补分类（991 家）

- 端点 POST /api/tasks/backfill-icp（admin，SSE 可观测，幂等：只处理 industry_key
  为空的企业；**is_competitor=1 的跳过**——采集端打标优先，防覆盖）
- 内部复用 apply_icp + recompute_intent，返回 {processed, classified, competitors}
- 立即对真库执行一轮，验证未分类清零、分类分布合理

## W3 验证与门禁

- 行业分布前后对照（未分类 991 → 目标 <200：apply_icp 只对名称/范围含行业词的
  命中，无词的保持未分类是**正确行为**——不发明数据）
- test_spec_w.py：增量建档带分类；backfill 幂等（二次跑 processed=0）；
  competitor 跳过语义；真库验证分类分布
- 全量门禁 + E2E（E2E_PORT=8017）

## 边界

- 不动 enrich-pipeline（那是工商补全编排，另一条线）
- 不做"强行分类"（无词企业保持未分类——不发明）
