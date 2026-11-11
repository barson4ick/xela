"""Устав основателей: привилегии игровой банды «Законченные» (kv charter:{gid} = 1).

Работает на уровне gid, поэтому пережимает переименование. Хуки: gangwar (доход районов), gangplus5 (расходы, рост контроля),
police.case_add (дело), balance (налог общака), gang.heist (добыча налёта).
"""
import gangwar

INCOME_MULT = 1.25
COST_MULT = 0.7
CASE_MULT = 0.75
HEIST_MULT = 1.15

PERKS = [
    ("💰", "Доход районов +25%", "Каждый район платит участникам на четверть больше."),
    ("🧱", "Расходы на 30% ниже", "Занять район, укрепить его и перекрыть чужой маршрут стоит на 30% дешевле."),
    ("📈", "Контроль растёт быстрее", "На своих районах контроль растёт на 1 пункт быстрее каждые 30 минут."),
    ("🚓", "Полиция давит слабее", "Давление на районах спадает на 1 быстрее, а дело на банду растёт на 25% медленнее."),
    ("🏛", "Нет налога на общак", "Банда платит только содержание штаба: налог с общака с неё не берут."),
    ("🎯", "Налёт на 15% богаче", "Добыча общих налётов выше на 15%."),
]


async def chartered(conn, gid: int) -> bool:
    if not gid:
        return False
    return str(await gangwar.kget(conn, f"charter:{int(gid)}") or "") == "1"


async def cost(conn, gid: int, base: int) -> int:
    return int(base * COST_MULT) if await chartered(conn, gid) else int(base)


async def income_mult(conn, gid: int) -> float:
    return INCOME_MULT if await chartered(conn, gid) else 1.0


async def heist_mult(conn, gid: int) -> float:
    return HEIST_MULT if await chartered(conn, gid) else 1.0


async def info(conn, gid: int):
    if not await chartered(conn, gid):
        return None
    return {"title": "Устав основателей", "perks": [{"icon": i, "title": t, "desc": d} for i, t, d in PERKS]}
