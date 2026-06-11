"""ai-helper 后端日志:5MB × 2 滚动文件,总盘大约 10MB。

用法:启动时 setup_logging() 一次;之后用 logging.getLogger("aih") 写自己的;
uvicorn/uvicorn.error/uvicorn.access 也会被劫持落到同一份日志,方便统一查。
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from config import DATA_DIR

LOG_DIR = DATA_DIR / "logs"
LOG_PATH = LOG_DIR / "ai-helper.log"
MAX_BYTES = 5 * 1024 * 1024  # 5MB / 文件
BACKUP_COUNT = 1              # 1 个备份文件 → 总约 10MB
_INSTALLED = False


def setup_logging(level: int = logging.INFO) -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh = RotatingFileHandler(
        LOG_PATH, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    fh.setLevel(level)
    fh.setFormatter(fmt)
    # 注册到 root + uvicorn 各个 logger:都进同一份滚动文件
    for name in ("", "uvicorn", "uvicorn.error", "uvicorn.access", "aih"):
        lg = logging.getLogger(name)
        lg.setLevel(level)
        # 防止重复 add(热重启场景)
        if not any(isinstance(h, RotatingFileHandler)
                    and getattr(h, "baseFilename", "")
                    == str(LOG_PATH.resolve())
                    for h in lg.handlers):
            lg.addHandler(fh)
    _INSTALLED = True


def get_tail(lines: int = 200) -> str:
    """返回最近 N 行(无视滚动备份,只读主日志文件)。"""
    if not LOG_PATH.is_file():
        return ""
    try:
        with LOG_PATH.open("r", encoding="utf-8", errors="replace") as f:
            data = f.readlines()
        return "".join(data[-max(1, min(lines, 2000)):])
    except OSError:
        return ""


def clear_log() -> bool:
    try:
        for p in (LOG_PATH, LOG_PATH.with_suffix(".log.1")):
            if p.exists():
                p.write_text("", encoding="utf-8")
        return True
    except OSError:
        return False


def stats() -> dict[str, Any]:
    """日志体积/路径概览,供设置页显示。"""
    def _sz(p: Path) -> int:
        try:
            return p.stat().st_size if p.exists() else 0
        except OSError:
            return 0
    a = _sz(LOG_PATH)
    b = _sz(LOG_PATH.with_suffix(".log.1"))
    return {
        "path": str(LOG_PATH),
        "size": a + b,
        "max_total": MAX_BYTES * (BACKUP_COUNT + 1),
    }
