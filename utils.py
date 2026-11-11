import hashlib
import html
from datetime import datetime, timedelta

from aiogram.types import User

from config import TZ


def display_name(user: User) -> str:
    """Формирует отображаемое имя или @username пользователя."""
    if user.username:
        return f"@{user.username}"
    return user.full_name or str(user.id)


def esc(text: str) -> str:
    return html.escape(text)


def now_local() -> datetime:
    return datetime.now(TZ)


def day_str(offset_days: int = 0) -> str:
    """Дата в часовом поясе чата (ISO), со сдвигом в днях."""
    return (now_local().date() + timedelta(days=offset_days)).isoformat()


def seed_int(*parts) -> int:
    """Стабильное число из набора значений (для «предсказаний», зависящих от дня)."""
    raw = "|".join(str(p) for p in parts).encode("utf-8")
    return int(hashlib.md5(raw).hexdigest(), 16)
