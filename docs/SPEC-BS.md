# SPEC-BS · 浙江备案通道轮：第四省+事项维度（Agent B 认领）

> 立项：2026-11-07 W59 · 省份复制第二站浙江。侦察实证（本会话直连实测）：
> **tzxm.zjzwfw.gov.cn 裸调可达**，`POST /publicannouncement.do?method=itemList`
> 返回 JSON `{itemList, counts}`，参数仅 `pageFlag=1&pageNo&area_code=&
> area_flag=1&deal_code=&item_name=关键词`（验证码逻辑在站点 JS 中已注释，
> 实测未启用）。固定 10 行/页；「真空」近一年 counts=255。
> **召回质量**：年产2000套低温真空泵生产线（泵制造=OEM/同行双向情报）、
> 半导体真空工艺集成装备研发量产、真空自耗熔炼产线。
> **独有维度**：每行带**事项名**（备案/核准/规划许可/水保）+办理状态+
> 办理部门——不只备案，是项目全生命周期的审批节点流（推进中=强前兆）。

## BS1 collectors/proposal_zj.py

- `fetch_zj_items(keyword, page_no=1, client=None)`：POST itemList，解析
  JSON 行 {deal_code, apply_project_name, ITEM_NAME, DEAL_NAME,
  DEAL_TIME, DEPT_NAME} → 规范化 dict
- **文号前缀剥离**：`富政工出【2025】16号` 类出让文号正则剥离后再抽单位
- 噪音过滤复用 proposal_hb.is_residential_noise；DEAL_TIME 剥 ".0"
- 固定 UA+Referer+1.5s 限速；client 注入可测

## BS2 jobs.run_proposal_zj_scan(max_pages=2)

- 词表 device_strong_terms()[:12]（与苏/ ccgp 同口径；10 行/页 ×2 页）
- 单位抽取 extract_enterprise_name（剥文号后）；抽不到计数跳过
- 建档 resolve(source="proposal_zj") + province 空则填**浙江省** +
  apply_icp；信号 evidence 带「项目名」+事项名+办理状态+办理部门
- cron 05:30（粤 05:00/鄂 05:10/苏 05:20 后错峰）；看门狗纳入

## BS3 前端 /proposals 四通道徽章

- 广东(blue)/湖北(green)/江苏(cyan)/浙江(amber)；channel 映射源
  proposal_zj

## BS4 测试 tests/test_spec_bs.py

- 文号前缀剥离 + JSON 行解析 + 噪音复用
- 扫描入库：单位抽取/省份补齐/幂等/evidence 事项名；center 徽章 zj

## 边界

- 全事项入库（备案=前兆，规划许可/水保=推进中信号），evidence 如实带
  事项名供销售分辨；不做事项白名单（词表已过滤设备相关性）
- 每省通道请求量 12-24 请求/日，四省合计 <100 请求/日，克制

## 落地记录（2026-11-07 W59）

- 采集器 proposal_zj.py：JSON 行解析 + 文号前缀剥离（富政工出【2025】16号）
  + 噪音复用 + DEAL_TIME 剥 ".0"。
- **词表纠偏（首扫实测逼出）**：ggzy 主体搜索用的泵品类细词（真空泵/
  罗茨泵/分子泵…）在浙江"项目名"检索召回≈0（首扫仅 1 词命中 20 条）；
  换 ggzy 宽词前 12（项目名口径，与 ggzy 每日增量同源）后 10 词命中。
  **教训：服务端关键词检索的词表必须匹配检索字段的文体**——主体名库
  用品类细词，项目名库用品类宽词。
- 真库首扫（宽词版）：10 词命中/89 记录/33 新企业/41 新信号/45 无单位
  如实跳过。
- 标题结构：事项名全量在前+项目名截断——同项目不同审批节点各自成信号
  （项目推进被分段跟踪）。
- 看门狗+stub 同步（规矩第二次执行，一次到位）；四通道徽章（浙=amber）。
- 门禁：pytest 321 + build + E2E 7 全绿。
