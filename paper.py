"""«Вечерний Район»: ежедневная ИИ-газета из событий города (дела, банды, войны, свадьбы, банк)."""
import logging

import rp
from database import kv_get, kv_set

log = logging.getLogger(__name__)

PROMPT = (
    "Ты — главный редактор городской газеты «Вечерний Район» в шуточной криминальной РП-игре друзей. "
    "Напиши свежий выпуск: яркий заголовок (одна строка, заглавными буквами), затем 4–6 коротких заметок-строк через перенос, "
    "в стиле жёлтой прессы: эмоционально, с юмором и лёгкими подколами, но без злобы и мата. В конце одна строка «Прогноз от Бабы Валя»: "
    "ехидный совет от бабушки с лавочки (она возглавляет ОПГ «Бабушкин синдикат»). "
    "Используй ТОЛЬКО факты ниже, ничего не выдумывай, имена оставь как есть, без markdown и списков с дефисами.\n\n"
)


async def build(chat_id: int) -> str | None:
    from handlers.chatai import _complete_gemini, ai_enabled, split_gif, system_prompt
    from utils import esc
    facts = await rp.news_facts(chat_id)
    if not facts:
        return None
    text = None
    if ai_enabled():
        try:
            raw = await _complete_gemini(system_prompt(), PROMPT + facts, max_tokens=700)
            text = split_gif(raw)[0] if raw else None
        except Exception as exc:
            log.warning("ИИ-газета не получилась: %r", exc)
    text = text or "\n".join(f.lstrip("- ") for f in facts.splitlines()[:5])
    return f"📰 <b>Вечерний Район</b>\n\n{esc(text)}"
