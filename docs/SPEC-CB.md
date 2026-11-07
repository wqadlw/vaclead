# SPEC-CB · 移动端适配轮：日活线窄屏可用（Agent B 认领）

> 立项：2026-11-07 W59 · 销售拨打场景天然在手机上（tel: 链接已是
> 移动原生），但 fixed 228px 侧栏在窄屏吃掉 60% 宽度。头部平台
> （剑鱼/探迹）移动端是标配；我们用抽屉式适配达到 80 分可用。

## CB1 AppShell 响应式（一处改动覆盖全部页面）

- 侧栏：`-translate-x-full md:translate-x-0`（移动抽屉化，md 起常驻），
  变换过渡 200ms；移动开态加半透明背板（点击关闭）
- 主列：`ml-[228px]` → `md:ml-[228px]`
- 顶栏加汉堡按钮（md:hidden，Menu 图标）开抽屉；导航项点击后关抽屉
- 登录页居中布局天然适配；页面级 KPI 网格已有 md: 断点 ✓

## CB2 移动视口 E2E（Playwright devices 仿真）

- 新 spec mobile.spec.ts（390×844）：登录 → 联系人中心 →
  断言侧栏初始在屏外/汉堡可见 → 点开抽屉 → 点导航项 → 抽屉关闭且
  tel: 拨打链接存在于 DOM

## 边界

- 桌面体验零改动（md 断点以上一切照旧）
- 表格类页面（线索列表/报表）窄屏以横向滚动兜底，不做重排

## 落地记录（2026-11-07 W59）

- AppShell 五处改动：背板/抽屉化侧栏（transition 200ms，md 常驻不变）/
  主列 md:ml-228/顶栏汉堡（md:hidden）/导航项点击关抽屉。
- mobile.spec.ts：390×844 仿真——侧栏初始屏外/汉堡开抽屉/点导航自动
  收起/tel: 链接在 DOM。API 登录与 journey 同款。
- **两个坑如实记**：①mobileNav 状态插错组件（锚定在别的组件的 scheme
  state 上）——build 报 Cannot find name 当场拦下，教训=插 state 前先
  确认组件边界；②**E2E 服务的是上次 build 的 out/**——改前端后必须
  先 build 再跑 E2E（本轮第一次 mobile 失败就是旧产物）。
- 后端零改动（pytest 沿用 332 绿）。门禁：build + E2E 8（含移动）全绿。
