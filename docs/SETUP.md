# 环境与外部服务配置指引（SETUP）

## 本地依赖

- Python 3.11+（`pip install -r backend/requirements.txt`，网络不畅用 `--index-url https://pypi.tuna.tsinghua.edu.cn/simple`）
- Node.js 18+（`cd frontend && npm install`）

## 高德开放平台 Key（可选 — POI 地图拓客）

1. 访问 https://lbs.amap.com 注册账号
2. 进入「控制台」→「应用管理」→「创建新应用」，应用类型选 **Web服务**
3. 为应用添加 Key，获得一串 32 位 key
4. 完成个人实名认证可大幅提升免费配额（基础服务约 15 万次/月）
5. 将 Key 粘贴到 vaclead「设置 → 数据源 Key」页保存
6. 支持多 Key 轮询：用英文逗号分隔多个 key 一起粘贴

> 未配置时：POI 采集功能禁用，其余功能不受影响。

## 知了标讯 API Key（可选 — 招标自动采集）

1. 访问知了标讯开放平台注册（注册赠送 200 次免费 API 调用）
2. 获取 API Key 后填入「设置 → 数据源 Key」
3. 未配置时：使用「拓客任务」页的 CSV 导入方式录入招标公告（格式见 `data/sample_tenders.csv`）

## 数据合规说明

- 平台仅使用公开公示信息（招标公告、公开企业信息、地图 POI）
- 法定代表人等自然人联系方式按个保法要求谨慎处理，v1 不采集
- 商业数据 API（如需）建议采购正规数据商授权服务

## 数据备份

- SQLite 数据库位于 `data/vaclead.db`，系统每晚 02:00（随公海回收任务）自动备份至 `backups/`，保留最近 7 份

## 常见问题

- **登录 401**：确认后端已启动（http://127.0.0.1:8000/docs 可访问）；首次启动自动建库并写入种子账号
- **POI 采集 400 "缺少高德Key"**：按上文配置高德 Key
- **端口占用**：修改 `scripts/dev.bat` 中端口并同步改前端 `NEXT_PUBLIC_API_BASE`
