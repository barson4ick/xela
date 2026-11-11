"""«Что делать сейчас»: персональные подсказки по состоянию игрока и личные уведомления о срочном.

build(state) — чистая функция поверх rp.state: список {id, icon, title, sub, go, gsub, urgent, rp}.
tick(bot) — раз в 10 минут шлёт в личку срочные пункты (не чаще 4 в сутки, без ночи, без повторов 6 ч).
"""
import logging
import time

from config import TZ

log = logging.getLogger(__name__)

DM_DAY_CAP = 4
DM_GAP = 10 * 60
DM_REPEAT = 6 * 3600
MAX_ITEMS = 5


def _dur(s) -> str:
    s = max(0, int(s or 0))
    if s >= 3600:
        return f"{s // 3600} ч {s % 3600 // 60} мин"
    return f"{max(1, s // 60)} мин"


def build(st: dict) -> list:
    out = []

    def add(id_, icon, title, sub, go="earn", gsub="", urgent=False, rp=""):
        out.append({"id": str(id_), "icon": icon, "title": title, "sub": sub, "go": go, "gsub": gsub, "urgent": bool(urgent), "rp": rp})

    me = st.get("me") or {}
    g = st.get("gang") or {}
    mine = g.get("mine") or {}
    pc = g.get("pc") or {}
    pol = g.get("pol") or {}
    pw = st.get("power") or {}
    world = (st.get("patrons") or {}).get("world") or {}
    cfg = st.get("config") or {}
    jailed = (me.get("jail_left") or 0) > 0
    dz = st.get("doom") or {}
    if dz.get("dead_me"):
        add("dead", "☠", "Твой персонаж погиб в Судный день", ("Твой персонаж погиб навсегда." if dz.get("perm_me") else f"Вернёшься с рассветом, ждать {_dur(dz.get('dead_left'))}.") + " Действия недоступны.",
            urgent=bool(not dz.get("perm_me") and int(dz.get("dead_left") or 0) > 18 * 3600))
        return out
    if dz.get("active"):
        ph = dz.get("phase") or {}
        add(f"doom:{ph.get('key')}", "☠", f"Судный день: «{ph.get('title', '')}»", f"{ph.get('desc', '')} До рассвета {_dur(dz.get('left'))}.", urgent=True)

    if jailed:
        add("jail", "🚔", f"Ты в тюрьме ещё {_dur(me['jail_left'])}", "Взятка, побег или тюремная работа — внизу экрана.", urgent=True)
    trial = (st.get("justice") or {}).get("trial") or {}
    if trial.get("is_me"):
        add("trial", "⚖️", f"Тебя судят: до приговора {_dur(trial.get('left'))}", "Адвокат, признание, конверт судье или сдать своего: у каждого выбора своя цена.", urgent=True)
    if pc.get("summon"):
        add(f"summon:{pc['summon'].get('id', '')}", "⚖️", "Тебя вызвали на допрос", f"На явку {_dur(pc['summon'].get('left'))}. Не придёшь — розыск и камера.", urgent=True)
    for x in ((world.get("tasks") or {}).get("incoming") or [])[:2]:
        add(f"task:{x.get('id', '')}", "📌", "Тебе дали задание", f"{x.get('title') or x.get('kind') or 'Задание'}: выполни сам, иначе уйдёт автоматом с худшим шансом.", urgent=True)
    if trial and not trial.get("is_me") and trial.get("can_witness"):
        add("witness", "⚖️", f"Идёт суд над {trial.get('name', 'игроком')}", "Можно дать показания против (+150 ₽) или алиби: это меняет приговор.")
    for x in (pc.get("debt_offers") or [])[:1]:
        add(f"debtoffer:{x.get('id')}", "📒", f"«{x.get('gang', '')}» предлагает заём {x.get('amount', 0)} ₽", f"Вернуть {x.get('owed', 0)} ₽ за {x.get('hours', 48)} ч. Согласись или откажись.")
    for x in (pc.get("my_debts") or [])[:1]:
        if x.get("late"):
            add(f"debtlate:{x.get('id')}", "📒", f"Долг «{x.get('gang', '')}» просрочен: {x.get('owed', 0)} ₽", "Коллекторы забирают наличные и тайник каждые 12 часов.", go="own", urgent=True)
        elif int(x.get("left") or 0) < 6 * 3600:
            add(f"debtdue:{x.get('id')}", "📒", f"Долг «{x.get('gang', '')}»: {x.get('owed', 0)} ₽", f"Вернуть через {_dur(x.get('left'))}, иначе придут коллекторы.", go="own", urgent=True)
    if pc.get("pol_lawyer"):
        add("pollawyer", "🎭", "Тебе предлагают «бесплатного адвоката»", "Свобода сейчас, но дело на банду вырастет.")
    if pc.get("prot_offer"):
        add("prot", "🤝", "Банда предлагает покровительство", "Неделя защиты от нападений ИИ-банд.")
    if pc.get("orders_in"):
        add("orders", "🥊", "Заказ на избиение ждёт ответа", "Деньги пойдут в общак.", go="gang")

    war = g.get("war")
    if war and mine:
        add(f"war:{war.get('id', '')}", "⚔️", "Твоя банда на войне", "Набирай очки на стрелке: победитель забирает район.", go="gang", gsub="war", urgent=True)

    if mine:
        p5 = mine.get("p5") or {}
        tmap = p5.get("map") or []
        my = [t for t in tmap if t.get("mine")]
        boss = bool(mine.get("is_boss"))
        tre = int(mine.get("treasury") or 0)
        if tmap and not my:
            add("noterr", "🏚", "У банды нет районов", "Без районов и точек банда развалится. Займи свободный район или выиграй войну.", go="map", urgent=True)
        weak = sorted((t for t in my if int(t.get("control") or 0) < 30), key=lambda t: t.get("control") or 0)
        if weak:
            t = weak[0]
            add(f"weak:{t['key']}", "📉", f"Район «{t['name']}» теряет контроль: {t.get('control', 0)}%", "Укрепи район или сбей давление полиции, иначе его отожмут.", go="map", urgent=True)
        hot = sorted((t for t in my if int(t.get("police") or 0) >= 60), key=lambda t: -(t.get("police") or 0))
        if hot:
            t = hot[0]
            add(f"hot:{t['key']}", "🚓", f"Полиция давит район «{t['name']}»: {t.get('police', 0)}%", "Подкупи патруль или купи крышу, пока контроль не упал.", go="map")
        dues = mine.get("dues") or {}
        if dues.get("overdue"):
            add("dues", "💸", f"Взнос в общак просрочен: {dues.get('amount', 0)} ₽", "Должников бьют и выгоняют.", go="gang", gsub="main", urgent=True)
        elif dues.get("can_pay") and 0 < int(dues.get("left") or 0) < 3 * 3600:
            add("dues", "💸", f"Взнос в общак {dues.get('amount', 0)} ₽", f"Осталось {_dur(dues.get('left'))}.", go="gang", gsub="main")
        coup = mine.get("coup") or {}
        if coup.get("can_vote") and not coup.get("my_vote"):
            add("coup", "👑", "В банде переворот: нужен твой голос", "Поддержи босса или претендента.", go="gang", gsub="main", urgent=True)
        if boss and my and not jailed:
            low = min(my, key=lambda t: t.get("control") or 0)
            if not p5.get("fort_wait") and tre >= int(p5.get("fort_cost") or 0) and int(low.get("control") or 0) < 80:
                add("fort", "🧱", f"Укрепи район «{low['name']}»", f"Контроль {low.get('control', 0)}% → +{p5.get('fort_gain', 18)} за {p5.get('fort_cost', 700)} ₽ из общака.", go="map")
            free = [t for t in tmap if t.get("can_claim")]
            if free and not p5.get("claim_wait") and tre >= int(p5.get("claim_cost") or 0):
                add("claim", "🏴", f"Свободный район «{free[0]['name']}» можно занять", f"{p5.get('claim_cost', 1500)} ₽ из общака: больше дохода и сырья.", go="map")
        if not jailed and not mine.get("heist_left") and (me.get("energy") or 0) >= (mine.get("heist_energy") or 0) and int(mine.get("player_count") or 0) + int(mine.get("ai_count") or 0) >= 2:
            add("heist", "💰", "Банда готова к налёту", f"Шанс {mine.get('heist_chance', 0)}%, добыча делится по ролям.", go="gang", gsub="ops")

    god = pw.get("godfather") or {}
    cand = god.get("candidate") or {}
    if cand.get("is_me") and not god.get("player"):
        add("godcand", "👑", "Ты кандидат в Крёстные отцы", god.get("why") or "Прими должность в «Город → Власть района».", go="town", urgent=bool(god.get("can_become")))
    elif god.get("can_become") and not cand:
        add("godcan", "👑", "Можно стать Крёстным отцом", f"Коронация {god.get('cost', 5000)} ₽: дань с банд и влияние на мэра.", go="town")
    if god.get("is_me"):
        tg = [x for x in (((pw.get("ctl") or {}).get("god") or {}).get("targets") or []) if not x.get("levy_wait") and int(x.get("treasury") or 0) >= 300]
        if tg:
            best = max(tg, key=lambda x: x.get("treasury") or 0)
            add("godlevy", "👑", f"Обложи данью «{best.get('name', '')}»", f"В общаке {best.get('treasury', 0)} ₽, дань 12%.", go="town")
    mayor = pw.get("mayor") or {}
    mg = pw.get("mg") or {}
    if (mg.get("war_until") or 0) > 0 and (mayor.get("is_me") or god.get("is_me")):
        add("mgwar", "⚔️", f"Война мэра и Крёстного: {mg.get('mayor_score', 0)}:{mg.get('god_score', 0)}", f"До конца {_dur(mg.get('war_until'))}. Рейды, бунты и дань приносят очки; победитель забирает кассу проигравшего.", go="town", urgent=True)
    if mayor.get("is_me") and not mayor.get("set_wait"):
        add("mayorset", "🎩", "Можно сменить курс мэрии", "За полицию, нейтральный или подкупной.", go="town")

    prop = ((pw.get("ctl") or {}).get("captain") or {}).get("proposal")
    if prop:
        add(f"capprop:{prop.get('op')}:{prop.get('gname')}", "📋", f"{prop.get('by', 'Капитан')} просит добро на {prop.get('title', 'операцию')} против «{prop.get('gname', '')}»",
            f"Дай добро (изъятие +{prop.get('bonus', 25)}%) или наложи вето. Через {_dur(prop.get('left'))} капитан решит сам.", go="town", urgent=True)
    res = st.get("res") or {}
    if res.get("eligible") and not jailed:
        if not res.get("home"):
            add("reshome", "🏘", "Выбери район, где живёшь", "Жители решают, кто сильнее в районе: банда или полиция. За поддержку платят.", go="town")
        elif not res.get("wait"):
            h = next((x for x in res.get("districts") or [] if x["key"] == res["home"]), None)
            if h and h.get("owner"):
                add("ressup", "🏘", f"Район «{h['name']}»: поддержи банду или полицию", f"Хозяин — «{h.get('owner_name', '')}». Твой голос двигает контроль, благодарность {res.get('reward', 80)} ₽.", go="town")
    role = pol.get("role") or ""
    if role and not jailed:
        if not pol.get("shift_wait"):
            add("shift", "🚓", f"Выйди на смену: +{pol.get('shift_pay', 0)} ₽", "Зарплата и репутация офицера.", go="police")
        elif (pol.get("patrols") or 0) > 0 and not pol.get("patrol_wait"):
            add("patrol", "🚓", "Патруль доступен", "Давит районы банд и ловит разыскиваемых.", go="police")

    el = (pc.get("council") or {}).get("election")
    if el:
        add("election", "🗳", "Идут выборы мэра", "Проголосуй, пока не закрыли участок.", go="town")
    vote = (g.get("p7") or {}).get("vote") or {}
    if vote and not vote.get("mine"):
        add("lawvote", "🗳", "Народный сход: выбери закон недели", f"Осталось {_dur(vote.get('left'))}.")

    try:
        stall = int(me.get("stall") or 0)
        cap = int((cfg.get("stalls") or [])[stall].get("income", 0)) * int(cfg.get("stall_hours") or 8) if stall else 0
        if cap and (me.get("stall_ready") or 0) >= cap * 0.6 and not jailed:
            add("stall", "🌯", f"Забери выручку ларька: {int(me['stall_ready'])} ₽", "Сверх лимита выручка не копится, а воры забирают часть.", go="own", rp="collect")
    except Exception:
        pass
    if not jailed and (me.get("energy") or 0) >= (me.get("max_energy") or 999) and not role:
        add("energy", "⚡", "Энергия полная и пропадает зря", "Иди на дело или на работу.", go="earn")

    out.sort(key=lambda x: not x["urgent"])
    return out[:MAX_ITEMS]


