import logging
import random
import re
import time
from collections import defaultdict, deque
from typing import Deque, Dict

import aiohttp
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReactionTypeEmoji,
)

from config import AI_MODEL, ANTHROPIC_API_KEY, GEMINI_API_KEY, GEMINI_BASE_URL, GEMINI_MODEL, GIPHY_API_KEY, GIPHY_BASE_URL
from database import kv_get, kv_set
from utils import display_name, esc

log = logging.getLogger(__name__)
router = Router()

AI_COOLDOWN = 8  # секунд между ответами в одном чате
BASE_PROMPT = (
    "Ты — свой в доску друг в чате друзей, бот чата. Пишешь как живой человек в мессенджере: коротко, просто, "
    "по-разговорному, как будто строчишь с телефона. Можно «ну», «короче», «слушай», «блин», «кстати», «ой», "
    "«хах», «ладно», неполные предложения, лёгкие междометия. Никакого официоза, канцелярита и пафоса. "
    "Юмор дружеский и добрый: шутишь, иронизируешь, слегка подкалываешь, но без злости и без нравоучений. "
    "Больше тепла и живого интереса, чем сарказма; подкол — максимум одна лёгкая шпилька, и только если уместно. "
    "Отвечай очень коротко: обычно одна фраза, максимум две, до ~25 слов. Без списков, без вступлений и пересказа вопроса. "
    "Мемный сленг вставляй редко и к месту (не больше одного слова за ответ, без повторов из ответа в ответ). "
    "Эмодзи — не чаще одного и не в каждом ответе. "
    "Ты бот и этого не скрываешь: не выдумывай, что у тебя есть тело, дом или вечер; можешь пошутить про своё ботство. "
    "Красные линии: никакого мата, никаких оскорблений по внешности, здоровью, семье, национальности, "
    "вере, ориентации, никакой политики и травли. Если человеку плохо или вопрос серьёзный — "
    "ответь тепло, по-человечески и по делу, без шуток. "
    "Можешь ссылаться на недавние сообщения чата. Не используй markdown и HTML."
)
GIF_PROMPT = (
    " Примерно в каждом третьем ответе, если это смешно и к месту, добавь в самый конец тег "
    "[gif: короткий поисковый запрос на английском, например: facepalm, mind blown, this is fine] — "
    "бот заменит его на подходящую гифку. В остальных ответах тег не пиши."
)

_trends = ""  # актуальные мемы дня; обновляются раз в сутки


def system_prompt() -> str:
    prompt = BASE_PROMPT
    if _trends:
        prompt += f" Сейчас в тренде (используй к месту, а не списком): {_trends}."
    if GIPHY_API_KEY:
        prompt += GIF_PROMPT
    return prompt

FALLBACK = [
    "Я бы ответил, но решил сохранить интригу. 😏",
    "Интересный вопрос. Задай ещё раз?",
    "Ой, тут я пас. Спроси чуть позже?",
    "Хм, дай подумать... нет, не придумал 😄",
    "Мозги зависли. Повтори, а?",
    "Если это вызов — принимаю, но только в /duel.",
]

# Короткая память чата для контекста (только в оперативной памяти)
history: Dict[int, Deque[str]] = defaultdict(lambda: deque(maxlen=20))
_last_reply: Dict[int, float] = {}
_bot_username: str = ""


def remember(message: Message) -> None:
    text = message.text or message.caption
    if text and message.from_user and not message.from_user.is_bot and not text.startswith("/"):
        history[message.chat.id].append(f"{display_name(message.from_user)}: {text[:200]}")


def ai_enabled() -> bool:
    return bool(GEMINI_API_KEY or ANTHROPIC_API_KEY)


async def _post_json(url: str, headers: dict, payload: dict) -> dict | None:
    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, headers=headers, json=payload) as resp:
            if resp.status != 200:
                log.warning("ИИ-провайдер %s: HTTP %s", url.split("/")[2], resp.status)
                return None
            return await resp.json()


