"""Карточка игрока: картинка для публикации в чат."""
import io
from typing import List

from PIL import Image, ImageColor, ImageDraw, ImageFont

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
W, H = 900, 520


def _font(path: str, size: int):
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def _clean(text: str) -> str:
    # эмодзи шрифт DejaVu не рисует: оставляем только то, что умеет отрисовать
    return "".join(ch for ch in text if ord(ch) < 0x2600 or 0x2C00 <= ord(ch) < 0xA000).strip()


def render(name: str, title: str, size: int, rank: int, players: int, streak: int, rep: int,
           season_rank: int, ach_done: int, ach_total: int, wins: List[tuple], frame=None, bg=None) -> bytes:
    img = Image.new("RGB", (W, H), "#101827")
    d = ImageDraw.Draw(img)
    top = ImageColor.getrgb(bg[0]) if bg else (16, 24, 39)
    bottom = ImageColor.getrgb(bg[1]) if bg else (46, 42, 109)
    for y in range(H):  # вертикальный градиент
        t = y / H
        d.line([(0, y), (W, y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    if frame:
        c1, c2 = ImageColor.getrgb(frame[0]), ImageColor.getrgb(frame[1])
        for k in range(7):  # рамка с градиентом по ширине
            t = k / 6
            col = tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))
            d.rounded_rectangle([20 + k, 20 + k, W - 20 - k, H - 20 - k], radius=36 - k, outline=col, width=1)
    else:
        d.rounded_rectangle([24, 24, W - 24, H - 24], radius=34, outline="#3b82f6", width=3)

    name = _clean(name) or "Игрок"
    d.ellipse([60, 60, 180, 180], fill="#3b82f6")
    initial = name.lstrip("@")[:1].upper() or "?"
    f = _font(BOLD, 64)
    d.text((120, 120), initial, font=f, fill="white", anchor="mm")
    d.text((210, 78), name[:22], font=_font(BOLD, 46), fill="white")
    sub = _clean(title) if title else "без титула"
    d.text((212, 138), sub[:34], font=_font(FONT, 26), fill="#f5a524" if title else "#7a8590")

    d.text((60, 215), f"{size} см", font=_font(BOLD, 96), fill="#ffffff" if size >= 0 else "#ff6b6b")
    d.text((64, 322), f"{rank}-е место из {players} в чате", font=_font(FONT, 28), fill="#9fb3d1")

    items = [("серия", f"{streak} дн."), ("карма", str(rep)), ("сезон", f"#{season_rank}" if season_rank else "-"),
             ("достижения", f"{ach_done}/{ach_total}")]
    x = 60
    for label, value in items:
        d.rounded_rectangle([x, 390, x + 185, 480], radius=20, fill="#1b2740")
        d.text((x + 92, 422), value, font=_font(BOLD, 34), fill="white", anchor="mm")
        d.text((x + 92, 458), label, font=_font(FONT, 20), fill="#9fb3d1", anchor="mm")
        x += 200
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()
