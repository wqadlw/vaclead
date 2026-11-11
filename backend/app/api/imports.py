"""数据导入路由：企业 CSV / 招标公告 CSV（词表匹配生成意图信号）。"""
from __future__ import annotations

import csv
import io
import json
import logging
import math
import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from urllib.parse import quote
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth import admin_only
from ..collectors.tender import _decode_csv_bytes
from ..db import get_session
from ..models import Enterprise, EnterpriseAlias, Signal, User, utcnow
from ..normalize import FUZZY_THRESHOLD, REVIEW_THRESHOLD, canonical_name, resolve_enterprise
from ..vertical import (
    detect_industry,
    downstream_industries,
    signal_weights,
)

logger = logging.getLogger("vaclead.api.imports")

router = APIRouter(tags=["imports"])

# 从标题/正文中提取企业名的保守模式（XX公司/厂/集团 专名）
# 企业名抽取（两级）：
# 1) _COMPANY_RE 严格模式：以"公司/集团/厂"等后缀结尾的完整企业名（含括号分段）
# 2) _ORG_RE 宽松模式：采购单位常见形态（XX中心/XX局/XX学院/XX研究所/XX医院/XX学校/XX委员会）
# 顺序：先标题后正文；严格模式优先于宽松模式。
_COMPANY_RE = re.compile(
    r"([\u4e00-\u9fa5A-Za-z0-9（）()]{2,28}?(?:有限公司|股份有限公司|有限责任公司|公司|集团|工厂|厂))"
)
_ORG_RE = re.compile(
    r"([\u4e00-\u9fa5]{2,18}?(?:研究中心|研究所|研究院|检测中心|疾控中心|交易中心|管理中心|服务中心|"
    r"采购中心|管理局|财政局|发改委|人民政府|委员会|大学|学院|学校|医院))"
)
# 宽松模式的黑名单：行政区名/泛称不是采购单位
_ORG_BLACKLIST_SUFFIX = ("县", "市", "省", "区", "镇", "乡", "园区", "新区")
# 项目编号形态（zycgr24041501 / ZF2026-17-0968）：不是企业名
_PROJECT_CODE_RE = re.compile(r"^[A-Za-z]{1,12}[-]?\d{4,}([-A-Za-z0-9]*)?$")
# 摘要表格头粘连词：命中则从最后一次出现处截断，保留其后企业名
_JUNK_PREFIX_WORDS = (
    "评审总得分", "中标（成交）金额", "中标(成交)金额", "评审得分", "成交金额",
    "中标金额", "名称供应商", "供应商名称", "供应商地址", "评审专家", "主要标的信息",
    "服务要求", "项目编号", "代理机构", "采购人名称", "合同履行",
)


def _org_name_ok(name: str) -> bool:
    """过滤'某某县/某某市'这类纯行政区名（宽松模式误抓）。"""
    return not name.endswith(_ORG_BLACKLIST_SUFFIX)


def _clean_company_candidate(name: str) -> str:
    """企业名候选净化：剥表格头粘连 / 拒项目编号；不合格返回空串。"""
    name = name.strip("（(〕 :：,，、 ")
    if not name or len(name) < 4:
        return ""
    if _PROJECT_CODE_RE.match(name):
        return ""
    for word in _JUNK_PREFIX_WORDS:
        if word in name:
            name = name.rsplit(word, 1)[-1].strip("（(〕 :：,，、 ")
            break
    if len(name) < 4 or _PROJECT_CODE_RE.match(name):
        return ""
    return name


def extract_enterprise_name(title: str, content: str) -> str:
    """从公告标题/正文抽取企业（或采购单位）名：严格企业名 > 宽松机构名，先标题后正文。

    抽不到返回空串——调用方不应把整条公告丢弃（落"待归属"占位，见 imports）。
    """
    for text in (title, content):
        if not text:
            continue
        m = _COMPANY_RE.search(text)
        if m:
            name = _clean_company_candidate(m.group(1))
            if not name:
                continue
            # 常见误抽：后缀前是"建设/采购/项目/内容"等动词性词组的直接否定
            if not re.search(r"(建设|采购|项目|内容|施工|监理|咨询|设计|服务|货物)$", name[:4]):
                return name
    for text in (title, content):
        if not text:
            continue
        candidates = [
            _clean_company_candidate(m.group(1))
            for m in _ORG_RE.finditer(text)
        ]
        # 取最长合法匹配（"中国科学院某光学精密机械研究所"优于截断的"中国科学院"）
        candidates = [c for c in candidates if c and _org_name_ok(c)]
        if candidates:
            return max(candidates, key=len)
    return ""


