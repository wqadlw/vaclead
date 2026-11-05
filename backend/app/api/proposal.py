"""节能账计算引擎 + 客户提案（SPEC-AT，方案武器库层）。

第一性：老板买"节能账"（电费差额×年）、工程师买"选型不背锅"（计算书）。
全部数字带公式可解释——面对质询每个数字站得住。节省率=可调假设（页面
标注"按实测案例区间估算"），不做承诺。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_session
from ..models import Enterprise, Product, User
from ..recommend import _product_match

router = APIRouter(tags=["proposal"])


def roi_calc(
    power_kw: float,
    hours_per_day: float,
    load_factor: float,
    price_per_kwh: float,
    saving_pct: float,
    equipment_price_wan: float | None = None,
) -> dict:
    """节能账：全部输出带公式说明（销售可对工程师逐项解释）。"""
    if min(power_kw, hours_per_day, price_per_kwh) <= 0:
        raise ValueError("功率/运行小时/电价必须为正")
    if not (0 < load_factor <= 1):
        raise ValueError("负载率应在 (0, 1]")
    if not (0 < saving_pct < 60):
        raise ValueError("节省率应在 (0, 60)% 区间（实测案例 8.7%-30%）")

    baseline_year = power_kw * hours_per_day * load_factor * 365 * price_per_kwh
    annual_save = baseline_year * saving_pct / 100
    five_year = annual_save * 5
    payback = None
    if equipment_price_wan and annual_save > 0:
        # 万元 → 元：设备价 ×10000 / 年省
        payback = round(equipment_price_wan * 10000 / annual_save, 1)
    return {
        "baseline_year_kwh": round(power_kw * hours_per_day * load_factor * 365, 0),
        "baseline_year_cost": round(baseline_year, 0),
        "annual_save": round(annual_save, 0),
        "five_year_save": round(five_year, 0),
        "payback_months": payback,
        "formula": {
            "baseline": "功率kW × 运行小时/天 × 负载率 × 365 天 × 电价元/度",
            "annual_save": "基线年电费 × 节省率（实测案例区间 8.7%-30%，按负载波动性选取）",
        },
    }


@router.post("/proposal/roi")
def proposal_roi(
    body: dict,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    try:
        result = roi_calc(
            float(body.get("power_kw") or 0),
            float(body.get("hours_per_day") or 0),
            float(body.get("load_factor") or 0.8),
            float(body.get("price_per_kwh") or 0),
            float(body.get("saving_pct") or 25),
            float(body["equipment_price_wan"]) if body.get("equipment_price_wan") else None,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return {"ok": True, "result": result}


@router.get("/proposal/print")
def proposal_print(
    eid: int,
    power_kw: float = 37,
    hours_per_day: float = 16,
    load_factor: float = 0.8,
    price_per_kwh: float = 0.7,
    saving_pct: float = 25,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """客户提案（打印友好 HTML）：节能账 + 选型推荐 + 服务承诺。"""
    e = session.get(Enterprise, eid)
    if e is None or e.deleted_at:
        raise HTTPException(404, "企业不存在")
    try:
        calc = roi_calc(power_kw, hours_per_day, load_factor, price_per_kwh, saving_pct)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    match = _product_match(session, e.industry_key) or "按工况选型（联系工程师获取选型计算书）"
    product = session.execute(
        select(Product).where(Product.name == match, Product.active == 1)
    ).scalars().first()

    # SPEC-AV AV2：同行业公开采购案例（"同行都在用"——工程师的不背锅证据）
    # SPEC-AZ AZ2：私有成交案例优先（昊志真实交付 > 行业公开记录）
    from datetime import datetime, timedelta, timezone

    from ..models import CaseStudy, Signal

    private_cases = session.execute(
        select(CaseStudy.title, CaseStudy.content, CaseStudy.year)
        .where(
            or_(
                CaseStudy.enterprise_id == eid,
                CaseStudy.industry_key == (e.industry_key or "__none__"),
            )
        )
        .order_by(CaseStudy.id.desc())
        .limit(3)
    ).all()

    case_rows = []
    if e.industry_key:
        cutoff3y = (
            datetime.now(timezone.utc) - timedelta(days=1095)
        ).strftime("%Y-%m-%dT%H:%M:%S")
        case_rows = session.execute(
            select(Signal.title, Signal.pub_date, Signal.url, Enterprise.name)
            .join(Enterprise, Signal.enterprise_id == Enterprise.id)
            .where(
                Enterprise.industry_key == e.industry_key,
                Enterprise.deleted_at.is_(None),
                Signal.type.in_(["tender_win", "tender_buy", "tender"]),
                Signal.created_at >= cutoff3y,
            )
            .order_by(Signal.id.desc())
            .limit(5)
        ).all()

    rows = [
        ("现状年用电量", f"{calc['baseline_year_kwh']:,.0f} 度", "功率 × 小时 × 负载率 × 365"),
        ("现状年电费", f"{calc['baseline_year_cost']:,.0f} 元", "用电量 × 电价"),
        ("预计年节省", f"{calc['annual_save']:,.0f} 元", f"基线 × 节省率 {saving_pct}%"),
        ("5 年累计节省", f"{calc['five_year_save']:,.0f} 元", "年节省 × 5"),
    ]
    if calc["payback_months"]:
        rows.append(("投资回收期", f"约 {calc['payback_months']} 个月", "设备价 ÷ 年节省"))
    trs = "".join(f"<tr><td>{a}</td><td class='num'>{b}</td><td class='why'>{c}</td></tr>" for a, b, c in rows)
    spec_html = (
        f"<p><b>{product.name}</b>（{product.model or '—'}）—— {product.spec or ''}</p>"
        if product else "<p>选型请联系工程师获取计算书。</p>"
    )
    # 两层案例：私有交付优先（真实成交 > 行业公开记录），公开案例兜底
    public_lis = "".join(
        f"<li>{t[:56]} — {n}（{d or ''}）"
        + (f" <a href='{u}'>原文</a>" if u else "")
        + "</li>"
        for t, d, u, n in case_rows
    )
    if private_cases:
        priv_lis = "".join(
            f"<li><b>{t}</b>（{y or ''}）—— {c[:100]}</li>"
            for t, c, y in private_cases
        )
        cases_html = (
            "<h2>三、昊志交付案例（真实成交）</h2>"
            f"<ol class='cases'>{priv_lis}</ol>"
        ) + (
            f"<h2>四、同行业公开采购案例（{e.industry_name or '同行业'}）</h2>"
            f"<ol class='cases'>{public_lis}</ol>"
            "<p class='note'>以上为法定公开公告摘引，原文可溯源。</p>"
            if case_rows
            else ""
        )
        sec4, sec5 = "五、为什么选择昊志", "六、下一步"
    elif case_rows:
        cases_html = (
            f"<h2>三、同行业公开采购案例（{e.industry_name or '同行业'}）</h2>"
            f"<ol class='cases'>{public_lis}</ol>"
            "<p class='note'>以上为法定公开公告摘引，原文可溯源。</p>"
        )
        sec4, sec5 = "四、为什么选择昊志", "五、下一步"
    else:
        cases_html = ""
        sec4, sec5 = "三、为什么选择昊志", "四、下一步"
    html = f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>{e.name} 真空系统节能提案</title>
<style>
body{{font-family:-apple-system,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;
     color:#1f2328;max-width:760px;margin:32px auto;padding:0 20px;font-size:14px;line-height:1.8}}
h1{{font-size:20px;border-bottom:2px solid #1b6c80;padding-bottom:8px}}
h2{{font-size:15px;color:#1b6c80;margin-top:28px}}
table{{width:100%;border-collapse:collapse;margin-top:8px}}
td,th{{border:1px solid #d8dee2;padding:7px 10px;text-align:left}}
.num{{text-align:right;font-variant-numeric:tabular-nums;font-weight:600}}
.why{{color:#6b7175;font-size:12.5px}}
.note{{color:#8a8d8f;font-size:12px}}
ol.cases{{padding-left:20px}}ol.cases li{{margin-bottom:4px;font-size:13px}}
@media print{{.noprint{{display:none}}}}
</style></head><body>
<h1>{e.name} · 真空系统节能改造提案</h1>
<p>由 {user.display_name or user.username}（昊志机械 · 昊青气体装备官方销售商）基于公开实测案例区间估算</p>
<h2>一、节能账（按贵司现状参数）</h2>
<table><tr><th>项目</th><th>数值</th><th>计算口径</th></tr>{trs}</table>
<p class="note">节省率按同工况实测案例区间估算（负载波动场景优势显著）；实际以现场工况勘测为准。</p>
<h2>二、推荐方案</h2>
{spec_html}
{cases_html}
<h2>{sec4}、为什么选择昊志</h2>
<ul>
<li><b>官方销售商</b>：昊青气体装备直接授权，原厂备件与质保可追溯</li>
<li><b>系统搭建能力</b>：按贵司工艺出具选型计算书——选型与工况不匹配是行业最常见痛点，我们不让客户背这个锅</li>
<li><b>区域响应</b>：本地服务团队，维保响应快于国际品牌（国产体系平均快 30%）</li>
</ul>
<h2>{sec5}、下一步</h2>
<p>安排现场工况勘测（免费）→ 出具正式选型计算书与报价 → 样机试机验证节能数据。</p>
<p class="note noprint">打印：浏览器 Ctrl+P / 另存为 PDF。参数可在回链调整重算。</p>
</body></html>"""
    return HTMLResponse(html)
