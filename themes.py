"""Сезонные темы: в праздничные даты мини-апп меняет акцентный цвет и показывает баннер, а питомец надевает шапку."""
from datetime import date
from typing import Optional

from utils import now_local

# (ключ, название, иконка, начало (м, д), конец (м, д), цвет, баннер)
THEMES = [
    ("halloween", "Хэллоуин", "🎃", (10, 24), (11, 1), "#ff7a18", "Хэллоуин в чате: питомцы в тыквах, бар открыт всю ночь"),
    ("newyear", "Новый год", "🎄", (12, 15), (1, 10), "#22a55b", "Новогоднее настроение: питомцы в шапках Деда Мороза"),
    ("feb23", "23 февраля", "🎖", (2, 20), (2, 23), "#3b82f6", "Поздравляем защитников: сегодня все молодцы"),
    ("mar8", "8 марта", "🌷", (3, 5), (3, 8), "#ec4899", "С 8 марта! Цветы в каждом профиле"),
]


def _in_range(today: date, start: tuple, end: tuple) -> bool:
    s = date(today.year, *start)
    e = date(today.year, *end)
    if e < s:  # диапазон через Новый год
        return today >= s or today <= e
    return s <= today <= e


def current(today: Optional[date] = None) -> Optional[dict]:
    today = today or now_local().date()
    for key, title, icon, start, end, color, banner in THEMES:
        if _in_range(today, start, end):
            return {"key": key, "title": title, "icon": icon, "color": color, "banner": banner}
    return None
