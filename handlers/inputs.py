from aiogram import F, Router
from aiogram.filters import CommandObject
from aiogram.types import Message

from handlers import economy, social
from prompts import consume, peek

router = Router()

# команда → обработчик, которому отдаём введённый ответ как аргумент
HANDLERS = {
    "casino": economy.cmd_casino,
}


async def _is_answer(message: Message) -> bool:
    text = message.text or ""
    return bool(text) and not text.startswith("/") and peek(message) is not None


@router.message(F.text, _is_answer)
async def handle_prompt_answer(message: Message) -> None:
    command = peek(message)
    consume(message)
    if command in HANDLERS:
        await HANDLERS[command](
            message, CommandObject(prefix="/", command=command, args=(message.text or "").strip())
        )
