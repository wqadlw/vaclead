"""日志轮转（SPEC-I I6 P2 收尾）：按天滚动，默认保留 14 份。

- 文件落 data/logs/vaclead.log（data/ 不入库）
- 只接管 vaclead.* 与 uvicorn.error 的业务日志；access 日志同样由此承载
- 幂等：重复调用不重复挂 handler
"""
from __future__ import annotations

import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from .db import DATA_DIR

_LOG_DIR = Path(DATA_DIR) / "logs"
_CONFIGURED = False


def setup_logging(backup_days: int = 14) -> Path | None:
    """挂按天滚动的文件 handler；返回日志目录路径（失败返回 None 不影响启动）。"""
    global _CONFIGURED
    if _CONFIGURED:
        return _LOG_DIR
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        handler = TimedRotatingFileHandler(
            _LOG_DIR / "vaclead.log",
            when="midnight",
            backupCount=backup_days,
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s %(message)s", "%Y-%m-%d %H:%M:%S"
            )
        )
        for name in ("vaclead", "uvicorn.error", "uvicorn.access"):
            lg = logging.getLogger(name)
            if not any(isinstance(h, TimedRotatingFileHandler) for h in lg.handlers):
                lg.addHandler(handler)
        _CONFIGURED = True
        return _LOG_DIR
    except OSError:
        logging.getLogger("vaclead").warning("日志目录不可写，文件轮转未启用", exc_info=True)
        return None
