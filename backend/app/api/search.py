"""全局搜索 API（SPEC-BZ）：企业/联系人/标讯三实体 typeahead。

命令面板（Ctrl+K）的数据源；typeahead 规模 ≤16 行/次，LIKE 全扫描
在小表毫秒级，不依赖 Meili。已删企业全链路排除。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_session
from ..models import Contact, Enterprise, Signal, User

router = APIRouter(tags=["search"])


@router.get("/search")
def global_search(
    q: str = "",
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """三实体分组搜索：企业（意图分降序 6）/联系人（5）/标讯（5）。"""
    kw = q.strip()
    if not kw:
        return {"enterprises": [], "contacts": [], "signals": []}

    ents = (
        session.query(Enterprise)
        .filter(Enterprise.deleted_at.is_(None), Enterprise.name.contains(kw))
        .order_by(Enterprise.intent_score.desc(), Enterprise.id.desc())
        .limit(6)
        .all()
    )
    cts = (
        session.query(Contact, Enterprise)
        .join(Enterprise, Enterprise.id == Contact.enterprise_id)
        .filter(Contact.name.contains(kw), Enterprise.deleted_at.is_(None))
        .order_by(Contact.id.desc())
        .limit(5)
        .all()
    )
    sigs = (
        session.query(Signal, Enterprise.name)
        .join(Enterprise, Enterprise.id == Signal.enterprise_id)
        .filter(Signal.title.contains(kw), Enterprise.deleted_at.is_(None))
        .order_by(Signal.id.desc())
        .limit(5)
        .all()
    )
    return {
        "enterprises": [
            {
                "id": e.id, "name": e.name,
                "industry_name": e.industry_name,
                "province": e.province, "city": e.city,
                "is_competitor": e.is_competitor,
            }
            for e in ents
        ],
        "contacts": [
            {
                "id": c.id, "name": c.name, "title": c.title,
                "enterprise_id": e.id, "enterprise_name": e.name,
            }
            for c, e in cts
        ],
        "signals": [
            {"id": s.id, "title": s.title, "type": s.type, "ent_name": ename}
            for s, ename in sigs
        ],
    }
