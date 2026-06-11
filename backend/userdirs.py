"""解析本机真实用户目录（用户名/主目录/桌面/下载/文档）。

修复：模型不知道真实路径时会瞎猜 C:\\Users\\admin\\Desktop，找不到再全盘
慢搜。这里用环境变量算出真路径，注入系统提示并提供 user_dirs 只读工具。
兼容：OneDrive 重定向（桌面/文档同步到 OneDrive）+ 中文本地化目录名
（桌面/文档/下载）。任何异常都安全降级，绝不抛。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def _home() -> str:
    return (os.environ.get("USERPROFILE")
            or os.path.expanduser("~")
            or os.getcwd())


def _onedrive() -> str:
    return (os.environ.get("OneDrive")
            or os.environ.get("OneDriveConsumer")
            or os.environ.get("OneDriveCommercial")
            or "")


def _pick(bases: list[str], names: list[str]) -> str:
    """在 bases × names 里找第一个真实存在的目录；都不在则回退
    home\\<第一个英文名>（即便不存在也给出规范路径，胜过让模型瞎猜）。"""
    cands: list[str] = []
    for b in bases:
        if not b:
            continue
        for n in names:
            cands.append(os.path.join(b, n))
    for c in cands:
        try:
            if Path(c).is_dir():
                return str(Path(c))
        except OSError:
            continue
    return cands[0] if cands else ""


def user_dirs() -> dict[str, Any]:
    home = _home()
    od = _onedrive()
    user = (os.environ.get("USERNAME")
            or os.path.basename(home.rstrip("\\/"))
            or "")
    bases = [od, home]
    return {
        "username": user,
        "home": home,
        "desktop": _pick(bases, ["Desktop", "桌面"]),
        "downloads": _pick([home, od], ["Downloads", "下载"]),
        "documents": _pick(bases, ["Documents", "文档", "My Documents"]),
    }


def prompt_hint() -> str:
    """注入到系统提示的一句话：给真实绝对路径，禁止猜用户名。"""
    d = user_dirs()
    return (
        "本机真实路径（涉及这些位置时务必直接用下列绝对路径，"
        "严禁猜测用户名如 admin，也不要用相对路径全盘慢搜）："
        f"用户名={d['username']}；主目录={d['home']}；"
        f"桌面={d['desktop']}；下载={d['downloads']}；"
        f"文档={d['documents']}。"
    )
