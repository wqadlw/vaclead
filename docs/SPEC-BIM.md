# SPEC-BIM：竞对抢单地图 + 品牌装机预备（P1 兑现轮）

> 2026-11-15 · 摸底实锤：现有 5,677 条信号语料品牌词命中仅 3~11 条
> （raw_excerpt 全库 ≤186 字截断摘要，全文从未入库；型号正则 1,006 次命中
> 实测全是项目编号）——品牌×型号抽取作为主链路**不成立**。改道三层：
> **主链="竞对→买家"抢单地图**（160 条带采购人的 tender_win + 94 条
> tender_buy/81 买家，_BUYER_RE 正则与证据格式现成）+ **brand_install 品牌装机
> 预备**（词表入配置+幂等抽取任务+增量钩子，语料升级后自动增产+手工导入
> 通道）+ **跨品牌维修群激活**（enterprise_count 注入 brand_install 去重企业）。
> 品牌词表来源：网上调研（Leybold/Busch/Atlas Copco/Edwards/ULVAC/Ebara/
> Kashiyama/Agilent/Becker/KNF/普发 + 国产中科仪/北方华创/汉钟 IPM·PMF·PD/
> 鲍斯/华成/肯富来；国产旋片 2X/XD 系列）。

## B1 品牌词表（config/industrial.yaml + vertical.py 访问器）

`brand_terms`（yaml 新键，device_terms 后）：中英对照数组——
莱宝/Leybold、普旭/Busch、阿特拉斯/Atlas Copco、爱德华/Edwards、爱发科/
ULVAC、荏原/Ebara、Kashiyama、普发/Pfeiffer、安捷伦/Agilent、Becker、KNF、
中科仪/中科科仪、北方华创、汉钟/汉钟精机、鲍斯/Baosi、华成、肯富来、
开山、住友。vertical.py 加 `brand_terms()` 访问器（照 device_strong_terms）。

## B2 BrandInstall 模型（models.py，create_all 直建）

```
brand_install: id PK / enterprise_id FK(enterprise.id, CASCADE) +
  Index ix_bi_enterprise / brand TEXT / model TEXT default "" /
  signal_id INTEGER（可空，溯源）/ evidence TEXT（截 200）/
  first_seen TEXT / last_seen TEXT / tenant_id INT default 1 /
  created_at default utcnow / UniqueConstraint(enterprise_id, brand, model)
```
不复用 InstalledUnit（那是昊青自有台账：product_id+耗材时钟；竞对装机
证据是公告信号）。聚合 join enterprise 过滤 deleted_at（软删兼容惯例）。

## B3 抽取器 `backend/app/brand_extract.py`（新 ~90 行）

- `extract_brand_for(session, enterprise_id) -> int`：扫该企业全部 signal 的
  title+raw_excerpt+evidence_claim，brand_terms 精确子串命中→品牌锚定后
  邻近 20 字内抽型号（`[A-Z]{1,4}[- ]?\d{2,4}[A-Z]?`，防项目编号：型号 token
  必须含字母且紧邻品牌词）；upsert brand_install（命中即
  last_seen=max、first_seen=min、signal_id/evidence 取最新）；返回新增数。
- `extract_all(session) -> dict`：全量 signal 逐条（5,677 行秒级）+ 按
  enterprise_id 去重调 extract_brand_for；totals {signals, enterprises,
  brands_found, installs}。
- 竞对方→买家方抽取（主链数据）：复用 competitors.py 的 `_BUYER_RE` ——
  tender_win evidence_claim 抽采购人名 → resolve（source 不变，只关联）
  → 若中标方 is_competitor=1 且买家非竞对 → **brand_install 也记一条
  brand=竞对公司名（model=""）**——"北方华创→中色东方"这类抢单关系直接
  进装机地图（品牌=竞对名，证据=原信号）。

## B4 幂等任务 + 增量钩子

- `POST /tasks/brand-extract`（照 backfill-icp 模板：uuid+worker+spawn+
  checkpoint+SSE/取消免费）；totals 走 SPEC-MULTI new/failures 契约。
- 增量：ggzy-harvest affected 循环尾 + daily_incremental 尾各加
  `extract_brand_for(s, eid)`；`_register_cron("brand_extract", 4, 20)` 每日
  兜底全量（幂等无害）。**看门狗纪律**：brand_extract 非采集任务不进
  watchdog_jobs。

## B5 产品面四出

1. **竞对雷达第四区块** `GET /api/competitors/install-map`：
   `{brands: [{brand, buyers: N, amount_wan: Σ, latest: date}], rows:
   [{competitor, buyer, brand, model, amount_wan, date, evidence, signal_id}]×50}`
   ——复用 _comp_signals cap 1500 口径；前端 competitors 页新 section
   （品牌榜 BarList + 明细表，空态友好）。
2. **跨品牌维修群激活**：segments.py `list_segments` 对
   code=="cross_brand_service" 特判——enterprise_count/dialable_count 改
   brand_install 去重企业数（join non_buyer_conds 同口径）；_segment_ops_stats
   id 集来源同款分支。
3. **企业详情页**：enterprise_detail 返回 `install_brands: [{brand, model,
   evidence, date, url}]`（brand_install 按 enterprise_id 查）——档案页签
   "在用品牌/被竞对中标"行，前端 view 页渲染。
4. **话术卡上下文**：pick_script_cards 的 competitor_replace 命中时
   _card_dict 附 `context: {comp_name, evidence}`（最近一条竞对中标证据）
   ——前端 PrepScriptCards 渲染"话术背景"一行。

## B6 测试（test_spec_bim.py）

- 词表访问器非空+中英都命中；extract：Mock 信号（北方华创+型号邻近/
  纯品牌无型号/项目编号不误抽 三路）+ upsert 幂等（二跑零新增+last_seen
  更新）；竞对→买家链（tender_win evidence 含采购人→brand=竞对名落库）
- install-map 端点形状+非客户排除；跨品牌群 count 注入（造 brand_install
  沙粒断言 enterprise_count>0）；详情页 install_brands；话术卡 context
- 隔离铁律照旧

## 验收

1. 全量 pytest（471+新增）+ build；2. 真库实弹跑 /tasks/brand-extract：
   预期 brands_found ≥2（北方华创 3 条+三井光中 1 条）、抢单关系 ≥3 条、
   跨品牌群 enterprise_count 从 0 变 >0；3. 竞对雷达第四区块渲染真数据。

## 不做

- 全文抓取（公告详情页入全文是语料升级的独立轮，触发条件=ccgp 解封）
- 型号 NER/LLM 抽取（v1 无 LLM 原则；正则+品牌锚定够用）
- brand_install 的手工导入 UI（units-paste 模式复制成本 ~45 行，下轮按需）
