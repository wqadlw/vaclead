# SPEC-AG · 拟建通道多省化：湖北项目级直采（Agent B 认领）

> 立项：2026-11-04 W51 · W49 预研（8 省探测：浙/鄂可达）+ W51 浏览器级逆向收官。
> 湖北是"备案查询"= **项目级数据**（单位名+项目名+申报时间）——广东 P2 边界
> （项目名在未暴露的详情接口）在湖北直接列表可得。"早"的阶梯再加一省。

## 逆向过程（方法论沉淀，WORKLOG W51 详录）

1. SPA 壳 `/xxgk`（aPaaS Vue，HPAAS_CUSTOM_WEB）→ Playwright 无头浏览器
   `page.on('request')` 捕获真实 XHR（IAB evaluate 桥在本环境回读不可靠，
   网络捕获脚本才是确定性路径）
2. 三个端点全部裸调可用：受理公告/拟批准公示（`xMod/HP_GS_info/customIndex`）
   + 备案查询（`xMod/filing_search/customIndex`）
3. 鉴权三头：`authtoken: null`（匿名）+ `compkey: hb` + `extoken`
4. **extoken 破译**（app.ae22f86a561dc43e00e2.js axios 拦截器）：
   `extoken = ((Date.now() % 1e9 + k*1e9) * 911 + 20170706).toString(36)`，
   k∈{0..9} 纯客户端时间签名，**服务端按时间窗校验**——伪造随机/过期 token 均
   400007，按公式现算即通（无需浏览器）
5. `screenKey` 必需（SCR_l4xunf59n2=备案查询），textSearch 关键词过滤格式有坑
   （暂不用）——采集策略=按 `-declaration_date` 拉最新页 + 本地词表过滤（同 GD）

## AG1 湖北采集器（`collectors/proposal_hb.py`）

- `compute_extoken()`：公式实现 + base36 编码（可注入时钟测试）
- `fetch_hb_filing_page(page_no, page_size, client)`：备案查询列表 →
  `{unit, project, declared_at}` 结构化记录；res!=0/网络错误返回 []
- 礼貌限速 2s（同 GD）；client 注入可测

## AG2 入库链（`jobs.run_proposal_hb_scan`）

- `resolve_enterprise(unit_name, source="proposal_hb")` 建档
- proposal 信号：title=`湖北项目备案：{project}｜{unit}`（**项目级标题**）、
  url=/xxgk 公示页、pub_date=declaration_date、evidence=官方平台完成备案句、
  base_score=40（同 GD）；title+url 查重幂等
- cron：`proposal_hb` 每日 05:10（与广东 05:00 错峰，独立水位）

## AG3 边界

- 受理公告/拟批准公示端点（HP_GS_info）本轮只逆向不接入——环评受理语义偏
  立项后段，备案查询才是"扩张前兆"主信号；接入留 P2
- 拟批准公示带 start_date/end_date（公示期），备案查询无——字段以实测为准
- 他省（浙江 unieap JSP）形态完全不同，按省逆向逐个立项（本契约不定通用
  注册表抽象——两省形态差异过大，抽象为时过早，第三省落地时再提炼）

## 验收

- 真库扫描 ≥1 页、新企业建档、信号可在标讯中心 proposal chip 下见「湖北」
- 测试：extoken 公式可解码校验 + 采集解析 + 入库幂等（注入 fixture 离线）
