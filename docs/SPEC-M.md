# SPEC-M · 标讯中心 + 筛选扩容 12 维（拓客环补强，PRODUCT_GAP P0-1 + 门面页）

> 立项：2026-10-31 W36 · Agent B 认领。依据 PRODUCT_GAP §三 P0-1（数据已在库纯产品化）
> + 拓客环缺"标讯查询"门面入口（探迹招投标页为对标形态）。
> 文件边界：不碰 Agent A 的 J4 文件（models.py 追加段/outreach/tasks.py worker/main.py）；
> enterprises.py（后端）当前干净可动；AppShell.tsx 仅追加导航一行。

## M1 标讯中心（/signals 新页 + GET /api/signals 新端点）

### 后端 app/api/signals.py（新文件）
- `GET /api/signals`：全局信号流倒序分页
  - 筛选：type（tender_win|tender_buy|tender）、industry_key（join enterprise）、
    province、q（标题 LIKE）、days（近 N 天，created_at）
  - 返回：items[{id,type,title,ent_name,ent_id,ent_industry,province,amount,
    pub_date,created_at,evidence_claim,base_score,intent_score,is_competitor}] + total
  - facet：type/province 计数（SQL group by，一次请求带回；Meili 投影是
    enterprise 事实源信号量小（数百级），SQLite group by 足够）
- 复用 Signal 模型现有列；金额从 raw_excerpt 提取已有 _AMOUNT_RE（jobs）→ signals.py
  自带同款正则（不 import jobs 防循环）

### 前端 app/signals/page.tsx（新页）
- 列表行：类型徽章（中标=cyan/公开采购=green/政采公告=blue，沿用既有配色）+ 标题 +
  企业名（点开情报页）+ 金额 + 日期 + 省份 + 证据句灰字
- 筛选条：类型/省份下拉 + 关键词搜索 + 近 7/30/90 天切换 + 行业下拉（复用 INDUSTRY_OPTIONS）
- 操作：进情报页 / 一键订阅该企业（POST /api/subscriptions 复用）/ 转线索按钮
  （非同行时，复用 enterprises/{id}/lead）
- AppShell NAV 加「标讯中心」（Radar→Megaphone 图标区分），PAGE_TITLES 登记
- g+6 快捷键顺位（KeyboardShortcuts 帮助列表若列导航则同步）

## M2 筛选扩容 10→12 维

### M2a 成立年限（数据补全 + 筛选）
- Enterprise 加列 `established_at TEXT`（幂等 ALTER + OPERATIONS §8 登记）
- 回填链路：resolve_enterprise 签名加 `est_date: str | None = None`（向后兼容），
  ggzy 采集调用处传 rec["est_date"]（已抽取未落库的数据首次真正入库）；
  CSV 导入表头同义词加「成立日期」映射
- 真库回填：ggzy 主体搜索池重跑一遍 est_date 回填（手动任务脚本，标注增量幂等）
- 筛选参数 `established_within_years`（≤N 年=新锐企业）接入 enterprises 列表
  （SQLite 路径 + Meili filter 同步）

### M2b 中标力筛选
- `bid_min`（EnterpriseStat.bid_count >= N）与 `bid_amount_min`（累计中标金额 ≥）
  ——join stat 半连接，SQLite 路径；Meili 路径先回退集合过滤（量小）

## 测试

- test_spec_m.py：signals 端点筛选/facet 正确性；established_at 回填与
  established_within_years/bid_min/bid_amount_min 三参数命中与组合
- 全量门禁 + E2E（E2E_PORT=8017）

## 边界

- 不做信号详情页（列表行+证据句已够用，点击直达企业情报页）
- 不做收藏/导出（P2 报表轮统一做）
