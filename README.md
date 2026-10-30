# vaclead · 工业设备智能拓客平台

> 从**官方公示数据**到**可拨打线索**到**商机成交**的全链路自托管系统。
> 单进程 · 零外部服务依赖 · SQLite 即真源。

## 这是什么

一个面向**工业设备全行业**销售团队的智能客户获取平台（真空/半导体/机床/化工/食品包装/环保/自动化等多行业词表，配置驱动可增删）。它不依赖任何付费数据 API——直接从国家公共资源交易平台与中国政府采购网的**官方公开数据**中挖出「谁在买设备」：

```
官方数据采集 → 信号引擎(词表匹配) → 双向建档(中标方+采购人) → 自动化规则
     → 商机管线(看板/日历) → 情报页(采购关系图谱/联系人) → 导出/跟进/成交
```

## 核心能力

| 域 | 能力 |
|---|---|
| **数据获取** | data.ggzy.gov.cn 官方中标 API 直采（免 Key）+ ccgp.gov.cn 政采公告全文检索；采集韧性层（错误积分制/指数冷却，吸收 crawlee） |
| **联系人解锁** | 政采公告详情页的**法定公开联系方式**（项目联系人/电话/地址）自动回填——每条带公告溯源 |
| **信号引擎** | 34 个设备强词表匹配；intent=Σ分×0.5^(Δ天/30) 半衰；ICP 可解释评分（行业/资本/制造/资质） |
| **自动化规则** | 条件组（信号类型/行业/来源/分数/有电话/意图分）→ **动作链**（通知→建线索→定级→建待办）+ 否则分支 + dry-run 试运行 |
| **商机管线** | 拖拽看板（阶段赢率自动更新）+ 日历视图（预计成交日拖拽改期）+ 商机三要素（金额/赢率/成交日） |
| **情报页** | 采购关系图谱（轮辐 SVG，边宽=金额）、关键联系人（带溯源）、统一时间线（跟进+信号合流） |
| **检索** | Meilisearch 投影（SQLite 真源双路，毫秒组合筛选+分面）；高级筛选构建器（AND/OR）；快捷筛选 Quick picks |
| **企业级** | 单进程部署（FastAPI 托管静态导出）、JWT+登录限流、每日增量采集/画像/备份调度、140 项测试 |

## 快速开始

```bash
# 后端（Python 3.11+）
cd backend && pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# 前端构建（Node 18+）
cd frontend && npm install && npx next build   # 产物 out/ 由后端托管

# 浏览器打开 http://127.0.0.1:8000 （admin/admin123）
```

可选：Meilisearch（检索加速）、高德 Key（POI 地图拓客）、EmailEngine（邮件触达）——**全部可选，缺省即降级，不影响主功能**。

## 首次跑数据

登录后 → 拓客任务 → 「官方数据采集（免 Key）」→ 选关键词 → 开始采集。
每日 03:30 自动增量，04:08 自动回填新公告的联系人电话。

## 技术栈

FastAPI + SQLAlchemy 2 + SQLite(WAL+FTS5) + Meilisearch(可选) ｜ Next.js 14 + Semi Design ｜ APScheduler ｜ 140 pytest

## 文档

- [docs/DATA_ACQUISITION.md](docs/DATA_ACQUISITION.md) — 数据获取工程学（合规边界/限流实测/接口契约）
- [docs/OPERATIONS.md](docs/OPERATIONS.md) — 运维手册（部署/调度/备份）
- [docs/WORKLOG.md](docs/WORKLOG.md) — 26 轮开发日志
- [docs/SPEC-B.md](docs/SPEC-B.md) — 数据质量（归一/复核/画像）

## 合规红线

只采官方主动公开的数据；固定 UA + 限速 + 指数冷却；不绕验证码不破解 WAF；联系人全部带公告溯源。详见 DATA_ACQUISITION.md。

## License

MIT
