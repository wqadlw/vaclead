# SPEC-HUNAN：湖南政采接入（luban 平台，日期增量模式）

> 2026-11-19 · 契约已 Playwright 实弹固化（scripts/hunan_list_cap.mjs 可复跑）。
> 湖南 = luban 平台（湘皖桂吉四省同款组件），接入模式与山东不同：
> **无关键词接口——每轮拉当日最新块 → 本地 sdgp_terms 词表过滤 → 命中拉
> 详情全文 → 电话入联系人**。

## H1 采集器 `backend/app/collectors/hunan.py`（新 ~120 行）

```
HUNAN_URL = "http://www.ccgp-hunan.gov.cn"
SEARCH_HOME = "/portal/searchHome"   # POST 无鉴权
DETAIL_URL = "/portal/detail"        # GET 无鉴权
DISTRICT_CODE = "430000"
CATEGORY_CODE = "160-396330"         # 项目信息
SUB_CODES = ["160-945988"]           # 招标公告子栏（其余子码下一轮验证）
REQUEST_INTERVAL_S = 1.5
_UA 同 sdgp

fetch_home_blocks(page_size=10, client=None) -> list[dict]
  POST searchHome {code, subCodes: SUB_CODES, districtCode, pageSize,
  needTotal: False, needNewCnt: False, isProvince: True}
  → 拍平 children：{title, article_id=articleId, pub_date=publishDateString,
    district_name=districtName}；pubDate(epoch ms) 兜底转日期。

fetch_detail(article_id, parent_id="622785", client=None) -> dict | None
  GET /portal/detail?articleId={urlencode(articleId)}&parentId&timestamp={ms}
  → 响应 result.data 里正文文本字段（实测 detail 页渲染 body——响应结构
  实施时以实拍为准：剥 HTML 压空白返回 {title, body_text}；失败 None）。

filter_by_terms(blocks, terms) -> list  # title 含任一词即命中（本地过滤）
```

注意 articleId 含 `/` 和 `+`——GET 参数必须 urlencode（quote safe=""）。

## H2 采集任务 `run_hunan_scan`（jobs.py，照 run_sdgp_scan 模式）

- 词表过滤：`vertical.sdgp_terms()`（40 词共用——湖南与山东同为标题库）
- 流程：fetch_home_blocks → filter_by_terms → 命中逐条：
  resolve_enterprise(买方, source="**hunan_buyer**") + province="湖南省" 只补空
  + apply_icp（建档即分类）+ Signal(type="tender", base_score=30, url=articleId
  拼 /luban/front/detail 链接[可溯源！比山东 url=""强], evidence=title)
- 详情电话：命中企业无 notice_contact 联系人时 fetch_detail → extract_phones
  → Contact(source="hunan", role="notice_contact")——**自收敛 cap 30/轮**
- 停用短路 any_source_disabled("hunan")；礼貌间隔 1.5s；totals {notices,
  hits, buyers, signals, details, contacts_new, contacts_updated}
- 查重：Signal(url=详情链接, title, type) 三元组 + uq_signal_dedup 兜底

## H3 接线

- sources.py：注册 hunan（label_cn="湖南政采"，category="tender"，
  aliases=("hunan_buyer",)，sla_job="hunan"，sla_label="湖南政采采集"，
  sla_hours=26，search_url="http://www.ccgp-hunan.gov.cn/"）
- main.py：`_register_cron("hunan", _hunan_sweep, 6, 0)`（06:00 与 sdgp
  05:40 错峰——湖南是当日块，拉一次够）
- 前端零改动（registry 派生；signals 页 tender 徽章已有）

## H4 测试（test_spec_hunan.py）

- MockTransport：home 块正常/空/非 JSON 三路 + filter_by_terms 命中逻辑
  + articleId urlencode 断言（含 / + 的 id 必须编码）
- run_hunan_scan：monkeypatch fetch_home_blocks/fetch_detail → 建档
  （hunan_buyer+湖南省）/信号（url=详情链）/详情电话入库/幂等二跑
- registry：hunan 注册完备性
- 隔离铁律照 test_spec_collect.py

## 验收

1. 全量 pytest（483+新增）+ build；2. 真库实弹一轮 run_hunan_scan：
   报告当日块条数/词表命中/建档/信号/电话数；3. 二跑幂等零新增。