async def _complete_gemini(system: str, prompt: str, tools: list | None = None, max_tokens: int = 600) -> str | None:
    payload = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 1.0},
    }
    if tools:
        payload["tools"] = tools
    data = await _post_json(
        f"{GEMINI_BASE_URL}/models/{GEMINI_MODEL}:generateContent",
        {"x-goog-api-key": GEMINI_API_KEY, "content-type": "application/json"},
        payload,
    )
    if not data:
        return None
    # ответ может быть заблокирован фильтрами — тогда кандидатов или текста нет
    parts = ((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts).strip() or None


async def _complete_claude(system: str, prompt: str) -> str | None:
    data = await _post_json(
        "https://api.anthropic.com/v1/messages",
        {
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        {
            "model": AI_MODEL,
            "max_tokens": 300,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        },
    )
    if not data:
        return None
    return "".join(b.get("text", "") for b in data.get("content", [])).strip() or None


async def _ask_ai(chat_id: int, asker: str, question: str, snark: bool = False, private: bool = False) -> str | None:
    """Ответ нейросети по контексту беседы; None — ИИ не подключён или не ответил."""
    if not ai_enabled():
        return None
    context = "\n".join(history[chat_id])
    if snark:
        prompt = (
            f"Последние сообщения чата:\n{context}\n\n"
            f"Тебя никто не звал, но ты хочешь вставить короткую дружескую реплику с лёгким юмором про последнее сообщение от {asker}: "
            f"«{question}». Одна короткая живая фраза, до 12 слов. Если ничего смешного не придумалось — ответь ровно SKIP."
        )
    elif private:
        prompt = f"Личная переписка с {asker}. История диалога:\n{context}\n\nОтветь на последнее сообщение."
    else:
        prompt = f"Последние сообщения чата:\n{context}\n\n{asker} обращается к тебе: {question}"
    try:
        if GEMINI_API_KEY:
            return await _complete_gemini(system_prompt(), prompt)
        return await _complete_claude(system_prompt(), prompt)
    except Exception as exc:
        log.warning("ИИ недоступен: %s", exc)
        return None


# ───────── гифки ─────────

GIF_TAG = re.compile(r"\[gif:\s*([^\]]{2,80})\]", re.I)


def split_gif(text: str) -> tuple:
    """Отделяет тег [gif: запрос] от текста ответа. Возвращает (текст, запрос или None)."""
    match = GIF_TAG.search(text)
    query = match.group(1).strip() if match else None
    return GIF_TAG.sub("", text).strip(), query


async def find_gif(query: str) -> str | None:
    """Ищет гифку в Giphy; возвращает прямой адрес файла или None."""
    if not GIPHY_API_KEY:
        return None
    try:
        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                f"{GIPHY_BASE_URL}/gifs/search",
                params={"api_key": GIPHY_API_KEY, "q": query, "limit": 10, "rating": "pg-13", "lang": "en"},
            ) as resp:
                if resp.status != 200:
                    log.warning("Giphy: HTTP %s", resp.status)
                    return None
                items = (await resp.json()).get("data") or []
    except Exception as exc:
        log.warning("Giphy недоступен: %s", exc)
        return None
    urls = []
    for item in items:
        images = item.get("images") or {}
        url = (images.get("fixed_height") or {}).get("mp4") or (images.get("original") or {}).get("url")
        if url:
            urls.append(url)
    return random.choice(urls) if urls else None


async def reply_ai(message: Message, answer: str) -> str:
    """Отвечает текстом ИИ, при теге [gif: ...] прикладывает гифку. Возвращает чистый текст."""
    clean, query = split_gif(answer)
    url = await find_gif(query) if query else None
    if url:
        try:
            await message.reply_animation(url, caption=esc(clean)[:1000] or None)
            return clean
        except TelegramAPIError as exc:
            log.warning("Гифка не отправлена: %s", exc)
    await message.reply(esc(clean or answer))
    return clean


# ───────── актуальные мемы ─────────

TRENDS_EVERY = 86400      # обновлять раз в сутки
TRENDS_RETRY = 6 * 3600   # после неудачи пробовать не чаще, чем раз в 6 часов
TRENDS_SYSTEM = "Ты редактор медиа о мемах и интернет-культуре. Отвечай только списком."
TRENDS_QUERY = (
    "Назови 10 самых актуальных сейчас мемов, трендов и словечек интернет-сленга в русскоязычном интернете. "
    "Формат: одна строка через запятую, каждое — 1–4 слова, без пояснений, ссылок и нумерации. Только безобидные."
)
_TREND_ITEM = re.compile(r"^[\w\s\-!?.'«»]{2,40}$")


def _clean_trends(text: str) -> str:
    """Оставляет только короткие безобидные слова-мемы: текст попадёт в системный промпт."""
    items = [i.strip(" .\n\t*-•") for i in re.split(r"[,\n;]", text)]
    good = [i for i in items if _TREND_ITEM.match(i)]
    return ", ".join(good[:12])[:600]


async def load_trends() -> None:
    global _trends
    _trends = await kv_get("trends_text") or ""


async def refresh_trends() -> None:
    """Раз в сутки обновляет список мемов: через поиск Google, а если он недоступен — из памяти модели."""
    global _trends
    if not GEMINI_API_KEY:
        return
    now = time.time()
    if now - float(await kv_get("trends_ts") or 0) < TRENDS_EVERY:
        return
    if now - float(await kv_get("trends_try") or 0) < TRENDS_RETRY:
        return
    await kv_set("trends_try", str(now))

    text = None
    if now > float(await kv_get("trends_search_block") or 0):
        text = await _complete_gemini(TRENDS_SYSTEM, TRENDS_QUERY, tools=[{"google_search": {}}], max_tokens=700)
        if not text:
            # поиск недоступен (квота бесплатного тарифа и т.п.) — неделю не дёргаем его
            await kv_set("trends_search_block", str(now + 7 * 86400))
    if not text:
        text = await _complete_gemini(TRENDS_SYSTEM, TRENDS_QUERY, max_tokens=500)
    cleaned = _clean_trends(text) if text else ""
    if cleaned:
        _trends = cleaned
        await kv_set("trends_text", cleaned)
        await kv_set("trends_ts", str(now))
        log.info("Мемы дня обновлены: %s", cleaned)


# ───────── самопроизвольные язвительные реакции ─────────

# уровень → (вероятность реакции на сообщение, минимум секунд между реакциями в чате)
LEVELS = {
    "off": (0.0, 0),
    "low": (0.05, 300),
    "mid": (0.12, 120),
    "high": (0.25, 60),
}
LEVEL_TITLES = {"off": "😶 Выкл", "low": "😏 Редко", "mid": "😈 Средне", "high": "🔥 Часто"}
DEFAULT_LEVEL = "mid"
REACTIONS = ["🤡", "🤔", "🥱", "👀", "🤨", "🗿", "😁", "🔥", "🤓", "😏"]
EMOJI_SHARE = 0.35  # доля срабатываний, в которых бот просто ставит эмодзи-реакцию

SNARK = {
    "question": [
        "{n}, хороший вопрос, кстати. Чат, ну?",
        "Ооо, {n} философствует 🤔",
        "Интересно, а сам-то {n} что думает?",
        "Вот и мне интересно, {n}.",
    ],
    "later": [
        "«Потом» от {n} — звучит знакомо 😄",
        "{n}, «щас» это сколько по-вашему?",
        "Записал: {n} обещает. Напомню!",
        "Ловлю на слове, {n} 👀",
    ],
    "laugh": [
        "Хах, {n} в ударе 😄",
        "Ору с {n}.",
        "{n}, ну ты даёшь 😁",
    ],
    "caps": [
        "{n}, эмоции зашкаливают, да? 😅",
        "Слышу тебя, {n}! Громко и чётко.",
        "Кто-то сегодня на эмоциях, а, {n}?",
    ],
    "long": [
        "{n}, это целый роман! Кто-нибудь дочитал?",
        "Ого, {n} разошёлся. Дайте минуту, читаю 📖",
        "Много букв, {n}, но я старался.",
    ],
    "hello": [
        "О, {n}! Привет 👋",
        "{n}, здорово! Как сам?",
        "Привет-привет, {n}. Тебя тут не хватало.",
    ],
    "thanks": [
        "Обращайся, {n} 😉",
        "Всегда пожалуйста, {n}.",
    ],
    "generic": [
        "Хм, {n}, а мысль-то неплохая.",
        "{n} снова с интересным 👀",
        "Ну {n} даёт, конечно.",
        "Подпишусь, {n}. Почти.",
        "{n}, продолжай, мне нравится.",
        "Смело, {n}. Мне зашло.",
        "Кто-нибудь, переведите {n} с гения на русский 😄",
        "Так, {n}, а поподробнее?",
        "Любопытно, {n}. Чат, вы тут?",
        "{n} сегодня в ударе!",
    ],
}
_PATTERNS = [
    ("later", re.compile(r"\b(ща|щас|потом|завтра|позже|скоро буду|сейчас буду|попозже)\b", re.I)),
    ("laugh", re.compile(r"(ха){2,}|лол|ахах|\){3,}|😂|🤣", re.I)),
    ("hello", re.compile(r"^\s*(привет|здаров|хай|здравствуй|добр(ое|ый|ого))", re.I)),
    ("thanks", re.compile(r"\b(спасибо|спс|благодарю)\b", re.I)),
]

_levels: Dict[int, str] = {}
_last_snark: Dict[int, float] = {}


def pick_snark(text: str, name: str) -> str:
    """Подбирает язвительный ответ по характеру сообщения."""
    kind = "generic"
    if len(text) > 250:
        kind = "long"
    elif len(text) > 6 and text.isupper():
        kind = "caps"
    elif "?" in text:
        kind = "question"
    else:
        for key, pattern in _PATTERNS:
            if pattern.search(text):
                kind = key
                break
    return random.choice(SNARK[kind]).format(n=f"<b>{esc(name)}</b>")


async def get_level(chat_id: int) -> str:
    if chat_id not in _levels:
        saved = await kv_get(f"snark:{chat_id}")
        _levels[chat_id] = saved if saved in LEVELS else DEFAULT_LEVEL
    return _levels[chat_id]


async def maybe_snark(message: Message, bot: Bot, text: str) -> None:
    if message.chat.type not in ("group", "supergroup") or not message.from_user:
        return
    chance, gap = LEVELS[await get_level(message.chat.id)]
    if chance <= 0:
        return
    now = time.monotonic()
    if now - _last_snark.get(message.chat.id, 0) < gap or random.random() > chance:
        return
    _last_snark[message.chat.id] = now

    # часть срабатываний — просто эмодзи-реакция; для сообщений без текста — только она
    if not text.strip() or random.random() < EMOJI_SHARE:
        try:
            await bot.set_message_reaction(
                chat_id=message.chat.id,
                message_id=message.message_id,
                reaction=[ReactionTypeEmoji(emoji=random.choice(REACTIONS))],
            )
        except TelegramAPIError as exc:
            log.debug("Реакция не поставлена: %s", exc)
        return

    name = display_name(message.from_user)
    answer = await _ask_ai(message.chat.id, name, text[:300], snark=True)
    if answer is not None:
        if answer.strip().upper().startswith("SKIP"):
            return
        await reply_ai(message, answer)
    else:
        await message.reply(pick_snark(text, name))


def _snark_kb(current: str) -> InlineKeyboardMarkup:
    row = [
        InlineKeyboardButton(text=("✅ " if key == current else "") + title, callback_data=f"snark:{key}")
        for key, title in LEVEL_TITLES.items()
    ]
    return InlineKeyboardMarkup(inline_keyboard=[row[:2], row[2:]])


async def _is_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
    except TelegramAPIError:
        return False
    return member.status in ("creator", "administrator")




@router.callback_query(F.data.startswith("snark:"))
async def handle_snark_level(callback: CallbackQuery, bot: Bot) -> None:
    if not callback.data or not callback.message:
        await callback.answer()
        return
    level = callback.data.split(":", 1)[1]
    chat = callback.message.chat
    if level not in LEVELS:
        await callback.answer()
        return
    if chat.type != "private" and not await _is_admin(bot, chat.id, callback.from_user.id):
        await callback.answer("Менять это могут только админы чата.", show_alert=True)
        return
    await kv_set(f"snark:{chat.id}", level)
    _levels[chat.id] = level
    await callback.answer(LEVEL_TITLES[level])
    try:
        await callback.message.edit_text(
            f"😄 <b>Живые реакции:</b> {LEVEL_TITLES[level]}\n"
            f"Как часто я сам встреваю в разговор: колкой репликой или эмодзи. Менять могут админы чата.",
            reply_markup=_snark_kb(level),
        )
    except TelegramAPIError:
        pass


async def handle_private(message: Message, text: str) -> None:
    """Личная переписка: бот отвечает на любое сообщение, помня недавний диалог."""
    if not text.strip() or not message.from_user:
        return
    now = time.monotonic()
    if now - _last_reply.get(message.chat.id, 0) < 2:
        return
    _last_reply[message.chat.id] = now

    if not ai_enabled():
        await message.reply("🤖 Болтать я пока не умею — админу нужно подключить ИИ. Зато работают /menu и /app!")
        return
    name = display_name(message.from_user)
    history[message.chat.id].append(f"{name}: {text[:300]}")
    answer = await _ask_ai(message.chat.id, name, text, private=True)
    if answer:
        reply = await reply_ai(message, answer)
    else:
        reply = random.choice(FALLBACK)
        await message.reply(esc(reply))
    history[message.chat.id].append(f"Бот: {reply[:300]}")


@router.message()
async def handle_message(message: Message, bot: Bot) -> None:
    global _bot_username
    if not message.from_user or message.from_user.is_bot:
        return
    text = message.text or message.caption or ""
    if text.startswith("/"):
        return

    if message.chat.type == "private":
        await handle_private(message, text)
        return

    me = await bot.me()
    _bot_username = me.username or ""
    mentioned = bool(_bot_username) and f"@{_bot_username.lower()}" in text.lower()
    replied_to_bot = bool(
        message.reply_to_message
        and message.reply_to_message.from_user
        and message.reply_to_message.from_user.id == me.id
    )
    if not (mentioned or replied_to_bot):
        await maybe_snark(message, bot, text)
        return

    now = time.monotonic()
    if now - _last_reply.get(message.chat.id, 0) < AI_COOLDOWN:
        return
    _last_reply[message.chat.id] = now

    question = text.replace(f"@{_bot_username}", "").strip() or "скажи что-нибудь"
    answer = await _ask_ai(message.chat.id, display_name(message.from_user), question)
    if answer:
        reply = await reply_ai(message, answer)
    else:
        reply = random.choice(FALLBACK)
        await message.reply(esc(reply))
    history[message.chat.id].append(f"Бот: {reply[:200]}")  # чтобы диалог продолжался по контексту