def _decay(base_score: float, pub_date: str | None) -> float:
    half_life = float(signal_weights().get("intent_half_life_days", 30))
    if pub_date:
        try:
            d = datetime.strptime(pub_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            days = max((datetime.now(timezone.utc) - d).days, 0)
            return base_score * (0.5 ** (days / half_life))
        except ValueError:
            pass
    return base_score


def recompute_intent(session: Session, enterprise_id: int) -> float:
    signals = session.query(Signal).filter(Signal.enterprise_id == enterprise_id).all()
    score = sum(_decay(s.base_score, s.pub_date) for s in signals)
    ent = session.get(Enterprise, enterprise_id)
    if ent is not None:
        ent.intent_score = round(score, 2)
        ent.updated_at = utcnow()
    return round(score, 2)


def apply_icp(ent: Enterprise) -> None:
    """ICP 打分（与 agents.impl.icp_judge_rule 同一套规则）。

    优先调用 Agent 框架实现（可观测）；框架未就绪时退回本地同规则计算。
    """
    try:
        from ..agents.impl.icp_judge_rule import judge_enterprise

        result = judge_enterprise(
            name=ent.name,
            business_scope=ent.business_scope or "",
            registered_capital_wan=ent.registered_capital_wan,
            industry_key=ent.industry_key,
        )
        ent.icp_score = result["icp_score"]
        ent.set_reasons(result["icp_reasons"])
        ent.is_competitor = result["is_competitor"]
        # agent 路径不回行业——这里补齐（空值不覆盖已有分类）
        from ..vertical import detect_industry

        ind = detect_industry(f"{ent.name} {ent.business_scope or ''}")
        if ind is not None and not ent.industry_key:
            ent.industry_key = ind["key"]
            ent.industry_name = ind["name"]
        return
    except Exception:
        pass
    from ..vertical import (
        device_strong_terms,
        icp_weights,
        manufacture_or_sale_terms,
        manufacturing_terms,
        qualification_terms,
    )

    w = icp_weights()
    scope = ent.business_scope or ""
    text_all = f"{ent.name} {scope} {ent.industry_name or ''}"
    reasons: list[dict] = []
    score = 0.0
    ind = detect_industry(text_all)
    if ind is not None:
        pts = w.get(f"industry_tier{ind['tier']}", 0)
        score += pts
        reasons.append({"rule": f"行业命中:{ind['name']}(tier{ind['tier']})", "points": pts})
        ent.industry_key = ind["key"]
        ent.industry_name = ind["name"]
    strong_terms = device_strong_terms()
    mfg_terms = manufacturing_terms()
    qual_terms = qualification_terms()
    from ..vertical import manufacture_or_sale_terms

    # 同行判定：名称含"真空"且具备设备制造特征（名称含"设备"或经营范围含设备制造/真空泵生产等）
    # 例：XX真空设备有限公司✓、YY真空科技(产真空泵)✓；而真空镀膜材料厂/镀膜加工厂是客户✗
    equipment_markers = ("设备制造", "设备生产", "设备销售", "真空泵", "真空机组")
    is_competitor = ("真空" in ent.name) and (
        "设备" in ent.name or any(m in scope for m in equipment_markers)
    )
    if not is_competitor and any(t in scope for t in strong_terms):
        score += w.get("device_usage_terms", 0)
        reasons.append({"rule": "经营范围含设备使用词", "points": w.get("device_usage_terms", 0)})
    cap = ent.registered_capital_wan
    if cap is not None:
        if cap >= 1000:
            score += w.get("capital_gte_1000w", 0)
            reasons.append({"rule": "注册资本≥1000万", "points": w.get("capital_gte_1000w", 0)})
        elif cap >= 100:
            score += w.get("capital_100w_1000w", 0)
            reasons.append({"rule": "注册资本100-1000万", "points": w.get("capital_100w_1000w", 0)})
    if any(t in scope for t in mfg_terms):
        score += w.get("manufacturing_terms", 0)
        reasons.append({"rule": "经营范围含生产/制造/加工", "points": w.get("manufacturing_terms", 0)})
    if any(t in text_all for t in qual_terms):
        score += w.get("qualification", 0)
        reasons.append({"rule": "专精特新/高企标识", "points": w.get("qualification", 0)})
    ent.icp_score = min(round(score, 1), float(w.get("max_score", 100)))
    ent.set_reasons(reasons)
    ent.is_competitor = 1 if is_competitor else 0


class PasteImportIn(BaseModel):
    """粘贴导入（吸收 twenty spreadsheet-import 两步模式）：

    mode=auto：智能识别分隔符与表头（同义词归一）；
    mode=confirm：前端列映射确认后回传——headers=显式表头名列表（顺序对应列），
    mapping=每列目标字段（"skip" 表示忽略该列）。"""
    text: str
    mode: str = "auto"  # auto | confirm
    headers: list[str] | None = None  # mode=confirm 时的显式表头
    mapping: list[str] | None = None  # mode=confirm 时每列目标字段（skip 忽略）


# 表头同义词归一（spreadsheet-import 的"列映射"思想：不要求用户改表头）
_HEADER_SYNONYMS: dict[str, str] = {
    "name": "name", "企业名称": "name", "企业": "name", "名称": "name",
    "公司": "name", "公司名称": "name", "客户名称": "name",
    "province": "province", "省": "province", "省份": "province",
    "city": "city", "城市": "city", "市": "city",
    "district": "district", "区县": "district", "区": "district", "县区": "district",
    "address": "address", "地址": "address",
    "business_scope": "business_scope", "经营范围": "business_scope", "scope": "business_scope",
    "registered_capital_wan": "registered_capital_wan", "注册资本": "registered_capital_wan",
    "注册资本(万元)": "registered_capital_wan", "注册资本(万)": "registered_capital_wan",
    "capital": "registered_capital_wan",
    "industry_key": "industry_key", "行业": "industry_key", "行业键": "industry_key",
    "established_at": "established_at", "成立日期": "established_at",
    "成立时间": "established_at", "成立年份": "established_at",
    "legal_rep": "legal_rep", "法定代表人": "legal_rep", "法人": "legal_rep",
    "法人代表": "legal_rep",
}


def _import_headers(
    raw_fields: list[str],
    mapping: list[str] | None,
    synonyms: dict[str, str] | None = None,
) -> list[str]:
    """表头→目标字段归一公共函数（SPEC-IMP 1.2：两条入口共用一份行→字段逻辑）。

    - 显式 mapping 存在时优先：列序=下标，"skip" 表示忽略该列（skip 键落不进
      任何企业字段，dict(zip) 后自然被丢弃）；
    - 缺省走同义词表归一：命中→字段名，未命中→保留原表头名
      （落库时只读已知字段键，原名键自然忽略——与既有上传/粘贴路径语义一致）；
    - SPEC-GSH：synonyms 参数支持工商补数注入专用词表（_GS_BACKFILL_SYNONYMS），
      缺省仍为 _HEADER_SYNONYMS（既有 /import/enterprises 行为逐字节不变）。
    """
    if mapping:
        return list(mapping)
    syn = synonyms if synonyms is not None else _HEADER_SYNONYMS
    return [syn.get(f.lower(), syn.get(f, f)) for f in raw_fields]


def _parse_mapping_form(mapping: str | None) -> list[str] | None:
    """SPEC-IMP 1.2：multipart Form field `mapping` 解析（JSON 数组字符串）。

    缺省/空串返回 None（走同义词路径）；非法 JSON 或非字符串数组 422。
    """
    if mapping is None or not mapping.strip():
        return None
    try:
        parsed = json.loads(mapping)
    except json.JSONDecodeError:
        raise HTTPException(422, "mapping 需为 JSON 数组字符串")
    if not isinstance(parsed, list) or not all(isinstance(x, str) for x in parsed):
        raise HTTPException(422, "mapping 需为字符串数组")
    return parsed


def _parse_upload(raw: bytes, filename: str) -> tuple[list[str], list[dict]]:
    """上传文件统一解析（SPEC-XLSX）：.xlsx 走 openpyxl 只读直读，其余后缀走既有 CSV 路径。

    返回 (表头列表, 行字典列表)。两个分支语义对齐：
    - xlsx 分支：首个工作表（业务口径，多 sheet 选择是非目标）、首行非全空行=表头、
      其余行按表头 zip 成 dict；单元格 None→""，数值/日期统一转字符串（下游
      float() / [:10] 截断天然兼容）；行全空跳过。read_only+data_only 模式不加载
      样式与公式缓存对象（防样式炸弹，SPEC-XLSX）；坏文件 → 400 可读 detail。
    - CSV 分支：_decode_csv_bytes + csv.DictReader 原样包一层（不做分隔符探测，
      与既有 /import/enterprises 行为逐字节一致；preview 的 Tab 探测逻辑保留在端点内）。
      DictReader 的空行跳过 / 缺列 restval=None / 多余列 restkey 语义原样保留。
    """
    if filename.endswith(".xlsx"):
        from openpyxl import load_workbook  # 延迟导入：仅 xlsx 路径需要（SPEC-XLSX）

        try:
            wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        except Exception:  # noqa: BLE001 — 坏文件/非 xlsx 字节一律给出可读 400
            raise HTTPException(
                400, "Excel 文件解析失败，请确认是有效的 .xlsx（另存为 Excel 工作簿）"
            )
        try:
            ws = wb.worksheets[0]
            rows_iter = ws.iter_rows(values_only=True)
            headers: list[str] = []
            for row in rows_iter:
                # 首个非全空行=表头；None→"" 保留列位（删列会让数据行 zip 错位）
                if any(v is not None and str(v).strip() for v in row):
                    headers = ["" if v is None else str(v) for v in row]
                    break
            parsed: list[dict] = []
            for row in rows_iter:
                if not any(v is not None and str(v).strip() for v in row):
                    continue  # 行全空跳过（SPEC-XLSX）
                parsed.append({
                    h: ("" if v is None else str(v)) for h, v in zip(headers, row)
                })
        finally:
            wb.close()  # read_only 模式需显式关闭释放句柄
        return headers, parsed

    # CSV / 其余后缀：既有路径原样包一层（SPEC-XLSX 要求 .csv 行为逐字节不变）
    text_content = _decode_csv_bytes(raw)
    reader = csv.DictReader(io.StringIO(text_content))
    fieldnames = list(reader.fieldnames or [])
    rows: list[dict] = list(reader)
    return fieldnames, rows


def apply_enterprise_row_fields(session: Session, ent: Enterprise, row: dict, batch_id: str) -> None:
    """行→企业字段落库公共函数（SPEC-IMP 1.2：粘贴/上传两条导入路径共用）。

    约定：row 的键是归一后的目标字段名（name 已由调用方 resolve，不在此处理）。
    语义与两条路径的既有行为对齐：文本字段非空覆盖；established_at/legal_rep
    仅空值补填不覆盖；注册资本浮点容错（非数字静默跳过，SPEC 非目标）；
    industry_key 命中行业表时补 industry_name；最后 ICP 重打分并 flush。
    """
    ent.source_batch_id = batch_id
    for field in ("province", "city", "district", "address", "business_scope"):
        if row.get(field):
            setattr(ent, field, row[field])
    if row.get("established_at") and not ent.established_at:
        ent.established_at = str(row["established_at"])[:10]  # YYYY-MM-DD 前缀容错
    if row.get("legal_rep") and not ent.legal_rep:
        ent.legal_rep = row["legal_rep"]
    if row.get("registered_capital_wan"):
        try:
            ent.registered_capital_wan = float(row["registered_capital_wan"])
        except (TypeError, ValueError):
            pass
    provided_key = (row.get("industry_key") or "").strip()
    if provided_key:
        ent.industry_key = provided_key
        for ind in downstream_industries():
            if ind["key"] == provided_key:
                ent.industry_name = ind["name"]
                break
    apply_icp(ent)
    session.flush()


@router.post("/import/paste")
async def import_paste(
    body: PasteImportIn,
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    """粘贴导入：销售从 Excel 直接 Ctrl+V，无需存 CSV。

    解析顺序：按行拆分 → 首行表头（同义词归一）→ 分隔符自动探测（Tab 优先，
    逗号次之）→ 数据行逐条走 resolve_enterprise（复用归一/去重/评分全链路）。
    """
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(422, "粘贴内容为空")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        raise HTTPException(422, "至少需要表头 + 1 行数据")

    # 分隔符探测：表头行 Tab 数多于逗号数 → Tab
    header_line = lines[0]
    delim = "	" if header_line.count("	") >= header_line.count(",") else ","

    if body.mode == "confirm":
        if not body.headers or not body.mapping or len(body.headers) != len(body.mapping):
            raise HTTPException(422, "confirm 模式需提供 headers 与等长 mapping")
        headers = list(body.mapping)  # 直接使用用户确认的目标字段
        skip_first = True  # 两种模式首行都是表头；confirm 的表头是前端回显的原文
    else:
        headers_raw = [h.strip() for h in header_line.split(delim)]
        # SPEC-IMP 1.2：同义词归一走公共函数（未命中列保留原名键，落库时自然忽略）
        headers = _import_headers(headers_raw, None)
        skip_first = False

    if "name" not in headers:
        raise HTTPException(422, "需包含企业名称列（映射到 name）")

    inserted = merged = skipped = 0
    batch_id = uuid.uuid4().hex[:12]
    for line in lines[1:]:
        cells = [c.strip() for c in line.split(delim)]
        row = dict(zip(headers, cells))
        name = (row.get("name") or "").strip()
        if not name:
            skipped += 1
            continue
        ent, is_new = resolve_enterprise(session, name, source="csv")
        if is_new:
            inserted += 1
        else:
            merged += 1
        # SPEC-IMP 1.2：行→字段落库走公共函数（与上传路径共用一份逻辑）
        apply_enterprise_row_fields(session, ent, row, batch_id)
    session.commit()
    try:
        from .. import search as meili
        from ..db import SessionLocal

        with SessionLocal() as s2:
            meili.sync_all(s2)
    except Exception:  # noqa: BLE001 — 检索投影失败不影响导入
        logger.warning("粘贴导入后 Meili 同步失败", exc_info=True)
    return {
        "inserted": inserted, "merged": merged, "skipped": skipped,
        "batch_id": batch_id, "detected_delimiter": "tab" if delim == "	" else "comma",
    }


@router.post("/import/enterprises/preview")
async def preview_import_enterprises(
    file: UploadFile,
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    """企业 CSV 上传预演（SPEC-IMP 1.1）：选文件→映射确认前的探测接口。

    返回：分隔符探测 / 原始表头 / 同义词归一表头 / 映射建议（首列兜底 name、
    未命中列 skip）/ 按建议映射后的前 5 行预览 / 总数据行数 / 库内重名预演
    （dup_preview：单查询 Enterprise.name.in_(names) 计数，不做逐行 resolve；
    空名行不计入 existing，由正式导入按 skipped 落明细）。
    """
    raw = await file.read()

    # SPEC-XLSX：.xlsx 直读分支——走 _parse_upload 统一解析，跳过分隔符探测
    # （xlsx 无分隔符概念，detected_delimiter 占位返回 ","，前端向导不消费该字段）
    if (file.filename or "").endswith(".xlsx"):
        headers_x, rows_x = _parse_upload(raw, file.filename or "")
        if not headers_x:
            raise HTTPException(422, "Excel 需包含表头行")
        # 行 dict → 按表头列序还原 cells（后续同义词/映射建议逻辑与 CSV 完全共用）
        parsed_rows = [headers_x] + [[(r.get(h) or "") for h in headers_x] for r in rows_x]
        delim = ","
    else:
        text_content = _decode_csv_bytes(raw)
        lines = [ln for ln in text_content.splitlines() if ln.strip()]
        if not lines:
            raise HTTPException(422, "文件内容为空")

        # 分隔符探测：与粘贴路径同规则（SPEC-IMP 1.1）——首行 Tab 数 ≥ 逗号数取 Tab
        header_line = lines[0]
        delim = "\t" if header_line.count("\t") >= header_line.count(",") else ","

        # csv.reader 按探测分隔符解析（引号包裹的单元格正确展开；空行跳过）
        parsed_rows = [
            [c.strip() for c in r]
            for r in csv.reader(io.StringIO(text_content), delimiter=delim)
            if any(c.strip() for c in r)
        ]
    if not parsed_rows:
        raise HTTPException(422, "CSV 需包含表头行")
    headers_raw = parsed_rows[0]
    data_rows = parsed_rows[1:]

    # 同义词归一 + 映射建议：命中→字段；首列未命中→兜底 name；其余未命中→skip
    headers_syn = [
        _HEADER_SYNONYMS.get(h.lower(), _HEADER_SYNONYMS.get(h, "")) for h in headers_raw
    ]
    mapping_suggestion: list[dict] = []
    for i, field in enumerate(headers_syn):
        if field:
            mapping_suggestion.append({"col": i, "field": field})
        elif i == 0:
            mapping_suggestion.append({"col": 0, "field": "name"})  # 首列兜底 name
        else:
            mapping_suggestion.append({"col": i, "field": "skip"})
    col_field = {m["col"]: m["field"] for m in mapping_suggestion}

    def _map_row(cells: list[str]) -> dict:
        """按建议映射取一行（skip 列丢弃；越界列忽略）。"""
        return {
            field: cells[i]
            for i, field in col_field.items()
            if field != "skip" and i < len(cells)
        }

    # 首行预览（≤5 行）+ 全量行名收集（dup 预演用）
    preview_rows: list[dict] = []
    row_names: list[str] = []
    for cells in data_rows:
        mapped = _map_row(cells)
        row_names.append((mapped.get("name") or "").strip())
        if len(preview_rows) < 5:
            preview_rows.append(mapped)

    # dup_preview（SPEC-IMP 1.1）：一次 in_ 查询拿库内同名集合，再按行计数
    nonempty_names = [n for n in row_names if n]
    db_names: set[str] = set()
    if nonempty_names:
        db_names = {
            r[0]
            for r in session.query(Enterprise.name).filter(Enterprise.name.in_(nonempty_names)).all()
        }
    existing = sum(1 for n in row_names if n and n in db_names)
    new_count = len(row_names) - existing  # 空名行计入 new 侧（正式导入按 skipped 处理）

    return {
        "detected_delimiter": delim,
        "headers_raw": headers_raw,
        "headers_synonymized": headers_syn,
        "mapping_suggestion": mapping_suggestion,
        "preview_rows": preview_rows,
        "total_rows": len(data_rows),
        "dup_preview": {"existing": existing, "new": new_count},
    }


@router.post("/import/enterprises")
async def import_enterprises(
    file: UploadFile,
    mapping: str | None = Form(None),  # SPEC-IMP 1.2：JSON 数组字符串，下标=列序
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    raw = await file.read()

    # SPEC-IMP 1.2：显式 mapping 优先（skip 列跳过）；缺省走 _HEADER_SYNONYMS 同义词路径
    mapping_list = _parse_mapping_form(mapping)

    # SPEC-XLSX：统一上传解析（.xlsx→openpyxl 只读；.csv/其余→既有 _decode_csv_bytes
    # + DictReader 语义包一层）。表头归一走公共函数
    # （SPEC-M 同义词语义 + SPEC-IMP 1.2 显式 mapping，共用一份行→字段逻辑）
    raw_fields, raw_rows = _parse_upload(raw, file.filename or "")
    norm_fields = _import_headers(raw_fields, mapping_list)
    inserted, merged, skipped = 0, 0, 0
    skipped_details: list[dict] = []  # SPEC-IMP 1.2：无名称行明细（≤10 条）
    batch_id = uuid.uuid4().hex[:12]
    for row_no, raw_row in enumerate(raw_rows, start=2):  # 行号从 2 起算（表头=第 1 行）
        # 位置重建：norm_fields[i] 对应 raw_fields[i]（表头唯一时与 DictReader
        # dict(zip(fieldnames, row)) 行为逐字节一致，SPEC-XLSX）
        row = {n: raw_row.get(h) for n, h in zip(norm_fields, raw_fields)}
        name = (row.get("name") or "").strip()
        if not name:
            skipped += 1
            if len(skipped_details) < 10:
                skipped_details.append({"row": row_no, "reason": "缺少企业名称"})
            continue
        ent, is_new = resolve_enterprise(session, name, source="csv")
        if is_new:
            inserted += 1
        else:
            merged += 1
        # SPEC-IMP 1.2：行→字段落库走公共函数（与粘贴路径共用）
        apply_enterprise_row_fields(session, ent, row, batch_id)
    session.commit()
    # 检索投影：导入后全量同步（导入是批量操作，逐条增量反而慢）
    try:
        from .. import search as meili
        from ..db import SessionLocal

        with SessionLocal() as s2:
            meili.sync_all(s2)
    except Exception:  # noqa: BLE001 — 检索层失败不影响导入结果（SQLite 为真源）
        logger.warning("导入后 Meili 同步失败，检索投影滞后", exc_info=True)
    return {
        "inserted": inserted, "merged": merged, "skipped": skipped,
        "skipped_details": skipped_details, "batch_id": batch_id,
    }


@router.post("/import/tenders")
async def import_tenders(
    file: UploadFile,
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    raw = await file.read()
    text_content = _decode_csv_bytes(raw)
    reader = csv.DictReader(io.StringIO(text_content))
    strong = None
    from ..vertical import device_strong_terms, device_weak_terms

    strong = device_strong_terms()
    weak = device_weak_terms()
    weights = signal_weights()
    title_pts = float(weights.get("tender_title_hit", 70))
    body_pts = float(weights.get("tender_body_hit", 50))
    weak_discount = float(weights.get("weak_discount", 0.5))

    def match_signal(title: str, content: str) -> dict | None:
        t_hits = [t for t in strong if t in title]
        b_hits = [t for t in strong if t in content and t not in t_hits]
        w_hits = [t for t in weak if t in title and t not in t_hits and t not in b_hits]
        if t_hits:
            return {"terms": t_hits, "hit_part": "title", "score": title_pts}
        if w_hits:
            return {"terms": w_hits, "hit_part": "title", "score": title_pts * weak_discount}
        if b_hits:
            return {"terms": b_hits, "hit_part": "body", "score": body_pts}
        return None

    signals_created = 0
    enterprises_created = 0
    affected: set[int] = set()
    for row in reader:
        title = (row.get("title") or "").strip()
        content = (row.get("content") or "").strip()
        pub_date = (row.get("pub_date") or "").strip() or None
        url = (row.get("url") or "").strip()
        if not title:
            continue
        m = match_signal(title, content)
        if m is None:
            continue
        ent_name = (row.get("enterprise_name") or "").strip()
        if not ent_name:
            ent_name = extract_enterprise_name(title, content)
        if not ent_name:
            # 抽不到归属方：不再整条丢弃，落"待归属"企业占位（信号保留，
            # 后续可人工归属或凭企业名补录——修复最大数据漏损口）
            ent_name = f"待归属·{title[:18].strip()}"
        ent, is_new = resolve_enterprise(session, ent_name, source="tender")
        if is_new:
            enterprises_created += 1
        excerpt = content[:200] if content else title
        # 证据句（学 AI_Find_Customer evidence.claim）：标题本身即最可信的"要求买什么"
        evidence_claim = (content or title).strip()[:80]
        sig = Signal(
            enterprise_id=ent.id,
            type="tender",
            title=title,
            url=url,
            pub_date=pub_date,
            matched_terms="[]",
            hit_part=m["hit_part"],
            raw_excerpt=excerpt,
            evidence_claim=evidence_claim,
            base_score=m["score"],
        )
        import json as _json

        sig.matched_terms = _json.dumps(m["terms"], ensure_ascii=False)
        existing = (
            session.query(Signal)
            .filter(
                Signal.enterprise_id == ent.id,
                Signal.url == url,
                Signal.title == title,
            )
            .first()
        )
        if existing is None:
            session.add(sig)
            signals_created += 1
            affected.add(ent.id)
        session.flush()
    for eid in affected:
        recompute_intent(session, eid)
        ent = session.get(Enterprise, eid)
        if ent is not None:
            apply_icp(ent)
    session.commit()
    return {
        "signals_created": signals_created,
        "enterprises_created": enterprises_created,
        "matched_terms_summary": f"{signals_created} 条信号命中设备词",
    }


# ---------------------------------------------------------------------------
# SPEC-CK：风鸟配额流水线——缺数购物清单 + 官方导出回灌（合规：人工查询+官方导出）
# ---------------------------------------------------------------------------
@router.get("/tasks/gongshang-lookup-export")
def gongshang_lookup_export(
    limit: int = 500,
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    """缺数购物清单 CSV（SPEC-CK CK1）：值得去风鸟逐家查询的企业。

    缺 uscc/法人/注册资本/成立日期或无可拨电话者入选，意图分降序——
    免费配额花在刀刃上。官方导出/人工查询=合规数据源（红线不爬）。
    """
    import csv as _csv
    import io as _io

    from fastapi.responses import StreamingResponse

    from ..models import Contact as _CT
    from ..phones import load_phones as _lp

    limit = max(1, min(limit, 2000))
    ents = (
        session.query(Enterprise)
        .filter(Enterprise.deleted_at.is_(None))
        .order_by(Enterprise.intent_score.desc(), Enterprise.id.desc())
        .limit(limit * 3)
        .all()
    )
    dialable_ids = {
        eid
        for (eid, pj) in session.query(_CT.enterprise_id, _CT.phones_json).filter(
            _CT.phones_json != "[]"
        ).all()
        if _lp(pj)
    }
    rows_out = []
    for e in ents:
        # 2026-11-08 用户拍板：学校非目标客户——配额不花在非客户身上
        if e.industry_key == "research_edu":
            continue
        missing = []
        if not e.uscc:
            missing.append("统一社会信用代码")
        if not e.legal_rep:
            missing.append("法定代表人")
        if not e.registered_capital_wan:
            missing.append("注册资本")
        if not e.established_at:
            missing.append("成立日期")
        if e.id not in dialable_ids:
            missing.append("联系电话")
        if not missing:
            continue
        rows_out.append({
            "name": e.name,
            "eid": e.id,
            "region": " ".join(x for x in (e.province, e.city) if x),
            "industry": e.industry_name or "",
            "intent": e.intent_score,
            "missing": "、".join(missing),
        })
        if len(rows_out) >= limit:
            break
    buf = _io.StringIO()
    w = _csv.writer(buf)
    w.writerow(["企业名称", "地区", "行业", "意图分", "缺失项", "风鸟直搜链接"])
    for r in sorted(rows_out, key=lambda x: -(x["intent"] or 0)):
        # SPEC-CK 补强：每行带风鸟搜索直链——清单里点一下就到查询页，零复制粘贴
        url = "https://www.riskbird.com/ent/" + quote(r["name"]) + "?eid=" + str(r["eid"])
        w.writerow([r["name"], r["region"], r["industry"], r["intent"], r["missing"], url])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=gongshang_lookup_list.csv"},
    )


_GONGSHANG_MAP = {
    "name": "name", "企业名称": "name", "公司名称": "name",
    "统一社会信用代码": "uscc", "纳税人识别号": "uscc", "信用代码": "uscc",
    "法定代表人": "legal_rep", "法人": "legal_rep", "法人代表": "legal_rep",
    "注册资本": "registered_capital_wan", "注册资本(万元)": "registered_capital_wan",
    "成立日期": "established_at", "成立时间": "established_at",
    "企业电话": "phone", "联系电话": "phone", "电话": "phone",
    "地址": "address", "企业地址": "address",
    "经营范围": "business_scope",
}

_CAP_RE = re.compile(r"([\d.]+)\s*万")


def _parse_capital(raw: str) -> float | None:
    """风鸟注册资本形态清洗：'1000万人民币'/'1,200万元' → 1000.0。"""
    if not raw:
        return None
    m = _CAP_RE.search(raw.replace(",", ""))
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


class GongshangRowIn(BaseModel):
    rows: list[dict]


@router.post("/import/gongshang-csv")
def import_gongshang_csv(
    body: GongshangRowIn,
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    """风鸟官方导出回灌（SPEC-CK CK2）：逐行 resolve，仅补空不覆盖。

    电话建 Contact（source=riskbird，evidence=风鸟工商档案公开电话），
    同企业已有同号码跳过；未匹配列名忽略。
    """
    from ..models import Contact as _CT
    from ..normalize import resolve_enterprise
    from ..phones import dump_phones as _dp
    from ..phones import extract_phones as _ep

    filled = 0
    phones_added = 0
    matched = 0
    skipped = 0
    for raw in body.rows[:1000]:
        mapped: dict[str, str] = {}
        for k, v in raw.items():
            col = _GONGSHANG_MAP.get(str(k).strip())
            if col and str(v or "").strip():
                mapped[col] = str(v).strip()
        name = mapped.get("name") or ""
        if len(name) < 4:
            skipped += 1
            continue
        cap = _parse_capital(mapped.get("registered_capital_wan", ""))
        ent, is_new = resolve_enterprise(
            session, name, source="csv",
            uscc=mapped.get("uscc"),
            est_date=mapped.get("established_at"),
            legal_rep=mapped.get("legal_rep"),
        )
        matched += 1
        changed = False
        if cap is not None and not ent.registered_capital_wan:
            ent.registered_capital_wan = cap
            changed = True
        if mapped.get("address") and not ent.address:
            ent.address = mapped["address"]
            changed = True
        if mapped.get("business_scope") and not ent.business_scope:
            ent.business_scope = mapped["business_scope"]
            changed = True
        if changed or is_new:
            filled += 1
        phone_raw = mapped.get("phone")
        if phone_raw:
            phones = _ep(phone_raw)
            for ph in phones:
                dup = (
                    session.query(_CT)
                    .filter(_CT.enterprise_id == ent.id, _CT.phones_json.contains(ph))
                    .first()
                )
                if dup is not None:
                    continue
                session.add(_CT(
                    enterprise_id=ent.id, name="工商档案电话", role="map_phone",
                    phones_json=_dp([ph]), source="riskbird",
                    evidence_claim="风鸟工商档案公开电话",
                ))
                phones_added += 1
    session.commit()
    return {"matched": matched, "filled": filled,
            "phones_added": phones_added, "skipped": skipped}


# ---------------------------------------------------------------------------
# SPEC-GSH：工商补数批量回填——风鸟对照表 xlsx/csv 按名称/信用代码匹配，只填空不覆盖
# ---------------------------------------------------------------------------
# 企业工商五要素（法人/成立日期/注册资本/经营范围/统一信用代码）真库覆盖仅 0-6%
# （POI/公告源天然没有）。用户持风鸟对照表 Excel，此前只能逐家书签手工搬运——
# 本端点批量化：拖表 → 匹配（uscc 锚点优先，企业名归一同款）→ 只填空不覆盖。
#: 回填目标字段（工商五要素；name/uscc 兼作匹配键）
_GS_BACKFILL_FIELDS = (
    "legal_rep", "established_at", "registered_capital_wan", "business_scope", "uscc",
)
#: 向导预览行允许出现的键（匹配键 + 五要素）
_GS_BACKFILL_KEYS = ("name",) + _GS_BACKFILL_FIELDS

#: 工商对照表表头同义词（SPEC-GSH）：比 _HEADER_SYNONYMS 多 uscc 匹配键。
#: 独立词表而非扩充 _HEADER_SYNONYMS——既有 /import/enterprises 的 preview
#: 建议与落库行为保持逐字节不变（新增 uscc 同义词对旧流是意外变更）。
_GS_BACKFILL_SYNONYMS: dict[str, str] = {
    # 匹配键：企业名
    "name": "name", "企业名称": "name", "公司名称": "name", "企业": "name",
    "名称": "name", "公司": "name", "客户名称": "name",
    # 匹配键兼回填字段：统一社会信用代码
    "uscc": "uscc", "统一社会信用代码": "uscc", "信用代码": "uscc", "纳税人识别号": "uscc",
    # 工商五要素
    "legal_rep": "legal_rep", "法定代表人": "legal_rep", "法人": "legal_rep",
    "法人代表": "legal_rep",
    "established_at": "established_at", "成立日期": "established_at",
    "成立时间": "established_at",
    "registered_capital_wan": "registered_capital_wan", "注册资本": "registered_capital_wan",
    "注册资本(万元)": "registered_capital_wan", "注册资本(万)": "registered_capital_wan",
    "business_scope": "business_scope", "经营范围": "business_scope",
}

#: 统一社会信用代码强格式（18 位，字符集不含 I/O/S/V/Z）——与 api/intel.py
#: gongshang-paste 的 uscc 校验同一模式。区别于 gongshang-paste 的整块 text
#: 粘连解析：这里是结构化列，值干净但仍用 search 容错（风鸟表格复制常带
#: "复制"按钮尾巴，如 "914403...复制"）。
_USCC_RE = re.compile(r"[0-9A-HJ-NPQRTUWXY]{18}")


def _match_enterprise_readonly(
    session: Session, name: str, canon_map: dict[str, int]
) -> Enterprise | None:
    """SPEC-GSH：resolve_enterprise 同款归一匹配的**只读版**（回填只匹配不建档）。

    匹配序与 normalize._resolve 对齐：精确名 → 别名表（原全名/标准化主体名）→
    rapidfuzz token_sort_ratio ≥FUZZY_THRESHOLD 自动合并档。80-89 复核区间与
    未命中一样落 skipped——回填绝不新建企业、不写 review_queue、不加别名
    （dry_run 零写入红线的延伸：正式跑也只做 UPDATE 已有企业这一种写入）。
    """
    raw = "".join((name or "").split())  # 归一同款：剥全部空白（含全角空格）
    if not raw:
        return None
    ent = session.query(Enterprise).filter(Enterprise.name == raw).first()
    if ent is not None:
        return ent
    canon = canonical_name(raw)
    for alias_val in dict.fromkeys((raw, canon)):  # 去重且保序（同 _resolve）
        if not alias_val:
            continue
        row = (
            session.query(EnterpriseAlias)
            .filter(EnterpriseAlias.alias == alias_val)
            .first()
        )
        if row is not None:
            target = session.get(Enterprise, row.enterprise_id)
            if target is not None:
                return target
    if canon and canon_map:
        from rapidfuzz import fuzz
        from rapidfuzz import process as _process

        hit = _process.extractOne(
            canon, list(canon_map.keys()), scorer=fuzz.token_sort_ratio,
            score_cutoff=REVIEW_THRESHOLD,
        )
        if hit is not None:
            exist_canon, score, _ = hit
            if score >= FUZZY_THRESHOLD:  # ≥90 自动合并档；80-89 复核档不回填
                return session.get(Enterprise, canon_map[exist_canon])
    return None


def _parse_backfill_capital(cap_raw: str) -> float | None:
    """SPEC-GSH 注册资本浮点容错：纯数字直读（"500"/"1,200"），带单位形态走
    _parse_capital 的"X万"提取（"1000万人民币"/"1200万元"——风鸟对照表常见）。
    解析不出返回 None（该格静默不填，与既有导入的注册资本容错语义一致）。"""
    if not cap_raw:
        return None
    try:
        cap = float(cap_raw.replace(",", ""))
    except (TypeError, ValueError):
        cap = _parse_capital(cap_raw)
    if cap is None or not math.isfinite(cap):  # nan/inf 拒绝（防毒化 REAL 列）
        return None
    return cap


@router.post("/import/gongshang-backfill")
async def gongshang_backfill(
    file: UploadFile,
    dry_run: int = 0,  # query 参数：1=预演（零写入，只返回计数）
    mapping: str | None = Form(None),  # JSON 数组字符串，下标=列序（同 SPEC-IMP 1.2）
    session: Session = Depends(get_session),
    user: User = Depends(admin_only),
):
    """工商补数批量回填（SPEC-GSH）：风鸟对照表 → 按名称/信用代码匹配 → 只填空不覆盖。

    行匹配：行内 uscc 列提取到 18 位有效代码 → Enterprise.uscc 精确锚点优先；
    否则企业名走 _match_enterprise_readonly（resolve_enterprise 同款归一，
    只读不建档）；均不中 → skipped 计数并落明细（≤10 条）。

    回填红线：**只填空不覆盖**——任何已有值一律保原样（kept_existing 诚实计数）。

    dry_run=1：预演零写入——不 commit、不 apply_icp、不同步 Meili
    （get_session 收尾仅 close，未提交的内存变更随会话丢弃）。
    返回同时携带 headers_raw/mapping_suggestion/preview_rows/total_rows，
    供前端向导复用 SPEC-IMP 的"预演→映射确认→正式"两步结构。
    """
    raw = await file.read()
    raw_fields, raw_rows = _parse_upload(raw, file.filename or "")
    if not raw_fields:
        raise HTTPException(422, "文件需包含表头行")
    mapping_list = _parse_mapping_form(mapping)
    norm_fields = _import_headers(raw_fields, mapping_list, synonyms=_GS_BACKFILL_SYNONYMS)

    # 无显式 mapping 且没有任何列命中 name → 首列兜底 name（对照表首列通常是
    # 企业名；若首列是序号，匹配不上自然落 skipped，向导里可手动改映射）
    if mapping_list is None and "name" not in norm_fields:
        norm_fields[0] = "name"

    rows = [
        {n: raw_row.get(h) for n, h in zip(norm_fields, raw_fields)}
        for raw_row in raw_rows
    ]

    # 标准化主体名 → id 一次性载入（批量 fuzz 用，同 normalize._canon_cache 思路；
    # 软删除全局过滤自动排除已删企业）
    canon_map: dict[str, int] = {}
    for eid, nm in session.query(Enterprise.id, Enterprise.name).all():
        c = canonical_name(nm)
        if c:
            canon_map[c] = eid

    matched = skipped = kept_existing = 0
    filled: dict[str, int] = {f: 0 for f in _GS_BACKFILL_FIELDS}
    skipped_details: list[dict] = []
    matched_ents: dict[int, Enterprise] = {}  # id 去重：apply_icp 每家只跑一次

    for row_no, row in enumerate(rows, start=2):  # 行号从 2 起算（表头=第 1 行）
        uscc_cell = (row.get("uscc") or "").strip()
        name_cell = (row.get("name") or "").strip()
        uscc_m = _USCC_RE.search(uscc_cell) if uscc_cell else None
        uscc_val = uscc_m.group(0) if uscc_m else None
        ent: Enterprise | None = None
        matched_by_uscc = False
        if uscc_val:
            ent = session.query(Enterprise).filter(Enterprise.uscc == uscc_val).first()
            matched_by_uscc = ent is not None
        if ent is None and name_cell:
            ent = _match_enterprise_readonly(session, name_cell, canon_map)
        if ent is None:
            skipped += 1
            if len(skipped_details) < 10:
                if not uscc_cell and not name_cell:
                    reason = "缺少企业名称与信用代码"
                elif uscc_cell and not uscc_val:
                    reason = "信用代码非 18 位有效格式且企业名未命中"
                else:
                    reason = "未匹配到库内企业（只回填不建档）"
                skipped_details.append({"row": row_no, "reason": reason})
            continue
        matched += 1
        if not dry_run:
            matched_ents[ent.id] = ent

        # ---- 只填空不覆盖（SPEC-GSH 红线）：已有值保原样并诚实计入 kept_existing ----
        # dry_run 只计数不 setattr（预演零写入）。
        new_legal = (row.get("legal_rep") or "").strip()
        if new_legal:
            if ent.legal_rep:
                kept_existing += 1
            elif not dry_run:
                ent.legal_rep = new_legal
                ent.mark_prov("legal_rep", "对照表批量回填")
                filled["legal_rep"] += 1
            else:
                filled["legal_rep"] += 1

        new_scope = (row.get("business_scope") or "").strip()
        if new_scope:
            if ent.business_scope:
                kept_existing += 1
            elif not dry_run:
                ent.business_scope = new_scope
                ent.mark_prov("business_scope", "对照表批量回填")
                filled["business_scope"] += 1
            else:
                filled["business_scope"] += 1

        new_est = (row.get("established_at") or "").strip()
        if new_est:
            if ent.established_at:
                kept_existing += 1
            elif not dry_run:
                ent.established_at = str(new_est)[:10]  # YYYY-MM-DD 前缀容错（照 imports 既有）
                ent.mark_prov("established_at", "对照表批量回填")
                filled["established_at"] += 1
            else:
                filled["established_at"] += 1

        cap = _parse_backfill_capital((row.get("registered_capital_wan") or "").strip())
        if cap is not None:
            if ent.registered_capital_wan is not None:
                kept_existing += 1
            elif not dry_run:
                ent.registered_capital_wan = cap
                ent.mark_prov("registered_capital_wan", "对照表批量回填")
                filled["registered_capital_wan"] += 1
            else:
                filled["registered_capital_wan"] += 1

        # uscc 回填：以 uscc 命中（matched_by_uscc）时值必然相同，不计不写；
        # 名称匹配携带有效代码 → 空则回填（提升后续归一锚点），已有则保原样
        if uscc_val and not matched_by_uscc:
            if ent.uscc:
                kept_existing += 1
            elif not dry_run:
                ent.uscc = uscc_val
                ent.mark_prov("uscc", "对照表批量回填")
                filled["uscc"] += 1
            else:
                filled["uscc"] += 1

    if not dry_run:
        # ICP 重算（注册资本/经营范围影响 ICP 分——matched 企业全量重打分）
        for ent in matched_ents.values():
            apply_icp(ent)
        session.commit()
        # 检索投影：批量回填后全量同步（复用 imports 既有同步块模式）
        try:
            from .. import search as meili
            from ..db import SessionLocal

            with SessionLocal() as s2:
                meili.sync_all(s2)
        except Exception:  # noqa: BLE001 — 检索层失败不影响回填结果（SQLite 为真源）
            logger.warning("工商补数后 Meili 同步失败，检索投影滞后", exc_info=True)
    # dry_run：不 commit / 不 apply_icp / 不同步 Meili —— 零写入预演

    return {
        "dry_run": bool(dry_run),
        "matched": matched,
        "filled": filled,
        "kept_existing": kept_existing,
        "skipped": skipped,
        "skipped_details": skipped_details,
        "headers_raw": raw_fields,
        "mapping_suggestion": [{"col": i, "field": nf} for i, nf in enumerate(norm_fields)],
        "preview_rows": [
            {n: v for n, v in r.items() if n in _GS_BACKFILL_KEYS and (v or "").strip()}
            for r in rows[:5]
        ],
        "total_rows": len(rows),
    }
