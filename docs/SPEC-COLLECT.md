# SPEC-COLLECT：信息采集中心（情报中心组的采集门户）

> 2026-11-15（用户在情报晨报页："能不能专门出一个信息采集的功能"）。
> 诊断：采集入口全藏在"工具→拓客任务"，情报视角没有"去采更多"的动线；
> 任务只有单查（status/results/events）无历史列表——采过什么/采到多少/
> 成功失败无处可查。本 SPEC 把采集做成**情报中心一级功能**。

## C1 后端：GET /api/tasks/recent（新端点，tasks.py）

checkpoint 表倒序 cap 20：
`{tasks: [{task_id, kind, status, progress, updated_at(截19), summary}]}`
- summary 从 state_json 提取人话摘要：DONE_LABELS 同源映射（entities→主体/
  new_entities→新面孔/bids→中标/signals→信号/buyers→买家/announcements→
  公告/notices→公告/pushed…优先取数值>0 的前三键）；skipped_disabled→
  "来源已停用跳过"；error/failed→error 字段截 80
- 权限全员；只读零风险

## C2 前端：/collect 信息采集页（新页，情报中心组"信息采集"）

三段：
1. **源状态**（GET /api/sources 十二源）：卡片 grid——每源 label/类别 tag/
   企业数/最近入库/启用态；停用灰显。点源卡跳设置页数据源 tab（admin 管理）。
2. **快捷采集**：三张主动作卡（全国公共资源·中标数据→/tasks 通道预选、
   政采网公告→/tasks、山东政采分网→/tasks）+ 次级动作行（工商补数清单/
   POI 采集/粘贴导入/品牌抽取 brand-extract→POST 后 Toast 返回 task_id）。
   ——全部跳转/触发到既有端点，不重复造表单。
3. **采集历史**（C1 recent）：表（时间/类型中文/状态 tag/进度/结果摘要/
   详情链 → /tasks 页看 SSE）；空态"还没有采集记录——从上面发起第一次"。
   类型中文映射：poi/ggzy/ccgp/sdgp/enrich/tender/dup_scan/outreach/
   brand_extract。

## C3 导航

- AppShell 情报中心组加"信息采集"（Radar 图标已组内可复用 Download 或
  Satellite 图标）+ 命令面板加入口；拓客任务页保留（工具属性不撤）
- PAGE_TITLES 加 /collect

## C4 测试

- recent 端点：造 checkpoint 沙（done/running/error/skipped_disabled 各一）
  断言形状+summary 人话；空库空数组；401
- 前端 build；页面截图验收

## 不做

- 各源参数表单（复用拓客任务页现有表单，不重复）
- 定时任务管理 UI（cron 全自动，水位看数据质量页）
