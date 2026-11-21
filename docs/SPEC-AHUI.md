# SPEC-AHUI：安徽政采接入（portal/category 形态，author 直带采购人）

> 2026-11-21 · 安徽 = 政采云 portal/category 形态（与湖南 searchHome 不同：
> **标准分页 + author 字段直接给采购人单位名**——无需标题抽取，数据比山东
> 还全）。契约已实弹三发验证。

## 已验证契约（照抄）

- **列表**：`POST https://www.ccgp-anhui.gov.cn/portal/category`
  body：`{"pageNo": 1, "pageSize": 5, "categoryCode": "ZcyAnnouncement3012",
  "districtCode": ["340000"], "leaf": "0"}`
  → `{result: {data: {total: 1963, data: [{articleId(加密串), author(=采购
  人单位名), title, pathName(公告类型名), publishDate(epoch ms), content,
  districtCode}]}}}`
  **注意**：records 在 `data.data`（data.total 与 data.data 平级）
- **必须 HTTPS**（HTTP 全部 301 CWAP-waf）；**无鉴权无验证码**
- 类别：3012 采购/结果公告（1963）、3015（16,080 更大池）、3013/3014 空或
  无效（枚举待续）
- **author=发布单位**（芜湖市鸠江区教育局）——直接建档依据，无需标题抽取

## A1 采集器 `backend/app/collectors/anhui.py`（照 hunan.py 模板 ~130 行）

- `fetch_category(page_no=1, category_code="ZcyAnnouncement3012", client=None)
  -> dict`：POST /portal/category，返回 data.data 拍平
  `[{title, author, article_id, pub_date(publishDate epoch→日期),
  path_name, content}]`；resilience get_health("anhui")；HTTPS 必须
- **全量入库模式**（同 luban 决策——title 无设备词不筛）：author 直接
  resolve 建档（source="anhui_buyer"，province="安徽省" 只补空）
- Signal(type="tender", base_score=30, evidence=title, url=详情链——
  详情页路由 `/site/article?articleId={quote}&categoryCode=` 形态待实弹
  验证，验证不通则 url="" 照湖南)

## A2 任务 `run_anhui_scan`（jobs.py，照 run_luban_scan 单省版）

- 分页循环（cap 5 页/轮×20 条=100 条/轮防长跑；pageNo 递增）
- 停用短路 any_source_disabled("anhui")；细节电话/正文增强不做
  （anhui detail 契约下一轮）
- totals {notices, buyers, signals}

## A3 接线

- sources.py：anhui（"安徽政采"，tender，aliases=("anhui_buyer",)，
  sla_job="anhui"，sla_hours=26，search_url=安徽门户）
- main.py cron 06:20（错峰）；watchdog_jobs+stub 同步（test_spec_ar checked
  +1）

## A4 测试（test_spec_ahui.py）

- MockTransport：正常（author 直接建档断言）/空 data/非 JSON 三路
- run_anhui_scan：建档/信号/幂等二跑/停用短路
- registry 完备；隔离铁律

## 验收

1. 全量 pytest（509+新增）+ build；2. 真库实弹一轮：报告当日公告数/建档/
   信号；3. 二跑幂等。
