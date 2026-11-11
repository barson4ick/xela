"""Первые шаги новичка: одна цель за раз, без простыни из 30 кнопок."""

def _step(n: int, title: str, text: str, action: str, button: str) -> dict:
    return {"step": n, "total": 4, "title": title, "text": text, "action": action, "button": button, "done": False}


async def state(conn, chat_id: int, uid: int, p: dict, gv: dict) -> dict:
    mine = (gv or {}).get("mine")
    in_gang = bool(mine)
    in_police = bool(p.get("cop_rep") or p.get("agent"))
    worked = bool(p.get("work_sum") or p.get("work_day"))
    cred = int(p.get("street_cred", 0) or 0)
    if not worked and int(p.get("money", 0) or 0) < 300:
        return _step(1, "Первые деньги", "Сделай пару работ: на баланс упадёт первая тысяча, а энергия восстановится сама.", "earn", "К работе")
    if not in_gang and not in_police and cred < 10:
        return _step(2, "Уличный авторитет", f"До вступления в ОПГ нужно 10 авторитета. Сейчас {cred}/10. Чёрные дела — самый быстрый путь.", "dirty", "К чёрным делам")
    if not in_gang and not in_police:
        return _step(3, "Найди своих", "Авторитет есть. Теперь вступи в ОПГ или надень погоны — от этого зависит твой доход и роль.", "gang", "К ОПГ и полиции")
    if in_police and not p.get("cop_rep"):
        return _step(4, "Первая смена", "Выйди на смену: получишь зарплату, очки полиции и шанс найти улику.", "police", "На смену")
    return {"done": True, "step": 4, "total": 4}