async def toggle(chat_id: int, uid: int, name: str) -> dict:
    import gangplus3 as P3
    import gangwar

    async def run(conn):
        key = f"dmoff:{chat_id}:{uid}"
        off = bool(await gangwar.kget(conn, key))
        await gangwar.kset(conn, key, "" if off else "1")
        return P3._res("🔔 Личные уведомления включены: бот напишет в личку только о срочном." if off else "🔕 Личные уведомления выключены.", True, 0)
    return await P3._act(chat_id, uid, run)


async def tick(bot) -> None:
    """Личные уведомления о срочном. Вызывается из jobs.run_jobs (процесс бота)."""
    import datetime

    import aiosqlite
    from aiogram.types import InlineKeyboardMarkup

    import rp
    from config import DB_PATH
    from database import kv_get, kv_set
    from keyboards import app_link_button

    t = int(time.time())
    if t - int(await kv_get("ntf:tick") or 0) < DM_GAP:
        return
    await kv_set("ntf:tick", str(t))
    local = datetime.datetime.now(TZ)
    if not 9 <= local.hour < 23:
        return
    day = local.strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute("SELECT chat_id, user_id FROM rp_player WHERE user_id > 0")
        players = await cur.fetchall()
    for chat_id, uid in players:
        try:
            if await kv_get(f"dmoff:{chat_id}:{uid}") or int(await kv_get(f"ntfx:{chat_id}:{uid}") or 0) > t:
                continue
            dkey = f"ntfd:{chat_id}:{uid}:{day}"
            sent = int(await kv_get(dkey) or 0)
            if sent >= DM_DAY_CAP:
                continue
            st = await rp.state(chat_id, uid)
            for it in st.get("todo") or []:
                if not it.get("urgent"):
                    continue
                ikey = f"ntf:{chat_id}:{uid}:{it['id']}"
                if t - int(await kv_get(ikey) or 0) < DM_REPEAT:
                    continue
                await kv_set(ikey, str(t))
                try:
                    import html as _h
                    await bot.send_message(uid, f"{it['icon']} <b>{_h.escape(it['title'])}</b>\n{_h.escape(it['sub'])}\n\n<i>Отключить: Район → блок «Сейчас» → 🔕</i>",
                                           reply_markup=InlineKeyboardMarkup(inline_keyboard=[[app_link_button("📱 Открыть Район", "rp", chat_id)]]))
                    sent += 1
                    await kv_set(dkey, str(sent))
                except Exception as e:
                    # Игрок не открывал личку с ботом или заблокировал его: сутки не пробуем.
                    await kv_set(f"ntfx:{chat_id}:{uid}", str(t + 86400))
                    log.info("ЛС %s недоступна: %s", uid, type(e).__name__)
                break
        except Exception:
            log.exception("guide.tick %s/%s", chat_id, uid)
