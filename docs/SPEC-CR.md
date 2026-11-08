# SPEC-CR · 地图动态加载轮：视野驱动 bbox（Agent B 主线程+子 Agent 并行）

> 立项：2026-11-08 · 用户在 /map 上："不好用，继续深度优化"。全量读码
> 实锤**致命缺陷**：前端从不传 bbox——首次加载全国任意 2000 家后不再
> 刷新，切城市只是 setView 平移；**苏州视野下显示的可能是别城市的点，
> 苏州自己的点可能根本没加载**（后端无排序，截断任意）。地图没有
> "区域作战"能力，只是全国抽样截图。

## CR1 后端：geo 按意图分排序（enterprises.py，主线程）

- points 模式 `order_by(intent_score desc, id desc)`——limit 截断时
  保住最有价值的企业（全国模式 2000 上限=全国 Top 意图）
- bbox 模式照常（视野内全量）

## CR2 前端：moveend 视野驱动加载（子 Agent）

- **map moveend 事件（防抖 500ms）**：取当前视野 bbox →
  `geo?south=&west=&north=&east=&limit=2000` → 打点；zoom ≥11 时
  直接 points（城市级密度），zoom <11 全国聚合（clusters）
- 城市 chips 点击 = setView（触发 moveend 自然重载）；筛选变化 = 带
  当前 bbox 重载
- "只看有电话"/"近期有动态" 参数照带
- 统计行改为"当前视野 X 家"（bbox 模式下即真实区域数）
- 全国模式（无 bbox）保留聚合入口：zoom <11 走 clusters

## CR3 门禁

- 后端 pytest（排序断言：高意图在前被保留）
- build + E2E 8；子 Agent 自验 build/E2E

## 边界

- moveend 防抖 500ms（拖拽结束才请求）；失败静默保留旧点
- 网格聚合阈值 1500 逻辑不动
