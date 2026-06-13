"""用代码画出 Cogito 图标并输出多尺寸 Windows .ico。

图标 = #89C2FF 圆角方底 + 白色三节点推理图(呼应「自研 AI agent」身份)。
无外部图片源:直接用 Pillow 画,改设计改本文件即可;运行后覆盖 assets/icon.ico。
高分辨率(1024)绘制再降采样,各尺寸边缘平滑。

用法(已装 Pillow 的环境):  python backend/scripts/make_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
DST = ROOT / "assets" / "icon.ico"

# 必含 256×256,否则 electron-builder/Windows 会忽略图标回退默认图
SIZES = [(16, 16), (24, 24), (32, 32), (48, 48),
         (64, 64), (128, 128), (256, 256)]

SS = 1024                     # 先在高分辨率画,再降采样,边缘才平滑
BLUE = (137, 194, 255, 255)   # #89C2FF 主色
WHITE = (255, 255, 255, 255)


def _f(v: float) -> float:
    return v / 100.0 * SS      # 100 坐标系 → 像素


def main() -> None:
    img = Image.new("RGBA", (SS, SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    inset = _f(2)
    d.rounded_rectangle([inset, inset, SS - inset, SS - inset],
                        radius=_f(22), fill=BLUE)

    nodes = [(50, 29), (29, 69), (71, 69)]     # 上 / 左下 / 右下
    pts = [(_f(x), _f(y)) for x, y in nodes]
    lw = int(round(_f(6)))
    for a, b in [(0, 1), (0, 2), (1, 2)]:      # 三条连线(先画线,节点压在端点上)
        d.line([pts[a], pts[b]], fill=WHITE, width=lw)
    r = _f(12)
    for (cx, cy) in pts:                        # 三个节点
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=WHITE)

    master = img.resize((256, 256), Image.Resampling.LANCZOS)
    master.save(DST, format="ICO", sizes=SIZES)
    with Image.open(DST) as chk:
        got = sorted(chk.ico.sizes()) if hasattr(chk, "ico") else []
    print(f"icon written: {DST}  sizes={got}")


if __name__ == "__main__":
    main()
