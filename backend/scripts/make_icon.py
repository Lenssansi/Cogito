"""把机器人终端 PNG 转成多尺寸 Windows .ico。

用法：在已装 Pillow 的环境里
    python backend/scripts/make_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "assets" / "robot.png"
DST = ROOT / "assets" / "icon.ico"
# 必含 256×256，否则 electron-builder/Windows 会忽略图标回退默认图
SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
         (128, 128), (256, 256)]


def main() -> None:
    img = Image.open(SRC).convert("RGBA")
    # 贴到正方形透明画布，避免非方形图标被拉伸变形
    side = max(img.size)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
    # 关键：Pillow 存 ICO 不会放大，源若 < 256 则 256 档会被丢弃，
    # 导致 electron-builder/Windows 忽略图标回退默认图。先放大到 256。
    base = max(256, side)
    canvas = canvas.resize((base, base), Image.LANCZOS)
    canvas.save(DST, format="ICO", sizes=SIZES)
    with Image.open(DST) as chk:
        got = sorted(chk.ico.sizes()) if hasattr(chk, "ico") else []
    print(f"icon written: {DST}  含尺寸={got}")


if __name__ == "__main__":
    main()
