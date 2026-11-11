import logging
import time
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import Message

from database import count_command, count_message, ensure_user, kv_set
from handlers.chatai import remember
from utils import day_str, display_name

log = logging.getLogger(__name__)

ANON_BOTS = {1087968824, 136817688}  # «Анонимный админ» и канал-отправитель: это не игроки
COMMAND_GAP = 1.5  # секунд между командами одного игрока


class ThrottleMiddleware(BaseMiddleware):
    """Отбрасывает команды, присланные чаще раза в COMMAND_GAP секунд (защита от спама)."""

    def __init__(self) -> None:
        self._last: Dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any],
    ) -> Any:
        if event.from_user and event.text and event.text.startswith("/"):
            now = time.monotonic()
            if now - self._last.get(event.from_user.id, 0) < COMMAND_GAP:
                return None
            self._last[event.from_user.id] = now
            if len(self._last) > 5000:
                self._last = {k: v for k, v in self._last.items() if now - v < 60}
        return await handler(event, data)


class DrunkMiddleware(BaseMiddleware):
    """Если автор сообщения пьян, бот отдельно показывает его речь заплетающейся (не мешая остальным обработчикам)."""

    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any],
    ) -> Any:
        result = await handler(event, data)
        user = event.from_user
        if (
            user and user.id not in ANON_BOTS and not user.is_bot and event.text and not event.text.startswith("/")
            and event.chat.type in ("group", "supergroup") and data.get("bot")
        ):
            import asyncio
            from handlers.bar import drunk_effect
            asyncio.create_task(_safe_effect(drunk_effect, event, data["bot"]))
        return result


async def _safe_effect(fn, event, bot) -> None:
    try:
        await fn(event, bot)
    except Exception:
        log.exception("Сбой пьяного эффекта")


class StatsMiddleware(BaseMiddleware):
    """Считает сообщения для недельного дайджеста, запоминает участников и контекст беседы."""

    def __init__(self) -> None:
        self._titles: Dict[int, str] = {}

    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any],
    ) -> Any:
        user = event.from_user
        if user and not user.is_bot and event.text and event.text.startswith("/"):
            try:  # какие команды реально используют (для решений, что убрать)
                await count_command(user.id, event.text.split()[0][1:].split("@")[0].lower(), day_str())
                if event.chat.type in ("group", "supergroup"):
                    import chat_events
                    await chat_events.bump(event.chat.id, user.id, "cmd")
            except Exception:
                log.exception("Не удалось записать статистику команды")
        if user and user.id not in ANON_BOTS and not user.is_bot and event.chat.type in ("group", "supergroup"):
            try:
                await ensure_user(user.id, event.chat.id, display_name(user))
                await count_message(event.chat.id, user.id, day_str())
                title = event.chat.title or ""
                if title and self._titles.get(event.chat.id) != title:
                    await kv_set(f"chat_title:{event.chat.id}", title)  # для выбора чата в мини-аппе
                    self._titles[event.chat.id] = title
                remember(event)
                import missions
                await missions.on_message(event)
            except Exception:
                log.exception("Не удалось записать статистику сообщения")
        return await handler(event, data)
