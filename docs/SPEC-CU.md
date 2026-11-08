# SPEC-CU · 各部件移动端优化轮（双子 Agent 并行分片）

> 立项：2026-11-08 · 用户："各个部件的移动端优化，参考类似移动端
> app"。壳已就绪（CT 轮底部导航+顶栏精简），本轮下沉到**页面内部**。

## 移动端设计原则（参考高德/企查查/Linear mobile）

1. **筛选/工具行**：<md 改横向滚动（`overflow-x-auto flex-nowrap`，不折行不堆叠），隐藏滚动条
2. **KPI/网格卡**：默认 grid-cols-2 起步，md 扩列
3. **统计条/汇总条**：小字紧凑或隐藏次要项
4. **表格**：横向滚动兜底（已有则不动）
5. **验收硬标准**：390px viewport 下**无横向溢出**（body scrollWidth <= 390+容差）

## 分片（两个子 Agent 并行，文件级零冲突）

### 片 A（子 Agent 1）：工作台 / 联系人中心 / 客户地图
- frontend/app/page.tsx：晨检行 grid（grid-cols-1 sm:grid-cols-3 已有？核查）、今日行动/今日推荐卡内布局、KPI 行 grid-cols-2 md:grid-cols-3 xl:grid-cols-5 核查、统计条溢出
- frontend/app/contacts/page.tsx：KPI 四卡（已有 2 列 ✓ 核查）、筛选行（Select 三横排→滚动行）、名单卡
- frontend/app/map/page.tsx：工具行（城市 chips 横排已 ✓ 核查溢出）、统计行

### 片 B（子 Agent 2）：企业列表 / 标讯中心 / 拟建雷达 / 报表中心
- frontend/app/enterprises/page.tsx：工具行收敛已有、快捷筛选行溢出、摘要条、表格（横向滚动兜底核查）
- frontend/app/signals/page.tsx：chips 行+筛选条溢出、信号卡
- frontend/app/proposals/page.tsx：工具行+chips（已有部分）、列表
- frontend/app/reports/page.tsx：KPI 行 grid-cols-2 md:grid-cols-3 xl:grid-cols-6 核查

## 通用手法（两个子 Agent 共用）

- 溢出检测脚本（playwright 390×844 打开页面 evaluate `document.body.scrollWidth`）自查，>400 逐元素排查
- 改动模式：外层容器 `overflow-x-auto` + 内层 `flex-nowrap gap-2 px-4`；或控件 `!w-28` 收窄
- 文案不删（移动端可滚动看到全部），只改布局

## 验收（两个子 Agent 相同）

1. build 成功
2. E2E 12/12 全绿（对方分片不回归）
3. 390px viewport 五页截图 + scrollWidth 数字报告（<=400 达标）
4. git 不提交

## 主线程

- 整合双分片 → build + E2E 全量 + 390px 全页扫描复核 → WORKLOG/提交/推送
