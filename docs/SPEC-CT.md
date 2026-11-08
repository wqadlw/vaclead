# SPEC-CT · 移动端导航重构：底部导航栏+顶栏精简（Agent B 主线程+子 Agent 并行）

> 立项：2026-11-08 · 用户："移动端太差，把底部导航栏弄出来，顶部栏
> 好好设计，参考类似移动端 app"。**为谁服务**：销售外勤单手操作——
> 拇指可达的底部导航是移动 app 标配（参考 高德/企查查/Linear mobile），
> 抽屉藏得太深。SPEC-CB 的抽屉保留为"更多"入口。

## CT1 底部导航栏（<md 显示，子 Agent）

- **固定底部**（fixed bottom，md:hidden），5 个 tab：
  今日(工作台 /) · 联系人(/contacts) · 地图(/map) · 线索(/leads) · 更多(开抽屉)
- 图标+标签（11px），active 态 accent 色+加粗；安全区 padding
  env(safe-area-inset-bottom)
- 主内容 pb-16 md:pb-0 让位
- lucide 图标：Gauge/PhoneCall/Map/Radar/Menu

## CT2 顶栏移动端精简（子 Agent）

- <md 隐藏：搜索文字按钮（改图标钮）、默认工作区/全国选择器、? 快捷键
- 保留：汉堡（开抽屉）、铃铛、头像
- 图标化搜索（Search 图标钮，点击仍开命令面板）

## CT3 主题切换钮

- 移动端顶栏保留（顶栏精简后仍有位置）

## CT4 验收

- mobile.spec.ts 扩展：底部导航出现、5 tab 可点、点击切换页面、
  active 高亮、"更多"开抽屉
- build + E2E 全量全绿

## 边界

- 桌面端（md+）零改动；侧栏抽屉逻辑保留（SPEC-CB 不回退）
- 不改任何页面内部布局（仅壳）
