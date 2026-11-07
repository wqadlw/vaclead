# SPEC-BR · 江苏备案通道轮：昊志腹地第三省（Agent B 认领）

> 立项：2026-11-07 W59 · 缺口 #1 剩余项"更多省份通道复制"第一站选江苏
> ——苏州/无锡/常州=昊青设备的核心射程。侦察实证（本会话直连实测）：
> **tzxm.fzggw.jiangsu.gov.cn 裸调可达**，`POST /portalopenPublicInformation.do?
> method=querybeianExamineAll`，参数仅 `apply_project_name`（项目代码/
> 名称/备案证号）+ `pageNo/pageSize`，**服务端关键词过滤**（优于粤鄂
> "全量再筛"）+ 日期倒序（首页=昨天）。关键词「真空」实测召回即金矿：
> 干式真空泵扩产/真空镀膜机研发中心/真空钛金加工线——全是昊青下游。
> 全库 40,429 页≈80 万项目，日扫只取第 1 页即可。

## BR1 collectors/proposal_js.py

- `fetch_js_beian(keyword, page_no=1, page_size=100, client=None)`：
  POST 列表页，行解析 = [项目代码+项目名, 审批部门, 备案证号, 日期]；
  project_code 前缀正则剥离；日期 YYYY/MM/DD→ISO；固定 UA+Referer+
  1.5s 页间限速；client 注入可测
- 噪音过滤**复用** proposal_hb.is_residential_noise（光伏使用侧滤、
  制造侧保留——江苏首屏大量屋顶分布式光伏，同款噪音形态）
- 无合规 UA 不请求；只采法定公示页，不碰登录后功能（合规红线）

## BR2 jobs.run_proposal_js_scan(max_pages=1)

- 词表：device_strong_terms()[:12]（与 ccgp 增量同口径，12 请求/日）
- 单位抽取：extract_enterprise_name(项目名)——项目名=企业名开头的
  江苏命名习惯；抽不到的如实计数跳过（不发明归属）
- 建档：resolve(source="proposal_js") + **province 空则填江苏省**
  （BP0 确定性映射）+ 建档即 apply_icp（W40 模式）
- 信号：type=proposal，evidence 带「项目名」+备案证号+审批部门；
  (enterprise,url,title) 去重幂等
- cron 05:20（粤 05:00/鄂 05:10 后错峰）

## BR3 前端 /proposals

- channel 徽章三态：广东备案(blue)/湖北备案(green)/江苏备案(cyan)

## BR4 测试 tests/test_spec_br.py

- mock 注入：可抽取单位+光伏屋顶噪音+抽不到单位三类 → 断言入库/
  噪音跳过/无单位跳过/省份补齐/幂等重扫/center channel=js

## 边界

- 只取第 1 页（日期倒序已保证新鲜度）；扩页留给补扫
- 项目名=企业名开头的抽取习惯覆盖 majority；机构类项目（XX园区管委会
  自建）抽不到即跳过计数，不落待归属（与 ccgp 买家链路同语义）

## 落地记录（2026-11-07 W59）

- 采集器 `collectors/proposal_js.py`：POST .do + 行解析（项目代码剥离/
  日期归一/短行跳过）+ 噪音复用 proposal_hb.is_residential_noise +
  1.5s 限速 + client 注入可测。
- `run_proposal_js_scan`：12 强词 × 1 页（12 请求/日）+ 单位抽取自项目名
  （抽不到计数跳过）+ 建档即补省江苏省 + apply_icp + 幂等。
- **真库首扫战果**：12 词全通、90 记录、15 新企业、20 新信号、
  70 无单位跳过；关键词召回含"干式真空泵扩产/真空镀膜机研发中心"
  ——昊青下游直读。
- 前端三通道徽章（粤 blue/鄂 green/苏 cyan）；看门狗纳入 proposal_js。
- **测试教训（又一次）**：看门狗新增任务必须同步打桩——test_spec_ar
  漏 stub run_proposal_js_scan，缺水位触发真实网络（58s→打桩后 2.4s）。
  规矩固化：watchdog_jobs 加一行，test_spec_ar 的 stub 就加一行。
- 门禁：pytest 319 + build + E2E 7 全绿。
