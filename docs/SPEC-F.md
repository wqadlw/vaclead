# SPEC-F · 标杆化第六轮（新调研驱动）

> 立项：2026-10-26 · 第五轮调研（意图数据/线索评分/访客识别/竞对情报/技术栈指纹 5 域）
> 克隆深读：opengtm（分段评分）/ B2B-Job-Signal-Scraper（招聘信号）/ WebsiteTechMiner（技术指纹）
> 目标：**打造标杆**——补齐三个半吸收缺口 + 吸收三项新能力

## 新调研发现（对我们有直接价值的）

| 项目 | 星 | 核心思想 | vaclead 应用 |
|---|---|---|---|
| opengtm | ★56 | **ICP 分段评分**：规模(20)/行业适配/数字化成熟度(15)/痛点信号(带权重) 四段独立打分各带理由 | 升级我们单段 ICP 为分段评分器 |
| Job-Signal-Scraper | ★2 | **招聘=采购前兆**：PRIORITY_ROLES 角色表（VP Sales/CRO/GTM Engineer）→ 在招这些岗的公司=高意图 | 新信号源：国内=招聘网站搜"真空设备"岗 |
| WebsiteTechMiner | ★16 | BuiltWith/Wappalyzer 技术栈指纹 | P3（工商补全后做官网指纹） |
| 搜索面结论 | — | buyer intent/访客识别域无成熟开源（空白）| 印证我们的公示数据路线稀缺 |

## 工作项（按价值排序）

### F1 novu digest 通知聚合（防轰炸，半吸收→彻底）
- Notification 加 `digest_key` 列（默认按企业聚合）
- channels.py：dispatch 前检查静默时段（settings notify_quiet_hours "22:00-08:00"）
  → 静默期通知折入 digest 队列（不落 Notification，落 pending JSON）
- 每日 08:05 定时 digest_flush：聚合 pending → 单条"过去 X 小时 N 家企业动态"汇总通知

### F2 ICP 分段评分器（opengtm 四段模式）
- vertical.yaml icp_weights 扩为四段：size(20)/industry(40)/digital(15)/pain(25)
- 各段独立函数返回 (score, reasons)——reasons 带"段名:"前缀，情报页分组展示
- 存量数据重算（apply_icp 全量）

### F3 招聘信号源（Job-Signal 思想中国化）
- 新采集器 collectors/job_signal.py：搜索公共招聘 API（猎聘/BOSS 无公开 API——
  先落**架构位+规则引擎新动作 create_hiring_signal 的条件维度**，实际抓取待 P2 CDP）
- 垂直词表加 hiring_keywords（真空设备工程师/真空工艺/镀膜技师…）
- signal type 扩 "hiring"——命中词表时 base_score=60（强前兆）

### F4 企业地图分布（无高德 Key 的 SVG 方案）
- analytics.py 加 geo 分布聚合（省→计数）
- 工作台 charts.tsx 加 DistributionMap（中国地图简化 SVG 按省着色，纯前端）

### F5 沉淀
- WORKLOG W28 / STUDY 第五轮调研 / 记忆 / git

## 门禁
每项后 146 全绿；F2 后全量重算+前端理由分组验证；F4 截图验收。
