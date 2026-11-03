"""用户界面偏好（SPEC-AF AF3，twenty 模式：偏好存服务端跨设备一致）。

每个用户一组 JSON 偏好，settings_kv 键 = pref:{page}:u{user_id}。
先落 dashboard（工作台布局：区块显隐+排序），后续页面按需扩。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_session
from ..models import SettingsKV, User

router = APIRouter(tags=["prefs"])

#: 各页面允许的键白名单（防御任意写入）
_PAGES = {
    # 工作台区块 key 全集（前端 AppHome 区块注册表）
    "dashboard": {"order": list[str], "hidden": list[str]},
}


def _pref_key(page: str, user_id: int) -> str:
    return f"pref:{page}:u{user_id}"


class DashboardPrefs(BaseModel):
    order: list[str] = Field(default_factory=list, max_length=20)
    hidden: list[str] = Field(default_factory=list, max_length=20)


@router.get("/prefs/{page}")
def get_prefs(page: str, session: Session = Depends(get_session),
              user: User = Depends(current_user)):
    if page not in _PAGES:
        raise HTTPException(404, "未知偏好页")
    row = session.get(SettingsKV, _pref_key(page, user.id))
    if row is None or not row.value:
        return {"order": [], "hidden": []}
    try:
        data = json.loads(row.value)
    except ValueError:
        return {"order": [], "hidden": []}
    return {"order": list(data.get("order", [])), "hidden": list(data.get("hidden", []))}


@router.put("/prefs/dashboard")
def put_prefs_dashboard(body: DashboardPrefs,
                        session: Session = Depends(get_session),
                        user: User = Depends(current_user)):
    key = _pref_key("dashboard", user.id)
    row = session.get(SettingsKV, key)
    val = json.dumps({"order": body.order, "hidden": body.hidden}, ensure_ascii=False)
    if row is None:
        session.add(SettingsKV(key=key, value=val))
    else:
        row.value = val
    session.commit()
    return {"ok": True}
