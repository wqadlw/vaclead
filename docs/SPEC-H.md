# SPEC-H · E2E 测试体系 + v1.0 发布（自主选向收尾轮）

> 立项：2026-10-26 · 用户授权自主选向
> 理由：30 轮功能建设完成（147 后端测试），但**前端回归全靠手工**——每轮浏览器验收
> 占大量时间且有遗漏风险。把销售旅程固化成 Playwright E2E，之后每轮开发都有
> 自动化的全链路守门。这是"标杆项目"的工程底座，也是 v1.0 发布的前提。

## 工作项

### H1 E2E 基建
- frontend 已有 playwright 1.64（npx 可用）——补 @playwright/test + chromium 安装
- playwright.config.ts：baseURL http://127.0.0.1:8000（走后端托管静态导出，单进程真实形态）
  webServer 自动拉起 uvicorn（独立测试库 VACLEAD_DB=tmp，不碰真库）
- 全局 setup：seed 一组确定性数据（paste 导入企业+信号+规则），teardown 清理

### H2 销售旅程 E2E（journey.spec.ts，一条测试串全链路）
1. 登录 admin → 落工作台 → KPI/作战区渲染
2. 企业页 → 快捷筛选"有联系电话" → 计数变化
3. 高级筛选构建器 → 执行 → 结果出现
4. 点企业名 → Side Panel 预览 → 展开情报页
5. 情报页四页签（档案四段评分/图谱/联系人/信号）
6. 情报页转线索 → 商机管线看板出现卡片
7. 看板拖拽（或点击卡片改阶段）→ 赢率更新
8. 待办勾选完成
9. 主题切换（亮→暗）data-theme 断言
10. 快捷键 g2 导航
断言用 DOM 可见文本，不依赖实现细节。

### H3 关键回归单测（regress.spec.ts）
- 行内编辑（点资本格→输入→回车→保存）
- 视图保存/应用
- 通知铃铛未读数
- 命令面板 Ctrl+K 搜企业

### H4 CI 接线
- scripts/ci.bat 加 `npx playwright test`（前端 build 后）
- package.json scripts."test:e2e"

### H5 v1.0 发布
- CHANGELOG.md（30 轮里程碑分类归总）
- git tag v1.0.0 + GitHub Release（API 创建）

## 门禁
E2E 全绿 + 后端 147 全绿 + ci.bat 一键通过 → tag 发布。
