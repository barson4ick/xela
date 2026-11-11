import html
import random
from aiogram import Router, F
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import Message, User

from database import ensure_user, change_rep

router = Router()

# Предустановленный пул из 12 мем-предсказаний: (текст_предсказания, ссылка_на_картинку)
FUTURE_POOL = [
    (
        "🔮 Сегодня тебя ждёт внезапный прилив продуктивности, но ровно на 15 минут.",
        "https://http.cat/200.jpg",
    ),
    (
        "🔮 Будь осторожен с дедлайнами: они ближе, чем кажутся.",
        "https://http.cat/408.jpg",
    ),
    (
        "🔮 Твой внутренний ресурс временно недоступен. Требуется кофе и перезагрузка.",
        "https://http.cat/503.jpg",
    ),
    (
        "🔮 В ближайшее время ты найдёшь гениальное решение проблемы, которую сам же и создал.",
        "https://http.cat/201.jpg",
    ),
    (
        "🔮 Сегодня лучше никому не одалживать свои сантиметры и не лезть в авантюры.",
        "https://http.cat/403.jpg",
    ),
    (
        "🔮 Ожидается крупный финансовый или игровой успех ближе к вечеру!",
        "https://http.cat/202.jpg",
    ),
    (
        "🔮 Кто-то в этом чате тайно завидует твоему положению в таблице лидеров.",
        "https://http.cat/409.jpg",
    ),
    (
        "🔮 День пройдёт спокойно, если не нажимать на кнопку «Вертолёт» в дуэлях.",
        "https://http.cat/204.jpg",
    ),
    (
        "🔮 Твои планы на вечер могут резко поменяться из-за одного сообщения.",
        "https://http.cat/307.jpg",
    ),
    (
        "🔮 Звёзды говорят, что пора заварить чай и отложить все споры на завтра.",
        "https://http.cat/418.jpg",
    ),
    (
        "🔮 Слишком много запросов от окружающих! Включай режим энергосбережения.",
        "https://http.cat/429.jpg",
    ),
    (
        "🔮 Удача на твоей стороне: самое время испытать судьбу в /dick!",
        "https://http.cat/100.jpg",
    ),
]

VOICE_EASTER_EGGS = [
    "🎙 Ого, новый выпуск подкаста подъехал! Жаль, я понимаю только текст.",
    "🎧 Записал голосовое — сэкономил секунду себе, потратил минуту всему чату!",
    "📻 Приём-приём, на связи радиостанция нашего чата!",
    "🗣 Говорят, если перевести это голосовое в текст, где-то в мире улыбнётся один интроверт.",
    "🎼 Звучит как заявка на «Грэмми», но субтитров явно не хватает.",
]


def get_display_name(user: User) -> str:
    if user.username:
        return f"@{user.username}"
    return user.full_name or str(user.id)


@router.message(Command("future"))
async def cmd_future(message: Message) -> None:
    if message.from_user:
        await ensure_user(
            message.from_user.id,
            message.chat.id,
            get_display_name(message.from_user),
        )

    prediction_text, photo_url = random.choice(FUTURE_POOL)
    try:
        await message.reply_photo(photo=photo_url, caption=prediction_text)
    except TelegramAPIError:
        # На случай недоступности внешнего хостинга картинок отправляем текст
        await message.reply(prediction_text)


@router.message(F.text.in_({"+", "-", "👍", "👎"}))
async def handle_reputation(message: Message) -> None:
    if not message.from_user or not message.text:
        return

    # Триггер срабатывает только при ответе (reply) на чужое сообщение
    if not message.reply_to_message or not message.reply_to_message.from_user:
        return

    sender = message.from_user
    target = message.reply_to_message.from_user
    chat_id = message.chat.id

    # Игнорируем ответы самому себе и ботам
    if sender.id == target.id or target.is_bot:
        return

    sender_name = get_display_name(sender)
    target_name = get_display_name(target)
    await ensure_user(sender.id, chat_id, sender_name)

    delta = 1 if message.text in {"+", "👍"} else -1
    new_rep = await change_rep(target.id, chat_id, target_name, delta)

    safe_target = html.escape(target_name)
    action_word = "повышена 📈" if delta > 0 else "понижена 📉"

    extra = ""
    if delta > 0:
        import relations
        res = await relations.add_points(chat_id, sender.id, target.id, 1)
        if res["gained"] and res["up"]:
            extra = f"\n🎉 Теперь вы с {safe_target}: <b>{res['info']['stage']}</b>!"
    await message.reply(
        f"✨ Репутация <b>{safe_target}</b> {action_word}!\n"
        f"Текущая карма: <b>{new_rep}</b>{extra}"
    )


@router.message(F.voice)
async def handle_voice_easter_egg(message: Message) -> None:
    if message.from_user:
        await ensure_user(
            message.from_user.id,
            message.chat.id,
            get_display_name(message.from_user),
        )

    # Вероятность срабатывания 12% (в диапазоне 10–15% по ТЗ)
    if random.randint(1, 100) <= 12:
        await message.reply(random.choice(VOICE_EASTER_EGGS))
