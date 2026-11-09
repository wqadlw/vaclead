# SPEC-IMP：企业 CSV 上传导入向导（对齐粘贴向导）

> 第十五次功能轮。现状：粘贴导入已有 twenty spreadsheet-import 式两步
> 向导（tasks 页：粘贴→映射确认→confirm 提交）；**文件上传路径是裸直传**
> （Dropzone → /api/enterprises 零向导）。本轮把上传路径对齐：选文件 →
> 映射确认 + 首行预览 + **去重预演** → 确认导入 → 结果含 skipped 明细。
> 直接服务"对照表 5271"这类批量入库动作。

## 1. 后端契约（imports.py）

### 1.1 `POST /api/import/enterprises/preview`（multipart file，admin）

```json
{
  "detected_delimiter": ",",
  "headers_raw": ["公司名称", "省份", "…"],
  "headers_synonymized": ["name", "province", "…"],   // _HEADER_SYNONYMS 归一后
  "mapping_suggestion": [{"col": 0, "field": "name"}, {"col": 1, "field": "province"}, {"col": 2, "field": "skip"}],
  "preview_rows": [ {"name": "…", "province": "…"} × ≤5 ],  // 按建议映射后的行
  "total_rows": 5271,
  "dup_preview": {"existing": 12, "new": 35}   // 按企业名预演 resolve_enterprise
}
```

- delimiter 探测与粘贴路径同规则（\t vs , 计数）
- dup_preview：收集全部行 name → 单查 `Enterprise.name.in_(names)` 计数
  （一次查询，不做逐行 resolve）；空名行计入 new 侧的 skipped 由正式导入处理

### 1.2 `POST /api/import/enterprises` 增量（不破坏旧调用）

- 新增可选 form field `mapping`：JSON 数组字符串 `["name","province","skip",…]`
  （下标=列序）。**显式 mapping 存在时优先**（skip 字段跳过该列）；缺省走
  现有 _HEADER_SYNONYMS 路径
- 映射应用抽公共函数（同义词归一与显式 mapping 两条入口共用一份行→字段逻辑）
- 返回扩展：`"skipped_details": [{"row": 17, "reason": "缺少企业名称"}] ×≤10`
  （现有 skipped 计数保留）

## 2. 前端契约（tasks 页，Agent B）

- enterprises Dropzone 改造为两步：选/拖文件 → 先调 preview → 弹向导
  Modal（**复用粘贴向导的映射 UI 结构**：每列一个 Select 选目标字段，
  选项=name/province/city/district/address/business_scope/established_at/
  legal_rep/registered_capital_wan/industry_key/skip）+ 首行预览表 +
  「库内已有 X 家，将新增 Y 家」徽章 + 「确认导入 N 行」按钮
- 确认 → FormData（file + mapping JSON）→ 结果 Toast（新增/合并/跳过）
  + skipped_details >0 时在弹窗内展示明细行（行号+原因）
- tenders 上传保持直传不动
- 移动端：Modal 走全站兜底规则（自动 390 适配），映射行纵向排

## 3. 测试（test_spec_imp.py）

- preview：表头同义词归一/映射建议（首列 name 兜底+skip 缺省）/
  delimiter 探测/dup_preview 计数（预置 2 家同名库内企业）
- 正式导入带显式乱序 mapping 生效（列顺序打乱也能落对字段）
- 不带 mapping 旧语义兼容（同义词路径回归）
- skipped_details：无名称行计数+明细
- 401（非 admin）

## 4. 非目标

- 不做 .xlsx 解析（粘贴路径已覆盖 Excel Ctrl+V；.csv 上传足够）
- 不做字段级校验报告（注册资本非数字等沿用静默容错）
- tenders 向导（招标列映射是另一套语义，后续轮）
