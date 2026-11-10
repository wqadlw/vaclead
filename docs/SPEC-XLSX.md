# SPEC-XLSX：企业导入 .xlsx 直读

> 第十七次功能轮。用户场景：对照表 5271 是 Excel——现在得"另存为 CSV"
> 再拖入，多一步且中文易乱码。让导入链路直接吃 .xlsx（openpyxl 只读解析）。

## 1. 后端（imports.py）

- requirements 补 `openpyxl>=3.1`
- 新 `_parse_upload(raw: bytes, filename: str) -> tuple[list[str], list[dict]]`：
  - `.xlsx`（后缀判定）→ openpyxl.load_workbook(BytesIO(raw), read_only=True,
    data_only=True)，首个工作表，首行非空单元格=表头，其余行按表头 zip 成
    dict（None→""）；`.csv`/其余 → 现有 _decode_csv_bytes 路径
  - 三个入口统一走它：`/import/enterprises`、`/import/enterprises/preview`、
    `/import/gongshang-csv`（若其结构兼容——兼容才接，不兼容跳过并说明）
- 语义不变：xlsx 的表头同样过 _HEADER_SYNONYMS/显式 mapping

## 2. 前端（tasks 页）

- enterprises Dropzone `accept=".csv,.xlsx"` + 标题/提示文案更新
  （"支持 Excel(.xlsx) / CSV"）；tenders 不动

## 3. 测试（test_spec_imp.py 追加）

- openpyxl 内存构造 workbook（表头中文名+2 行数据）→ preview 返回正确
  映射/行数；xlsx 正式导入 inserted 断言；坏 xlsx（空文件改名）→ 4xx
  带可读 detail

## 4. 非目标

- 不做 .xls（老格式）与多 sheet 选择（首 sheet 即业务口径）
- 不做图片/合并单元格容错（read_only 模式天然跳过样式）
