# SPEC-AH · 联系方式扩池轮（Agent B 认领）

> 立项：2026-11-04 W52 · 用户拍板"联系方式是拓客平台最重要的"。
> 首轮全量回填战果：可拨电话覆盖 24→124 家（1.2%→6.0%），但暴露三处结构性
> 限制——①候选池硬编码 `source=ccgp_buyer`（59 家供应商公告里的采购人联系
> 方式被浪费）②单轮限额 `min(limit,50)` 使 AA1 的 300/天实际只跑 50 ③电话
> 存在 title 槽位里（GLOB 数字判断是语义债）。

## 调研吸收

- 风鸟/企查查/天眼查的联系方式核心=**企业年报自主公示**（gsxt 法定公开）+ 公开网页聚合；
  它们卖工商固话不卖决策人手机——印证我们的"公告法定公开"路线质量更高（在采买家）
- [python-phonenumbers](https://github.com/daviddrysdale/python-phonenumbers)（Google libphonenumber 移植）
  是公告文本抽电话的最佳实践；本项目域内号码形态固定（手机+座机+分机），
  正则+去重已够用，保持零新依赖
- ggzy 公告详情页 WAF（360 网站卫士）：浏览器池过墙维持 P2（DATA_ACQUISITION 已记）

## AH1 回填池语义修正 + 扩池（核心）

- **池 A（买家直采，语义不变）**：`source in (ccgp_buyer, ggzy_buyer)` 且有
  ccgp 信号 URL 且无可拨电话 → 联系人挂本企业
- **池 B（供应商公告挖采购人，新增）**：`source='ccgp'`（中标供应商）的公告
  详情里，采购单位/项目联系人/代理机构是**采购人侧**信息 → 解析 `buyer_name`
  → `resolve_enterprise(source="ccgp_buyer")` 建档或复用 → 联系人挂**采购人**
  （evidence 记"来自供应商中标公告「XX」"，可溯源）
- 两池合并，招标中优先排序不变；单轮限额 = body.limit（去掉 min(...,50) 截断）

## AH2 电话规范化（还 GLOB 语义债）

- `Contact.phones_json`（TEXT，默认 "[]"，幂等 ALTER）
- `app/phones.py`：`extract_phones(text)`——手机 `1[3-9]\d{9}` / 座机
  `0\d{2,3}-\d{7,8}(-\d{3,4}分机)?`，去重保序；回填写入时同步抽取
- **存量迁移**：回填入口先跑一遍 `phones_json='[]'` 且 title 含数字的联系人
  （幂等，cron 自愈旧数据）
- `has_callable` 判断升级：`phones_json != '[]'` OR title GLOB

## AH3 露出

- contacts 端点序列化加 `phones` 数组；情报页联系人抽屉把号码渲染成可复制
  chips（title 原文保留在下方做证据）

## 边界

- 供应商公告 → 采购人建档可能撞"同名采购人已有实体"——resolve_enterprise
  归一复用，不重复建档
- 官网联系页抓取、高德 POI（等用户 Key）、gsxt 年报电话：本轮不做，记档
- 前端零新依赖，chips 用现有 Tag 样式
