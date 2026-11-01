# SPEC-Z · 分析下钻联动 + 企业对比（Agent B 认领）

> 立项：2026-11-01 W44 · "分析到行动"的最后一公里：图表点击即筛选（BI drill-down
> 标配交互）+ 企业横向对比。零表结构变更、零后端新端点（compare 页纯前端聚合
> 既有 detail/stat 端点）。

## Z1 图表组件下钻能力

- charts.tsx：BarList/ProvinceBars/FunnelBars 加可选 `onPick?(name)` 回调
  （不传=纯展示向后兼容）；命中行 hover 变可点样式（cursor+accent 色提示）
- AreaTrend 不加（时间趋势点击语义弱）

## Z2 工作台三处接线

- ProvinceBars onPick → router.push("/enterprises?province=X")
- BarList onPick（来源）→ "/enterprises?source=X"
- FunnelBars onPick（线索状态）→ "/leads?status=X"
- URL 同步机制已有（enterprises/leads 均支持 query 恢复）

## Z3 标讯原文外链

- 标讯中心行 title 加"查看原文"外链图标（url 非空时，target=_blank）——
  官方公示原文直达（合规溯源的最后一环）

## Z4 企业对比页（/compare）

- 选入：企业列表行/情报页「加入对比」（localStorage 暂存，≤4 家，超限提示）
- 页面：属性为行、企业为列的对比表——ICP 分/意图分/资料完整度/注册资本/成立日期/
  中标次数/累计中标额/最近中标/信号数/标签/地区/来源
- 每列头部跳情报页；数值列**最优值高亮**（accent 色）；移除单列按钮
- URL 同步 ?ids=1,2,3（可分享）

## 测试与门禁

- test_spec_z.py：/compare 依赖的既有端点无回归即可（纯前端轮）+ 后端零改动断言
- 全量门禁 + E2E（E2E_PORT=8017）

## 边界

- 不做图表内嵌筛选（点击即跳页，不做弹层联动——简化）
- 对比上限 4 家（可读性）
