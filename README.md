# vaclead · 真空设备智能拓客平台

面向真空设备行业的自用智能拓客平台：筛出下游工厂 → 识别"谁在买设备"的招标意图信号 → 线索跟进闭环。

## 功能

1. **企业库多维筛选** — 下游行业级联（半导体/镀膜加工/光伏/锂电/冻干/热处理）×省市区×注册资本×信号×评分；同行自动打标排除
2. **ICP 规则评分** — 0-100 可解释评分（行业/设备词/资本/制造属性/资质加权）
3. **招投标意图信号** — 设备词表匹配招标公告，时间衰减意图分，S/A/B 线索分级
4. **高德 POI 拓客任务** — 10 个工业强市 × 行业关键词四叉树采集（简化区县粒度）
5. **线索 CRM** — 状态机流转、跟进时间线、撞单保护、30 天公海回收
6. **Agent 工作日志** — 全部 Agent 决策可观测

## 技术栈

- 后端：Python 3.11 · FastAPI · SQLAlchemy 2 · SQLite(WAL+FTS5) · LangGraph（狩猎图编排）
- 前端：Next.js 14 · React 18 · Ant Design 5 · Tailwind CSS
- 架构细节见 `docs/SPEC.md`

## 快速启动

```bash
scripts\dev.bat        # 一键启动后端(:8000)+前端(:3000)
```

账号：`admin/admin123`（管理员）、`sales/sales123`（销售）。

首次使用：
1. 登录后在「设置」页配置高德 Key（可选，POI 采集用）与知了标讯 Key（可选）
2. 「拓客任务」页导入 `data/sample_enterprises.csv`（52 家示例企业）与 `data/sample_tenders.csv`（20 条招标公告）
3. 去「企业筛选」页体验筛选/评分/信号；「线索看板」转线索跟进

## 目录

```
backend/    FastAPI 应用（app/models.py 数据契约、app/agents/ Agent框架、app/collectors/ 采集器、app/api/ 路由）
frontend/   Next.js 六页面
config/     vacuum.yaml 行业词表与评分配置
data/       示例数据（企业/招标 CSV）
scripts/    dev.bat 启动 / check.bat 质量门禁
docs/       SPEC.md 规格 · SETUP.md 环境指引
```

## 质量门禁

```bash
scripts\check.bat      # pytest 全量 + next build
```
