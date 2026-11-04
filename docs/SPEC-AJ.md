# SPEC-AJ · POI 行业纵深 + 客户地图视图（Agent B 认领）

> 立项：2026-11-05 W53 · 用户 Key 已激活、3458 家 POI 企业带 GCJ-02 坐标入库——
> 地图视图（探迹付费功能）的数据条件首次齐备。双主题：数据纵深 + 可视化。

## 调研吸收

- [Leaflet 高德瓦片纠偏](https://www.cnblogs.com/s0611163/p/15606460.html)：
  GCJ-02 偏移问题只在混用 WGS84 数据时出现——**我们的坐标和数据底图同源
  （都来自高德），天然对齐零纠偏**（最干净的场景）
- 瓦片模式 `webrd0{s}.is.autonavi.com/appmaptile?...style=8`（subdomains 1-4）
- CRM 地图标配：打点着色分维度（电话有无）、点击透出联系卡、聚合防炸

## AJ1 POI 行业纵深

- 5 新行业 × 10 城（environmental/machining/hvac_building/lab_instrument/
  medical_health），poi_batch.py 支持 industry_keys 参数化
- 预期 +2500~3500 家（词表命中密度略低于真空/半导体）

## AJ2 客户地图（/map）

- **后端** `GET /api/enterprises/geo`：bbox/with_phone/industry/limit(≤2000
  防炸) 筛选 → [{id,name,lat,lng,city,has_phone,industry_name,intent_score}]
- **前端** /map 页（Leaflet + 高德栅格瓦片）：
  - circleMarker：有电话=青蓝实心、无=灰描边（一眼看"打得通的地盘"）
  - popup：企业名 + 电话 chips + 「查看详情」直达情报页
  - 筛选条：有电话 only / 行业 Select / 城市聚焦
- AppShell 导航「客户地图」（拓客组）

## 依赖决策

- 新增 `leaflet`（+types）：39KB gzip，地图领域事实标准；静态导出
  ssr:false 动态导入兼容。自绘 SVG 中国轮廓方案否决（维护 GeoJSON 成本
  > 引包，且无缩放交互）

## 边界

- 高德瓦片为公开栅格服务（与 Key 无关）；POI 坐标 GCJ-02 与底图同源零纠偏
- 2000 点上限分城聚合加载（首页全国概览按城市聚合，下钻单城全量打点）
