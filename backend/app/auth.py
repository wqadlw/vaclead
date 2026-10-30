"""JWT 认证 — 登录签发、依赖注入当前用户与管理员校验（企业级加固）。

- 密钥强制环境注入：VACLEAD_JWT_SECRET 未设置时用机器随机密钥（进程级），
  并在日志中告警——多进程/重启会话失效风险显式化，禁止硬编码弱默认。
- 登录限流：同 IP 5 次失败后冷却 15 分钟（内存实现，单进程部署语义）。
"""
from __future__ import annotations

import logging
import os
import secrets
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .db import get_session
from .models import User

logger = logging.getLogger("vaclead.auth")

_ENV_SECRET = os.environ.get("VACLEAD_JWT_SECRET", "").strip()
if not _ENV_SECRET:
    _ENV_SECRET = secrets.token_hex(32)  # 进程级随机，重启失效（安全优于可用）
    logger.warning(
        "VACLEAD_JWT_SECRET 未设置：使用进程级随机密钥，重启后所有会话失效。"
        "生产部署必须显式设置该环境变量。"
    )
SECRET = _ENV_SECRET
ALGORITHM = "HS256"
TOKEN_HOURS = 72

bearer = HTTPBearer(auto_error=False)

# ---- 登录限流（内存；单进程部署足够，多进程时换 Redis）----
_LOGIN_FAILS: dict[str, list[float]] = defaultdict(list)
_LOGIN_WINDOW_S = 900.0  # 15 分钟冷却窗口
_LOGIN_MAX_FAILS = 5


def login_rate_limited(request: Request) -> None:
    """同 IP 登录失败超限则 429；成功登录由 login 端点清除计数。"""
    ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    fails = [t for t in _LOGIN_FAILS.get(ip, []) if now - t < _LOGIN_WINDOW_S]
    _LOGIN_FAILS[ip] = fails
    if len(fails) >= _LOGIN_MAX_FAILS:
        raise HTTPException(429, "登录失败次数过多，请 15 分钟后重试")


def record_login_failure(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    _LOGIN_FAILS[ip].append(time.monotonic())


def clear_login_failures(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    _LOGIN_FAILS.pop(ip, None)


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_token(user: User) -> str:
    payload = {
        "sub": str(user.id),
        "username": user.username,
        "role": user.role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=TOKEN_HOURS),
    }
    return jwt.encode(payload, SECRET, algorithm=ALGORITHM)


def _load_user(
    creds: HTTPAuthorizationCredentials | None, session: Session
) -> User:
    if creds is None or not creds.credentials:
        raise HTTPException(status_code=401, detail="未登录")
    try:
        payload = jwt.decode(creds.credentials, SECRET, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="token无效或已过期") from None
    user = session.get(User, int(payload["sub"]))
    if user is None:
        raise HTTPException(status_code=401, detail="用户不存在")
    if not user.active:
        raise HTTPException(status_code=401, detail="账号已禁用")
    return user


def _load_api_key(raw: str, request: Request, session: Session) -> User:
    """X-Api-Key 认证（SPEC-J J1，twenty api-key 思想）：机器通道。

    scopes=read 仅放行 GET/HEAD；rw 全放行。明文不落库（sha256），
    命中即刷新 last_used_at。
    """
    import hashlib

    from .models import UserApiKey

    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    row = (
        session.query(UserApiKey)
        .filter(UserApiKey.key_hash == key_hash, UserApiKey.revoked == 0)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=401, detail="API Key 无效或已吊销")
    if row.scopes == "read" and request.method not in ("GET", "HEAD"):
        raise HTTPException(status_code=403, detail="该 API Key 为只读 scope")
    user = session.get(User, row.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="API Key 所属用户不存在")
    row.last_used_at = datetime.now(timezone.utc).isoformat()  # 随端点事务落库（尽力而为）
    return user


async def current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    session: Session = Depends(get_session),
) -> User:
    """async 依赖：在请求上下文（事件循环）里 set actor contextvar，
    使同一请求的线程池端点继承（sync 依赖里 set 只活在各自线程副本，SPEC-J 实测踩坑）。"""
    from .audit import current_actor

    api_key = (request.headers.get("X-Api-Key") or "").strip()
    if api_key:
        user = _load_api_key(api_key, request, session)
    else:
        user = _load_user(creds, session)
    if not user.active:  # API Key 通道同样执行禁用拦截（SPEC-N N1）
        raise HTTPException(status_code=401, detail="账号已禁用")
    current_actor.set(user.id)
    return user


def admin_only(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


def visible_owner_ids(user: User, session: Session) -> list[int] | None:
    """线索可见的 owner 集合（SPEC-N N2）：None=不限（admin）。

    sales=自己；团队负责人（team.leader_id=自己）追加全部成员；
    公海（owner NULL）由调用方另行放行，本函数只管"人"的集合。
    """
    if user.role == "admin":
        return None
    ids = [user.id]
    if user.team_id is not None:
        from .models import Team

        team = session.get(Team, user.team_id)
        if team is not None and team.leader_id == user.id:
            ids = [
                int(uid)
                for (uid,) in session.query(User.id)
                .filter(User.team_id == user.team_id, User.active == 1)
                .all()
            ]
    return ids
