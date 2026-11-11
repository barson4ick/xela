"""Диалоговый ввод: команда без параметра → бот спрашивает значение и ждёт ответ на своё сообщение."""
import time
from typing import Dict, Optional, Tuple

from aiogram.types import ForceReply, Message

PROMPT_TTL = 300  # сколько секунд бот ждёт ответ

# (chat_id, id сообщения с вопросом) → (id игрока, команда, время)
_pending: Dict[Tuple[int, int], Tuple[int, str, float]] = {}


def _purge() -> None:
    now = time.monotonic()
    for key in [k for k, v in _pending.items() if now - v[2] > PROMPT_TTL]:
        _pending.pop(key, None)


async def ask(message: Message, command: str, text: str, placeholder: str = "") -> None:
    """Задаёт вопрос ответом на команду игрока; отвечать должен именно он."""
    if not message.from_user:
        return
    sent = await message.reply(
        text,
        reply_markup=ForceReply(selective=True, input_field_placeholder=placeholder or None),
    )
    _purge()
    _pending[(message.chat.id, sent.message_id)] = (message.from_user.id, command, time.monotonic())


def peek(message: Message) -> Optional[str]:
    """Название команды, если сообщение — ответ того же игрока на вопрос бота."""
    reply = message.reply_to_message
    if not reply or not message.from_user:
        return None
    entry = _pending.get((message.chat.id, reply.message_id))
    if not entry or entry[0] != message.from_user.id:
        return None
    if time.monotonic() - entry[2] > PROMPT_TTL:
        _pending.pop((message.chat.id, reply.message_id), None)
        return None
    return entry[1]


def consume(message: Message) -> None:
    if message.reply_to_message:
        _pending.pop((message.chat.id, message.reply_to_message.message_id), None)
