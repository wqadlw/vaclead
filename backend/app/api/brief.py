"""客户作战简报（SPEC-BRF，打单前一页纸）。

打大单/久攻不下客户时 10 分钟读完的一页纸：这家公司是干嘛的/在买什么/
为什么现在打/打给谁/谈资/下一步。纯本地八源数据组装（零新依赖、零出站
请求），GLM Key 到位后在打印页预留注释处注入 AI 摘要段。

- GET /enterprises/{id}/brief        大聚合 JSON（SPEC-BRF 2.1，单请求拿全）
- GET /enterprises/{id}/brief/print  A4 一页打印 HTML（2.2，学 proposal.py
  proposal_print 成熟先例：完整 HTML + 内联 CSS + @media print + .noprint）

鉴权：登录即可；详情页 window.open 新窗口无 Authorization 头时的
?token= 查询参数回退由 auth.current_user 统一承担（SPEC-AI AI1 先例），
端点不重复实现。
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_session
from ..jobs import extract_amount_wan
from ..models import (
    Contact,
    Enterprise,
    Followup,
    InstalledUnit,
    Lead,
    Signal,
    User,
)
from ..phones import load_phones

router = APIRouter(tags=["brief"])

# 与 jobs.enrich_stats / competitors.py 同源口径：公告文本回抽，可溯源不发明
_BUYER_RE = re.compile(r"采购人[：:]\s*([^\s，,。；;]+)")

# 信号类型中文名（why_now.signals.type_cn；库内枚举 tender/tender_buy/tender_win/proposal）
_TYPE_CN = {
    "tender": "招标公告",
    "tender_buy": "采购公告",
    "tender_win": "中标公告",
    "proposal": "拟建/备案",
}

# 联系人来源渠道中文名（who.contacts.channel；未收录的来源原样透出）
_CHANNEL_CN = {
    "ggzy": "公共资源交易平台",
    "ggzy_buyer": "公共资源交易平台",
    "ccgp": "中国政府采购网",
    "ccgp_buyer": "中国政府采购网",
    "manual": "手工录入",
    "riskbird": "工商档案",
}

# 决策链优先级（SPEC-BRF：可拨优先 + 决策链优先；可拨/未拨口径照抄 contacts.py 工作列表）
_DM_RANK = {"decision_maker": 0, "handler": 1}


def _intent_band(score: float) -> str:
    """SPEC-BRF：≥60 high / ≥30 mid / else low。"""
    if score >= 60:
        return "high"
    if score >= 30:
        return "mid"
    return "low"


def _sig_date(sig: Signal) -> str:
    """信号归属日：pub_date 优先，created_at 兜底（同 competitors._sig_date）。"""
    return (sig.pub_date or sig.created_at or "")[:10]


def _todo_hint(
    *,
    followups: list[dict],
    lead: Lead | None,
    installs: list[dict],
    dialable_count: int,
    band: str,
) -> str:
    """下一步建议（SPEC-BRF §3 确定性规则，非 AI）：规则顺序即优先级。"""
    if not followups:
        if dialable_count > 0:
            return "先拨第一个可拨联系人"
        return "先补录联系人电话，再开启首次触达"
    if lead is None or not (lead.next_action or "").strip():
        return "补记下一步动作"
    if installs:
        return "查耗材时钟是否到期"
    if band == "high":
        return "意图分处高位窗口期：按 next_time 尽快上门拜访"
    return "按 next_time 维持跟进节奏"


def _aggregate(session: Session, e: Enterprise) -> dict:
    """六块大聚合（SPEC-BRF 2.1）：全部走现有表，无新表无新列。"""
    eid = e.id
    band = _intent_band(e.intent_score or 0.0)

    # ---- buying 他们在买什么：中标史 + 采购体系（口径照 enterprises stat / enrich_stats）----
    win_rows = (
        session.query(Signal)
        .filter(Signal.enterprise_id == eid, Signal.type == "tender_win")
        .order_by(Signal.pub_date.desc(), Signal.id.desc())  # SPEC：wins 按 pub_date desc
        .all()
    )
    wins: list[dict] = []
    buyer_agg: dict[str, dict] = {}
    amount_total = 0.0
    for w in win_rows:
        # 金额回抽照 jobs.enrich_stats：证据句优先、摘录兜底
        amt = extract_amount_wan(w.evidence_claim or "") or extract_amount_wan(
            w.raw_excerpt or ""
        )
        mb = _BUYER_RE.search(w.evidence_claim or "")
        buyer = mb.group(1) if mb else ""
        if amt:
            amount_total += amt
        wins.append({
            "title": w.title,
            "amount_wan": amt,
            "pub_date": (w.pub_date or "")[:10],
            "buyer": buyer,
        })
        if buyer:
            agg = buyer_agg.setdefault(buyer, {"amount_total_wan": 0.0, "count": 0})
            agg["count"] += 1
            if amt:
                agg["amount_total_wan"] += amt
    top_buyers = sorted(
        ({"buyer": name, **v} for name, v in buyer_agg.items()),
        key=lambda x: (-x["amount_total_wan"], -x["count"], x["buyer"]),
    )[:5]
    buying = {
        "wins": wins[:8],
        "top_buyers": top_buyers,
        "amount_total_wan": round(amount_total, 2),
    }

    # ---- why_now 为什么现在打：近期信号 + 意图分档 + 拟建/备案（采购前兆）----
    sig_rows = (
        session.query(Signal)
        .filter(Signal.enterprise_id == eid)
        .order_by(Signal.pub_date.desc(), Signal.id.desc())
        .limit(8)
        .all()
    )
    proposal_rows = (
        session.query(Signal)
        .filter(Signal.enterprise_id == eid, Signal.type == "proposal")
        .order_by(Signal.pub_date.desc(), Signal.id.desc())
        .limit(3)
        .all()
    )
    why_now = {
        "signals": [
            {
                "type_cn": _TYPE_CN.get(s.type, s.type),
                "title": s.title,
                "pub_date": (s.pub_date or "")[:10],
                "base_score": s.base_score,
            }
            for s in sig_rows
        ],
        "intent_score": e.intent_score,
        "intent_band": band,
        "proposals": [
            {"project": p.title, "pub_date": (p.pub_date or "")[:10]}
            for p in proposal_rows
        ],
    }

    # ---- who 打给谁：可拨优先 + 决策链优先（可拨/未拨口径照抄 contacts.py 工作列表）----
    contacts_all = session.query(Contact).filter(Contact.enterprise_id == eid).all()

    def _contact_key(c: Contact) -> tuple:
        dialable = bool(load_phones(c.phones_json))
        untouched = dialable and c.call_result in (None, "pending")
        return (
            0 if dialable else 1,
            _DM_RANK.get(c.decision_role or "", 2),
            0 if untouched else 1,
            -c.id,
        )

    contacts_sorted = sorted(contacts_all, key=_contact_key)
    has_phone_count = sum(1 for c in contacts_all if load_phones(c.phones_json))
    who = {
        "contacts": [
            {
                "name": c.name,
                "title": c.title,
                "phones": load_phones(c.phones_json),
                "channel": _CHANNEL_CN.get(c.source or "", c.source),
                "evidence_claim": c.evidence_claim,
            }
            for c in contacts_sorted[:6]
        ],
        "has_phone_count": has_phone_count,
    }

    # ---- leverage 谈资：装机 / 竞对近 30 天抢走的单 / 同行业我方成交案例 ----
    units = session.query(InstalledUnit).filter(InstalledUnit.enterprise_id == eid).all()
    units.sort(key=lambda u: (u.install_date or "", u.id), reverse=True)
    installations = [
        {"model": u.model, "installed_at": (u.install_date or "")[:10]}
        for u in units[:5]
    ]

    # 竞对最近拿了他什么单（30 天）：竞对 tender_win 证据句里的采购人 = 本企业
    # （competitors.feed stolen_from 同口径；名字互含归属，确定性可溯源）
    cutoff30 = (date.today() - timedelta(days=30)).isoformat()
    comp_ids = [
        r[0]
        for r in session.query(Enterprise.id)
        .filter(Enterprise.is_competitor == 1, Enterprise.deleted_at.is_(None))
        .all()
    ]
    comp_wins: list[dict] = []
    if comp_ids:
        comp_rows = (
            session.query(Signal, Enterprise.name)
            .join(Enterprise, Enterprise.id == Signal.enterprise_id)
            .filter(Signal.enterprise_id.in_(comp_ids), Signal.type == "tender_win")
            .order_by(Signal.pub_date.desc(), Signal.id.desc())
            .limit(200)
            .all()
        )
        for s, comp_name in comp_rows:
            if _sig_date(s) < cutoff30:
                continue
            mb = _BUYER_RE.search(s.evidence_claim or "")
            if not mb or not e.name:
                continue
            buyer = mb.group(1)
            if buyer == e.name or e.name in buyer or buyer in e.name:
                comp_wins.append({
                    "title": s.title,
                    "competitor_name": comp_name,
                    "amount_wan": extract_amount_wan(s.evidence_claim or "")
                    or extract_amount_wan(s.raw_excerpt or ""),
                    "pub_date": _sig_date(s),
                })
            if len(comp_wins) >= 5:
                break

    # 同行业且有 won lead 的企业（排除自身）×3（SPEC-BRF：similar_won）
    similar_won: list[dict] = []
    if e.industry_key:
        peer_rows = (
            session.query(Enterprise, Lead)
            .join(Lead, Lead.enterprise_id == Enterprise.id)
            .filter(
                Enterprise.id != eid,
                Enterprise.deleted_at.is_(None),
                Enterprise.industry_key == e.industry_key,
                Lead.status == "won",
            )
            .order_by(Lead.updated_at.desc(), Enterprise.id.asc())
            .limit(3)
            .all()
        )
        similar_won = [
            {
                "id": p.id,
                "name": p.name,
                "industry_name": p.industry_name,
                "won_at": (ld.updated_at or "")[:10],
            }
            for p, ld in peer_rows
        ]
    leverage = {
        "installations": installations,
        "competitor_wins_30d": comp_wins,
        "similar_won": similar_won,
    }

    # ---- next 下一步：跟进记录 + 线索状态 + 确定性 todo_hint ----
    lead = session.query(Lead).filter(Lead.enterprise_id == eid).first()
    followup_rows: list[Followup] = []
    if lead is not None:
        followup_rows = (
            session.query(Followup)
            .filter(Followup.lead_id == lead.id)
            .order_by(Followup.id.desc())
            .limit(5)
            .all()
        )
    followups = [
        {"content": f.content, "created_at": (f.created_at or "")[:16]}
        for f in followup_rows
    ]
    next_block = {
        "followups": followups,
        "lead_status": lead.status if lead is not None else None,
        "todo_hint": _todo_hint(
            followups=followups,
            lead=lead,
            installs=installations,
            dialable_count=has_phone_count,
            band=band,
        ),
    }

    return {
        "enterprise": {
            "name": e.name,
            "industry_name": e.industry_name,
            "province": e.province,
            "city": e.city,
            "registered_capital_wan": e.registered_capital_wan,
            "business_scope": e.business_scope,
            "uscc": e.uscc,
            "icp_score": e.icp_score,
            "intent_score": e.intent_score,
            "is_competitor": e.is_competitor,
        },
        "buying": buying,
        "why_now": why_now,
        "who": who,
        "leverage": leverage,
        "next": next_block,
    }


# ---------------- SPEC-BRF 2.2：A4 一页打印 HTML（六分区销售语言标题） ----------------

_STATUS_CN = {
    "new": "新线索",
    "following": "跟进中",
    "intent": "意向",
    "won": "已成交",
    "lost": "已输单",
    "pool": "公海",
}


def _print_html(e: Enterprise, b: dict, user: User) -> str:
    band = b["why_now"]["intent_band"]
    badge_text, badge_fg, badge_bg = {
        "high": ("意图高", "#b42318", "#fef3f2"),
        "mid": ("意图中", "#8a6d00", "#fff8e1"),
        "low": ("意图低", "#4b5563", "#eef1f4"),
    }[band]
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    ent = b["enterprise"]
    cap = ent["registered_capital_wan"]
    line1 = " · ".join(
        x for x in (
            ent["industry_name"] or "行业未知",
            "".join(x for x in (ent["province"], ent["city"]) if x) or "地区未知",
            f"注册资本 {cap:g} 万元" if cap else None,
        ) if x
    )
    sec1 = (
        f"<p>{line1}</p>"
        f"<p>{ent['business_scope'] or '经营范围待补录——先从中标项目推断其工艺与设备缺口。'}</p>"
    )

    wins = b["buying"]["wins"]
    if wins:
        trs = "".join(
            f"<tr><td>{w['title'][:40]}</td>"
            f"<td class='num'>{w['amount_wan'] if w['amount_wan'] else '—'}</td>"
            f"<td>{w['buyer'] or '—'}</td><td>{w['pub_date']}</td></tr>"
            for w in wins
        )
        buyers_txt = "、".join(t["buyer"] for t in b["buying"]["top_buyers"][:3]) or "—"
        sec2 = (
            "<table><tr><th>中标项目</th><th>金额(万元)</th><th>采购人</th><th>日期</th></tr>"
            f"{trs}</table>"
            f"<p class='note'>近 {len(wins)} 笔中标合计 {b['buying']['amount_total_wan']:g} 万元；"
            f"主要采购人：{buyers_txt}</p>"
        )
    else:
        sec2 = "<p class='muted'>库内暂无中标记录——从经营范围与行业公开线索推断其采购方向。</p>"

    sigs = b["why_now"]["signals"]
    if sigs:
        sig_lis = "".join(
            f"<li><b>{s['type_cn']}</b> {s['title'][:48]}（{s['pub_date']}）</li>"
            for s in sigs[:5]
        )
    else:
        sig_lis = "<li>近期无新信号——意图分不高时按兵不动，等公告再打。</li>"
    prop_lis = "".join(
        f"<li>{p['project'][:48]}（{p['pub_date']}）</li>"
        for p in b["why_now"]["proposals"]
    )
    prop_html = (
        f"<p class='note'>另有拟建/备案项目（采购前兆，早于招标 1-3 个月）：</p><ul>{prop_lis}</ul>"
        if b["why_now"]["proposals"]
        else ""
    )
    sec3 = f"<ul>{sig_lis}</ul>{prop_html}"

    cs = b["who"]["contacts"]
    if cs:
        lis = "".join(
            f"<li><b>{c['name']}</b>（{c['title'] or '职务未知'}，{c['channel']}）"
            + ("，电话：" + " / ".join(c["phones"]) if c["phones"] else "，暂无号码")
            + "</li>"
            for c in cs
        )
        sec4 = (
            f"<ul>{lis}</ul>"
            f"<p class='note'>可拨联系人共 {b['who']['has_phone_count']} 人；"
            "排序=可拨优先+决策链优先。</p>"
        )
    else:
        sec4 = "<p class='muted'>暂无联系人——先从公告代理机构与工商公开电话补录。</p>"

    parts: list[str] = []
    installs = b["leverage"]["installations"]
    if installs:
        lis = "".join(
            f"<li>已装 {i['model']}（{i['installed_at'] or '日期未知'}）</li>"
            for i in installs
        )
        parts.append(
            f"<p><b>存量装机</b></p><ul>{lis}</ul>"
            "<p class='note'>装机即耗材时钟——密封/滤芯到期就是天然回访由头。</p>"
        )
    cw = b["leverage"]["competitor_wins_30d"]
    if cw:
        lis = "".join(
            f"<li>{w['competitor_name']} 近 30 天拿下「{w['title'][:36]}」"
            f"（{w['amount_wan'] if w['amount_wan'] else '金额未知'}万元，{w['pub_date']}）</li>"
            for w in cw
        )
        parts.append(f"<p><b>竞对动向（最近拿了他什么单）</b></p><ul>{lis}</ul>")
    sw = b["leverage"]["similar_won"]
    if sw:
        lis = "".join(
            f"<li>{w['name']}（{w['industry_name'] or ''}）已与我方成交</li>" for w in sw
        )
        parts.append(f"<p><b>同行谁在用我们</b></p><ul>{lis}</ul>")
    sec5 = "".join(parts) or "<p class='muted'>暂无装机/竞对/同行成交谈资——用行业公开案例开场。</p>"

    nxt = b["next"]
    status_cn = _STATUS_CN.get(nxt["lead_status"] or "", "")
    status_line = (
        f"线索状态：{status_cn}。" if status_cn else "尚未建档线索——先转线索再跟进。"
    )
    fu_lis = "".join(
        f"<li>{f['content'][:48]}（{f['created_at']}）</li>" for f in nxt["followups"]
    )
    fu_html = f"<ul>{fu_lis}</ul>" if fu_lis else "<p class='muted'>暂无跟进记录。</p>"
    sec6 = f"<p>{status_line} <b>建议下一步：{nxt['todo_hint']}</b></p>{fu_html}"

    return f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>{e.name} · 客户作战简报</title>
<style>
@page{{size:A4;margin:12mm}}
body{{font-family:-apple-system,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;
     color:#1f2328;max-width:760px;margin:24px auto;padding:0 20px;font-size:13px;line-height:1.7}}
h1{{font-size:19px;margin:0 0 2px}}
h2{{font-size:14px;color:#1b6c80;margin:16px 0 6px;border-bottom:1px solid #d8dee2;padding-bottom:3px}}
.headmeta{{color:#6b7175;font-size:12px;margin:0 0 4px}}
.badge{{display:inline-block;padding:1px 8px;border-radius:10px;font-size:11px;font-weight:600;
       color:{badge_fg};background:{badge_bg};vertical-align:2px}}
table{{width:100%;border-collapse:collapse;margin:4px 0}}
td,th{{border:1px solid #d8dee2;padding:4px 8px;text-align:left;font-size:12px}}
.num{{text-align:right;font-variant-numeric:tabular-nums}}
ul{{margin:4px 0;padding-left:20px}}li{{margin-bottom:2px}}
.note{{color:#8a8d8f;font-size:11.5px;margin:2px 0}}
.muted{{color:#8a8d8f}}
@media print{{.noprint{{display:none}}}}
</style></head><body>
<h1>{e.name} <span class="badge">{badge_text}</span></h1>
<p class="headmeta">客户作战简报 · 生成于 {now} · 数据来自平台公开采集，可溯源 · 生成人：{user.display_name or user.username}</p>
<h2>一、这家公司是干嘛的</h2>
{sec1}
<h2>二、他们在买什么</h2>
{sec2}
<h2>三、为什么现在打</h2>
{sec3}
<h2>四、打给谁</h2>
{sec4}
<h2>五、谈资</h2>
{sec5}
<h2>六、下一步</h2>
{sec6}
<p class="note noprint">打印：浏览器 Ctrl+P / 另存为 PDF（A4 一页）。</p>
<!-- SPEC-BRF-AI: GLM Key 到位后在此注入 AI 摘要段 -->
</body></html>"""


def _load_enterprise(session: Session, eid: int) -> Enterprise:
    e = session.get(Enterprise, eid)
    if e is None or e.deleted_at:
        raise HTTPException(404, "企业不存在")
    return e


@router.get("/enterprises/{eid}/brief")
def enterprise_brief(
    eid: int,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """SPEC-BRF 2.1：六块大聚合 JSON（enterprise/buying/why_now/who/leverage/next）。"""
    e = _load_enterprise(session, eid)
    return _aggregate(session, e)


@router.get("/enterprises/{eid}/brief/print")
def brief_print(
    eid: int,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """SPEC-BRF 2.2：A4 一页打印 HTML。

    模式照抄 proposal.py proposal_print；?token= 回退由 current_user 统一处理。
    """
    e = _load_enterprise(session, eid)
    brief = _aggregate(session, e)
    return HTMLResponse(_print_html(e, brief, user))
