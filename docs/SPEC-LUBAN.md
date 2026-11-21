# SPEC-LUBAN：政采云系多省意向接入（沪黔桂，泛化 luban 配置驱动）

> 2026-11-21 · 湖南 /portal/searchHome 契约在沪黔双省同款验证成功
> （districtCode 可选）。本 SPEC 泛化多省配置驱动接入三省意向公开。
> **模式与湖南不同**：意向标题无设备词——**全量入库**（建档+单位名，
> 不做词表过滤；日增 4-20 条/省，增量 searchHome 天然只回当日块）。

## L1 采集器 `backend/app/collectors/luban.py`（新 ~110 行）

```
LUBAN_PROVINCES = {
    "shanghai": {"base": "https://www.zfcg.sh.gov.cn",
                 "code": "77-379845", "sub_codes": [], "district": "",
                 "province": "上海市", "source_buyer": "shanghai_buyer"},
    "guizhou": {"base": "http://www.ccgp-guizhou.gov.cn",
                "code": "ZcyAnnouncement10016", "sub_codes": [], "district": "",
                "province": "贵州省", "source_buyer": "guizhou_buyer"},
    "guangxi": {"base": "https://zfcg.gxzf.gov.cn",
                "code": "61-266648", "sub_codes": ["ZcyAnnouncement10016"],
                "district": "459900", "province": "广西壮族自治区",
                "source_buyer": "guangxi_buyer"},
}

fetch_luban_home(prov_key, page_size=10, client=None) -> list[dict]
  POST {base}/portal/searchHome {code, sub_codes, district(空则不带该键),
  pageSize, needTotal: False, needNewCnt: False, isProvince: True}
  → children 拍平 {title, article_id, pub_date=publishDateString,
    district_name}；resilience get_health(prov_key)；trust_env=False；
  失败/非 JSON → []

detail：**本 SPEC 不做**（沪黔桂 detail 契约未验证——意向核心价值=单位名+
日期，正文抽标的待 detail 契约逐省实弹后增强）
```

## L2 任务 `run_luban_scan`（jobs.py）

遍历 LUBAN_PROVINCES（单省失败 continue 不串）：
- fetch_luban_home → 逐条 **全量入库**（不过滤）：
  resolve_enterprise(单位名, source=xx_buyer) + province 只补空 +
  apply_icp 建档即分类 + Signal(type="proposal", base_score=25,
  url="", evidence=f"{title}（采购意向）") 三元组查重 + uq 兜底
- totals：`{prov: {notices, buyers, signals}, ..., total_notices,
  total_buyers, total_signals}`（每省分键便于诊断）
- 停用短路：any_source_disabled(各省主 key) 逐省判断

## L3 接线

- sources.py：+3 源（shanghai "上海政采" / guizhou "贵州政采" /
  guangxi "广西政采"，category="tender"，aliases=(xx_buyer,)，
  sla_job="luban"，sla_label="政采云意向采集"，sla_hours=26，
  search_url=各省门户）——**共用一个 cron job luban**，水位按省分别落
  （job_name=f"luban_{prov}"？——**简化：单 job_name="luban" 单水位**
  ，SLA 表 registry 派生已按 sla_job 分组，三源共一条即可）
- main.py：`_register_cron("luban", _luban_sweep, 6, 10)`（06:10 湖南后）
- watchdog_jobs 加 ("luban", run_luban_scan) + test_spec_ar stub 同步（+1 行 checked 9→10）
- 前端零改动

## L4 测试（test_spec_luban.py）

- fetch_luban_home MockTransport：正常/空 children/非 JSON 三路 +
  district 空则不带键断言 + 单省失败不影响他省（ Louisiana 假省 ERR）
- run_luban_scan：monkeypatch fetch_luban_home → 建档（xx_buyer+省确定性）
  /proposal 信号/幂等二跑/单省炸不串
- registry：三源注册完备性
- 隔离铁律照 test_spec_collect.py

## 验收

1. 全量 pytest（496+新增）+ build；2. 真库实弹一轮 run_luban_scan：
   三省当日意向建档数/信号数报告；3. 二跑幂等零新增。

## 不做

- 三省 detail 契约验证（正文抽标的——待逐省实弹，下一轮增强）
- 广西 detail 最后一公里（SPA 路由人机协同专项）
- 历史 78K/150K/83K 池回填（只进增量，历史池不回填）
