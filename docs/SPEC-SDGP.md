# SPEC-SDGP：山东政采分网直采（新采集源，昊志本省）

> 2026-11-14 · 用户定调"能跑的新采集源是项目基石"。SPA bundle 逆推 +
> 网络捕获破译，契约已全部实弹验证。语义=政采（同 ccgp），价值=①昊志
> 本省（山东）直采更快更全 ②ccgp 主源被冷却时的**冗余备份**。

## 已验证契约（实弹全通，直接照抄）

- **端点**：`POST https://www.ccgp-shandong.gov.cn:8087/api/website/site/getListByCode`
- **请求**：`{"colCode": "0301", "area": "370000", "currentPage": 1,
  "pageSize": 20, "homePage": 0, "mergeType": 0, "title": "真空"}`
  - **title=服务端关键词过滤，无验证码**（搜索路径 searchAllByCode 有
    captcha 门——不用它）；参数严格：homePage 必填 0，乱加参数会 999/None
- **栏目**：0301=采购公告（真空 total=32）、0302=结果公告（17）、
  0305=更正、0306=终止（噪音，不采）、2502=合同
- **记录字段**：`date("2026-11-05 20:22:08"), title, areaName("山东省本级"),
  areaCode, id(加密串), buyKindCode, projectType`——无中标方名称（详情
  接口 getNoticeBody 请求方式未破解，v1 不做详情）
- **实测样例**：青岛大学 6 英寸超高真空多靶磁控溅射仪 / 山东第一医科大
  学附属肿瘤医院真空冻干机 / 青岛大学真空气氛热压炉——正中客户画像
- **httpx 必须 trust_env=False**（shell 代理毒死直连的坑，项目采集器惯例）
- 限速 1.5s + resilience `get_health("sdgp")`（ccgp.py 同款）
- **鲁棒性**：502/拦截页→mark_bad（resilience 域级熔断自动冷却）

## 实施

### 1. `backend/app/collectors/sdgp.py`（新建，~120 行）

- `fetch_notices(col_code, keyword, page, page_size, client) -> dict`
  （返回 result.data；records/total/pages；失败/冷却返回空结构——照
  ggzy.py 的 get_health 短路模式）
- `search_all(col_code, keyword, max_pages, client) -> Iterator[dict]`
  （照 search_all_entities 分页保险模式，max_pages 缺省 3）
- 字段归一 `normalize_notice(raw) -> {title, date, area_name, notice_id}`

### 2. `backend/app/jobs.py::run_sdgp_scan`（照 run_proposal_js_scan 模板）

- 词表：`vertical.ggzy_name_terms()`（**项目名库用品类宽词**——BS 轮
  金子级教训：标题/项目名检索必须宽词，细词召回≈0）
- 每词每栏目（0301/0302）分页拉取（max_pages=3）
- **语义映射**：
  - 0301 采购公告 → 采购人从标题抽取（extract_enterprise_name）→
    resolve_enterprise(source=**"sdgp_buyer"**, province="山东省" 确定性，
    只补空) + Signal(type=**"tender"**, base_score=30, title 原文,
    url="", evidence_claim=title)
  - 0302 结果公告 → 同样采购人沉淀 + Signal(type=**"tender_win"**,
    base_score=60, evidence=title + "（结果公告）")——语义=该买家此项目
    已成交（配套/后续窗口），evidence 如实标注买方视角
  - 同行打标不适用（本源无中标方名称，不发明）；apply_icp 照常建档即调
- 查重：signal_exists 三元组 + uq_signal_dedup 兜底（url="" 参与）
- 礼貌间隔 1.5s；单轮 totals：`{notices, buyers, signals}` + 停用短路
  （any_source_disabled("sdgp")）+ 水位 `_run_job_with_watermark`

### 3. 接线（注册表使大部分环节零改动）

- `sources.py`：注册 `sdgp`（label_cn="山东政采"，category="tender"，
  aliases=("sdgp_buyer",)，sla_job="sdgp"，sla_label="山东政采采集"，
  sla_hours=26）
- `main.py`：`_register_cron("sdgp", _sdgp_sweep, 5, 40)`（四省 05:00-30
  错峰后）；main.py 的 sweep wrapper 照四省模式
- **前端零改动**（registry 派生：来源矩阵/企业筛选/分析归并自动纳入）
- **看门狗纪律（BR 轮教训）**：watchdog_jobs 加任务行 + test_spec_ar 的
  stub 同步加一行（漏 stub=看门狗真实网络，测试变慢）

### 4. 测试 `backend/tests/test_spec_sdgp.py`

- collector：MockTransport 正常/空/非200/999 四路 + normalize 归一
- job：monkeypatch search_all → 真库隔离断言 buyer 沉淀（province=山东省）
  + tender/tender_win 信号 + 二跑幂等（信号不增）+ 词表宽词使用
- registry：sdgp 注册完备性 + channel_variants("sdgp")=("sdgp","sdgp_buyer")
- 隔离铁律：VACLEAD_DB setdefault + DISABLE_JOBS + 模块快照清理

## 验收

1. pytest 全量通过（446+新增）；`npm run build` 不涉及（前端零改动，
   跑一次确认）
2. 实弹冒烟：真库跑一轮 run_sdgp_scan → 采购人建档（山东省）+ 信号数
   >0 + 二跑幂等 0
3. 信息中心来源矩阵出现"山东政采"行；新鲜度仪表盘出现"山东政采采集"

## 后续候选（本轮不做，记档）

- **chinabidding.cn**（全国招标聚合，含企业自主采购——武钢备件采购等
  民营语义）：真域名 .cn，/zbgg /zfcg 分类，Nuxt SSR；搜索路径 404 待
  Playwright 解码；连发会连接重置（轻度限速）。下轮候选。
- **山东 0302 详情**（getNoticeBody 请求方式未破解）→ 解锁中标方名称
  +公告联系方式（ccgp notice-contacts 同款），值得再攻。
- **ccgp 分省镜像**（苏/浙/粤…）：同语义冗余，若山东模式验证成功再复制。
- **详情正文 getNoticeBody**：POST"请求方式错误"/GET 400/form 400——
  可能需加密参数或验证码 token，待浏览器专项捕获。
