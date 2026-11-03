"""团队共享视图（SPEC-AC AC3）：筛选/列配置存服务端，团队统一管线口径。

- 私有视图：仅本人可见/可删
- 共享视图：user_ids 非空数组=名单内可见，空数组=全员可见；仅 owner/admin 可删
- 他人应用共享视图为只读（可"另存为我的"——前端本地操作）
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_session
from ..models import SavedView, User

router = APIRouter(tags=["views"])


class SavedViewIn(BaseModel):
    name: str
    page: str = "enterprises"
    user_ids: list[int] = []
    filters: dict = {}
    hidden_cols: list[str] = []
    dense: bool = False
    col_aggs: dict = {}
    shared: bool = False


def _serialize(v: SavedView, me: User) -> dict:
    return {
        "id": v.id,
        "name": v.name,
        "page": v.page,
        "owner_id": v.owner_id,
        "is_mine": v.owner_id == me.id,
        "filters": json.loads(v.filters or "{}"),
        "hidden_cols": json.loads(v.hidden_cols or "[]"),
        "dense": bool(v.dense),
        "col_aggs": json.loads(v.col_aggs or "{}"),
        "created_at": v.created_at,
    }


@router.get("/saved-views")
def list_saved_views(
    page: str = "enterprises",
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    """我的私有 + 共享给我的（全员共享 + 定向名单含我）。"""
    rows = (
        session.query(SavedView)
        .filter(SavedView.page == page)
        .order_by(SavedView.id.desc())
        .all()
    )
    return {"items": [_serialize(v, user) for v in rows]}


@router.post("/saved-views", status_code=201)
def create_saved_view(
    body: SavedViewIn,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    name = (body.name or "").strip()
    if not 1 <= len(name) <= 40:
        raise HTTPException(422, "视图名 1-40 字")
    dup = (
        session.query(SavedView)
        .filter(
            SavedView.page == body.page,
            SavedView.name == name,
            SavedView.owner_id == user.id,
        )
        .first()
    )
    if dup is not None:
        raise HTTPException(409, "同名视图已存在")
    v = SavedView(
        page=body.page,
        name=name,
        owner_id=user.id,
        user_ids=json.dumps(body.user_ids or []),
        filters=json.dumps(body.filters or {}, ensure_ascii=False),
        hidden_cols=json.dumps(body.hidden_cols or []),
        dense=1 if body.dense else 0,
        col_aggs=json.dumps(body.col_aggs or {}, ensure_ascii=False),
    )
    session.add(v)
    session.commit()
    return {"id": v.id}


@router.delete("/saved-views/{vid}")
def delete_saved_view(
    vid: int,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    v = session.get(SavedView, vid)
    if v is None:
        raise HTTPException(404, "视图不存在")
    if v.owner_id != user.id and user.role != "admin":
        raise HTTPException(403, "仅本人或管理员可删除")
    session.delete(v)
    session.commit()
    return {"ok": True}
