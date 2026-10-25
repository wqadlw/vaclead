"""销售待办 API（吸收 twenty task：手动+自动生成，到期提醒）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_session
from ..models import Enterprise, Todo, User, utcnow

router = APIRouter(tags=["todos"])


class TodoIn(BaseModel):
    title: str
    enterprise_id: int | None = None
    due_date: str | None = None


def _todo_dict(t: Todo, ent: Enterprise | None) -> dict:
    return {
        "id": t.id,
        "title": t.title,
        "enterprise_id": t.enterprise_id,
        "enterprise_name": ent.name if ent else None,
        "due_date": t.due_date,
        "done": t.done,
        "source": t.source,
        "created_at": t.created_at,
    }


@router.get("/todos")
def list_todos(
    done: int = 0,
    scope: str = "open",  # open | today | overdue | all
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    from datetime import date

    stmt = session.query(Todo)
    today = date.today().isoformat()
    if scope == "all":
        pass
    elif scope == "today":
        stmt = stmt.filter(Todo.done == 0, Todo.due_date == today)
    elif scope == "overdue":
        stmt = stmt.filter(Todo.done == 0, Todo.due_date < today)
    else:
        stmt = stmt.filter(Todo.done == done)
    rows = stmt.order_by(Todo.due_date.asc().nullslast(), Todo.id.desc()).limit(100).all()
    ent_ids = {t.enterprise_id for t in rows if t.enterprise_id}
    ents = (
        {e.id: e for e in session.query(Enterprise).filter(Enterprise.id.in_(ent_ids)).all()}
        if ent_ids
        else {}
    )
    return {
        "items": [_todo_dict(t, ents.get(t.enterprise_id)) for t in rows],
        "today": today,
    }


@router.post("/todos", status_code=201)
def create_todo(
    body: TodoIn,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    if not body.title.strip():
        raise HTTPException(422, "待办标题不能为空")
    t = Todo(
        title=body.title.strip()[:200],
        enterprise_id=body.enterprise_id,
        due_date=(body.due_date or "")[:10] or None,
        owner_id=user.id,
        source="manual",
    )
    session.add(t)
    session.commit()
    return _todo_dict(t, session.get(Enterprise, t.enterprise_id) if t.enterprise_id else None)


@router.patch("/todos/{tid}/done")
def toggle_todo(
    tid: int,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    t = session.get(Todo, tid)
    if t is None:
        raise HTTPException(404, "待办不存在")
    t.done = 0 if t.done else 1
    session.commit()
    return {"ok": True, "done": t.done}
