import logging
from typing import Dict, Tuple

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import Message, ReactionTypeEmoji

from database import add_answer, add_size, ensure_user, get_daily_q, open_questions
from utils import day_str, display_name

log = logging.getLogger(__name__)
router = Router()

ANSWER_REWARD = 1

# Вопросы дня: ответ на сообщение бота — +1 см, вечером бот подводит итоги
QUESTIONS = [
    "Какую суперсилу ты бы выбрал, если бы она была бесполезной в 90% случаев?",
    "Что бы ты делал, если бы весь день был выходным без последствий?",
    "Какое самое странное блюдо ты пробовал и повторил бы?",
    "Какая последняя вещь рассмешила тебя до слёз?",
    "Если бы чат был страной, какой бы там был главный закон?",
    "С каким животным ты бы поменялся жизнью на неделю?",
    "Какой навык кажется тебе бесполезным, но ты им гордишься?",
    "Назови фильм, который можно пересматривать бесконечно.",
    "Какое место в мире ты хочешь увидеть, но боишься разочароваться?",
    "Что бы ты сказал себе на год назад?",
    "Какая у тебя самая неловкая привычка?",
    "Если бы сегодня тебе выдали миллион, на что бы потратил первые 5 минут?",
    "Какой совет ты чаще всех даёшь, но сам ему не следуешь?",
    "Кого из чата ты бы позвал в случае зомби-апокалипсиса и почему?",
    "Какая песня у тебя сейчас играет на повторе?",
    "Что ты делаешь, когда нужно срочно сделать вид, что всё под контролем?",
    "Какой мем лучше всего описывает твою неделю?",
    "Что бы ты изобрёл, если бы все ресурсы мира были твоими?",
    "Какая профессия кажется тебе самой недооценённой?",
    "Что бы ты поменял в этом чате прямо сегодня?",
    "Если бы твоя жизнь была сериалом, как бы назывался текущий сезон?",
    "Какая самая странная вещь есть у тебя дома?",
    "Что тебя в последний раз приятно удивило?",
    "Назови три вещи, без которых ты не выходишь из дома.",
    "Какую глупость ты бы с удовольствием совершил хоть раз?",
    "Что за прозвище тебе подходит больше всего?",
    "Какая у тебя самая смешная история про опоздание?",
    "Если бы ты мог поужинать с кем угодно, живым или нет, то с кем?",
    "Какая еда лечит любое плохое настроение?",
    "Какой ты видишь свою идеальную субботу?",
]

# (chat_id, message_id вопроса) → день; хранится в памяти, после рестарта восстанавливается из БД
questions: Dict[Tuple[int, int], str] = {}


async def load_open_questions() -> None:
    questions.clear()
    for chat_id, message_id, day in await open_questions(day_str(-2)):
        questions[(chat_id, message_id)] = day


async def _is_answer(message: Message) -> bool:
    reply = message.reply_to_message
    return bool(
        message.text
        and not message.text.startswith("/")
        and reply
        and (message.chat.id, reply.message_id) in questions
    )


@router.message(F.text, _is_answer)
async def handle_answer(message: Message, bot: Bot) -> None:
    user = message.from_user
    if not user or user.is_bot or not message.reply_to_message:
        return
    day = questions[(message.chat.id, message.reply_to_message.message_id)]
    name = display_name(user)
    await ensure_user(user.id, message.chat.id, name)
    if not await add_answer(message.chat.id, day, user.id, (message.text or "")[:300]):
        return  # награда только за первый ответ
    new_size = await add_size(user.id, message.chat.id, name, ANSWER_REWARD, "question")
    try:
        await bot.set_message_reaction(
            chat_id=message.chat.id,
            message_id=message.message_id,
            reaction=[ReactionTypeEmoji(emoji="👍")],
        )
    except TelegramAPIError:
        await message.reply(f"✅ Ответ принят: +{ANSWER_REWARD} см (теперь {new_size} см)")


@router.message(Command("question"))
async def cmd_question(message: Message) -> None:
    row = await get_daily_q(message.chat.id, day_str())
    if not row:
        await message.reply("❓ Вопрос дня выходит в 11:00. Подожди немного!")
        return
    await message.reply(
        f"❓ <b>Вопрос дня:</b> {row[1]}\n\nОтветь на моё утреннее сообщение с этим вопросом — получишь +{ANSWER_REWARD} см.",
        reply_to_message_id=row[0],
        allow_sending_without_reply=True,
    )
