import random
import time

import aiosqlite
from typing import Optional, List, Tuple
from config import DB_PATH


async def init_db() -> None:
    """Инициализация таблицы users с составным первичным ключом (user_id, chat_id)."""
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        # WAL: бот и мини-апп пишут в одну базу одновременно
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                size INTEGER NOT NULL DEFAULT 0,
                rep INTEGER NOT NULL DEFAULT 0,
                last_roll INTEGER NOT NULL DEFAULT 0,
                last_rob INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, chat_id)
            )
            """
        )
        # Миграция: новые колонки в существующей таблице users
        cursor = await db.execute("PRAGMA table_info(users)")
        cols = {r[1] for r in await cursor.fetchall()}
        for name, ddl in (
            ("last_daily", "TEXT NOT NULL DEFAULT ''"),
            ("streak", "INTEGER NOT NULL DEFAULT 0"),
            ("title", "TEXT NOT NULL DEFAULT ''"),
            ("title_until", "INTEGER NOT NULL DEFAULT 0"),
            ("emoji", "TEXT NOT NULL DEFAULT ''"),
            ("last_drip", "INTEGER NOT NULL DEFAULT 0"),
        ):
            if name not in cols:
                await db.execute(f"ALTER TABLE users ADD COLUMN {name} {ddl}")

        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS msg_stats (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                day TEXT NOT NULL,
                cnt INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (chat_id, user_id, day)
            );
            CREATE TABLE IF NOT EXISTS quotes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                author_id INTEGER NOT NULL,
                author_name TEXT NOT NULL,
                text TEXT NOT NULL,
                added_by INTEGER NOT NULL,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS heroes (
                chat_id INTEGER NOT NULL,
                day TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                PRIMARY KEY (chat_id, day)
            );
            CREATE TABLE IF NOT EXISTS size_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                ts INTEGER NOT NULL,
                delta INTEGER NOT NULL,
                reason TEXT NOT NULL,
                size_after INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_size_log_user ON size_log (chat_id, user_id, ts);
            CREATE TABLE IF NOT EXISTS lottery (
                chat_id INTEGER NOT NULL,
                day TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                tickets INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (chat_id, day, user_id)
            );
            CREATE TABLE IF NOT EXISTS daily_q (
                chat_id INTEGER NOT NULL,
                day TEXT NOT NULL,
                message_id INTEGER NOT NULL,
                question TEXT NOT NULL,
                summarized INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (chat_id, day)
            );
            CREATE TABLE IF NOT EXISTS daily_answers (
                chat_id INTEGER NOT NULL,
                day TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                text TEXT NOT NULL,
                PRIMARY KEY (chat_id, day, user_id)
            );
            CREATE TABLE IF NOT EXISTS quest_claims (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                day TEXT NOT NULL,
                qid TEXT NOT NULL,
                PRIMARY KEY (chat_id, user_id, day, qid)
            );
            CREATE TABLE IF NOT EXISTS boss (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                emoji TEXT NOT NULL,
                max_hp INTEGER NOT NULL,
                hp INTEGER NOT NULL,
                spawned INTEGER NOT NULL,
                ends INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'alive',
                killer INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_boss_chat ON boss (chat_id, id);
            CREATE TABLE IF NOT EXISTS boss_dmg (
                boss_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                day TEXT NOT NULL,
                hits INTEGER NOT NULL DEFAULT 0,
                dmg INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (boss_id, user_id, day)
            );
            CREATE TABLE IF NOT EXISTS trivia_bank (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                h TEXT NOT NULL UNIQUE,
                q TEXT NOT NULL,
                ans TEXT NOT NULL,
                wrong TEXT NOT NULL,
                created INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS trivia_seen (
                chat_id INTEGER NOT NULL,
                h TEXT NOT NULL,
                ts INTEGER NOT NULL,
                PRIMARY KEY (chat_id, h)
            );
            CREATE TABLE IF NOT EXISTS trivia_wins (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                day TEXT NOT NULL,
                cnt INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (chat_id, user_id, day)
            );
            CREATE TABLE IF NOT EXISTS cmd_stats (
                day TEXT NOT NULL,
                cmd TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                cnt INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (day, cmd, user_id)
            );
            CREATE TABLE IF NOT EXISTS rp_player (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                money INTEGER NOT NULL DEFAULT 150,
                energy REAL NOT NULL DEFAULT 100,
                energy_ts INTEGER NOT NULL DEFAULT 0,
                wanted INTEGER NOT NULL DEFAULT 0,
                wanted_ts INTEGER NOT NULL DEFAULT 0,
                jail_until INTEGER NOT NULL DEFAULT 0,
                car INTEGER NOT NULL DEFAULT 0,
                home INTEGER NOT NULL DEFAULT 0,
                stall INTEGER NOT NULL DEFAULT 0,
                collect_ts INTEGER NOT NULL DEFAULT 0,
                exch_day TEXT NOT NULL DEFAULT '',
                exch_cm INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (chat_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS rp_news (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                ts INTEGER NOT NULL,
                text TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_rp_news ON rp_news (chat_id, id);
            CREATE TABLE IF NOT EXISTS gang (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL, name TEXT NOT NULL, boss INTEGER NOT NULL,
                created INTEGER NOT NULL, last_heist INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS gang_member (
                chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL, gang_id INTEGER NOT NULL, joined INTEGER NOT NULL,
                PRIMARY KEY (chat_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS gang_invite (
                gang_id INTEGER NOT NULL, user_id INTEGER NOT NULL, chat_id INTEGER NOT NULL,
                invited_by INTEGER NOT NULL, ts INTEGER NOT NULL,
                PRIMARY KEY (gang_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS gang_war (
                id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL,
                a INTEGER NOT NULL, b INTEGER NOT NULL, started INTEGER NOT NULL, ends INTEGER NOT NULL,
                score_a INTEGER NOT NULL DEFAULT 0, score_b INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'active', winner INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS territory (
                chat_id INTEGER NOT NULL, key TEXT NOT NULL, owner INTEGER NOT NULL DEFAULT 0,
                last_pay INTEGER NOT NULL DEFAULT 0,
                control INTEGER NOT NULL DEFAULT 50, police INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (chat_id, key)
            );
            CREATE TABLE IF NOT EXISTS gang_betray (
                id INTEGER PRIMARY KEY AUTOINCREMENT, gang_id INTEGER NOT NULL, chat_id INTEGER NOT NULL,
                uid INTEGER NOT NULL, ts INTEGER NOT NULL, resolved INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS ai_citizen (
                chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL, name TEXT NOT NULL,
                icon TEXT NOT NULL DEFAULT '🧑', kind TEXT NOT NULL DEFAULT 'citizen',
                money INTEGER NOT NULL DEFAULT 0, cred INTEGER NOT NULL DEFAULT 0,
                last_act INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (chat_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, text TEXT NOT NULL,
                voice INTEGER NOT NULL DEFAULT 0, ts INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS bank_raid (
                id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, msg_id INTEGER NOT NULL DEFAULT 0,
                hp INTEGER NOT NULL, max_hp INTEGER NOT NULL, alert INTEGER NOT NULL DEFAULT 0,
                started INTEGER NOT NULL, ends INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'active',
                day TEXT NOT NULL, note TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS bank_hit (
                raid_id INTEGER NOT NULL, user_id INTEGER NOT NULL, name TEXT NOT NULL,
                hits INTEGER NOT NULL DEFAULT 0, dmg INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (raid_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS marriage (
                chat_id INTEGER NOT NULL, a INTEGER NOT NULL, b INTEGER NOT NULL, ts INTEGER NOT NULL,
                joint INTEGER NOT NULL DEFAULT 0, last_kid INTEGER NOT NULL DEFAULT 0, last_accr INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (chat_id, a, b)
            );
            CREATE TABLE IF NOT EXISTS family_kid (
                id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, a INTEGER NOT NULL, b INTEGER NOT NULL,
                name TEXT NOT NULL, icon TEXT NOT NULL, born INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS mission (
                day TEXT NOT NULL, chat_id INTEGER NOT NULL, uid INTEGER NOT NULL, name TEXT NOT NULL,
                kind TEXT NOT NULL, arg TEXT NOT NULL DEFAULT '', need INTEGER NOT NULL DEFAULT 1,
                seen TEXT NOT NULL DEFAULT '[]', failed INTEGER NOT NULL DEFAULT 0, ts INTEGER NOT NULL,
                PRIMARY KEY (day, chat_id, uid)
            );
            CREATE TABLE IF NOT EXISTS rel (
                chat_id INTEGER NOT NULL, a INTEGER NOT NULL, b INTEGER NOT NULL,
                pts INTEGER NOT NULL DEFAULT 0, last_ts INTEGER NOT NULL DEFAULT 0,
                day TEXT NOT NULL DEFAULT '', day_pts INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (chat_id, a, b)
            );
            CREATE TABLE IF NOT EXISTS dispute (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL, msg_id INTEGER NOT NULL DEFAULT 0,
                a_id INTEGER NOT NULL, a_name TEXT NOT NULL, b_id INTEGER NOT NULL, b_name TEXT NOT NULL,
                text TEXT NOT NULL, stake INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'offer',
                created INTEGER NOT NULL, votes TEXT NOT NULL DEFAULT '{}', winner INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS prediction (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL, msg_id INTEGER NOT NULL DEFAULT 0,
                author_id INTEGER NOT NULL, author_name TEXT NOT NULL, text TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open', created INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS prediction_vote (
                pid INTEGER NOT NULL, user_id INTEGER NOT NULL, name TEXT NOT NULL, side TEXT NOT NULL,
                PRIMARY KEY (pid, user_id)
            );
            CREATE TABLE IF NOT EXISTS trial (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL, msg_id INTEGER NOT NULL DEFAULT 0,
                plaintiff_id INTEGER NOT NULL, plaintiff_name TEXT NOT NULL,
                defendant_id INTEGER NOT NULL, defendant_name TEXT NOT NULL,
                text TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open', created INTEGER NOT NULL,
                votes TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS highlight (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL, ts INTEGER NOT NULL, icon TEXT NOT NULL, text TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_highlight_chat ON highlight (chat_id, ts);
            CREATE TABLE IF NOT EXISTS cosmetic (
                chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
                frame TEXT NOT NULL DEFAULT '', bg TEXT NOT NULL DEFAULT '', owned TEXT NOT NULL DEFAULT '[]',
                PRIMARY KEY (chat_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS cq_prog (
                chat_id INTEGER NOT NULL, day TEXT NOT NULL, user_id INTEGER NOT NULL, n INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (chat_id, day, user_id)
            );
            CREATE TABLE IF NOT EXISTS pet (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                born INTEGER NOT NULL,
                food REAL NOT NULL DEFAULT 70,
                fun REAL NOT NULL DEFAULT 70,
                rest REAL NOT NULL DEFAULT 70,
                ts INTEGER NOT NULL,
                away INTEGER NOT NULL DEFAULT 0,
                starve_ts INTEGER NOT NULL DEFAULT 0,
                cd TEXT NOT NULL DEFAULT '{}',
                cares INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (chat_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS poker_escrow (
                tid TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                chips INTEGER NOT NULL,
                PRIMARY KEY (tid, user_id)
            );
            CREATE TABLE IF NOT EXISTS bar_player (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                level REAL NOT NULL DEFAULT 0,
                ts INTEGER NOT NULL DEFAULT 0,
                peak REAL NOT NULL DEFAULT 0,
                hang_until INTEGER NOT NULL DEFAULT 0,
                out_until INTEGER NOT NULL DEFAULT 0,
                opo_until INTEGER NOT NULL DEFAULT 0,
                tipsy_until INTEGER NOT NULL DEFAULT 0,
                drinks INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (chat_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS kv (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        cursor = await db.execute("PRAGMA table_info(gang)")
        have_g = {r[1] for r in await cursor.fetchall()}
        for col in ("is_ai", "wins", "losses"):
            if col not in have_g:
                await db.execute(f"ALTER TABLE gang ADD COLUMN {col} INTEGER NOT NULL DEFAULT 0")
        cursor = await db.execute("PRAGMA table_info(gang_member)")
        if "role" not in {r[1] for r in await cursor.fetchall()}:
            await db.execute("ALTER TABLE gang_member ADD COLUMN role TEXT NOT NULL DEFAULT 'grunt'")
            await db.execute("UPDATE gang_member SET role = 'boss' WHERE user_id = (SELECT boss FROM gang WHERE gang.id = gang_member.gang_id)")
        cursor = await db.execute("PRAGMA table_info(bar_player)")
        if "tipsy_until" not in {r[1] for r in await cursor.fetchall()}:
            await db.execute("ALTER TABLE bar_player ADD COLUMN tipsy_until INTEGER NOT NULL DEFAULT 0")
        cursor = await db.execute("PRAGMA table_info(rp_player)")
        if "esc_used" not in {r[1] for r in await cursor.fetchall()}:
            await db.execute("ALTER TABLE rp_player ADD COLUMN esc_used INTEGER NOT NULL DEFAULT 0")
        cursor = await db.execute("PRAGMA table_info(rp_player)")
        have = {r[1] for r in await cursor.fetchall()}
        for col, ddl in (("job_key", "TEXT NOT NULL DEFAULT ''"), ("job_rep", "INTEGER NOT NULL DEFAULT 0"),
                         ("job_ts", "INTEGER NOT NULL DEFAULT 0"), ("work_day", "TEXT NOT NULL DEFAULT ''"),
                         ("work_sum", "INTEGER NOT NULL DEFAULT 0"), ("street_cred", "INTEGER NOT NULL DEFAULT 0")):
            if col not in have:
                await db.execute(f"ALTER TABLE rp_player ADD COLUMN {col} {ddl}")
        cursor = await db.execute("PRAGMA table_info(rp_player)")
        have_rp = {r[1] for r in await cursor.fetchall()}
        for col, ddl in (("char_icon", "TEXT NOT NULL DEFAULT ''"), ("char_hat", "TEXT NOT NULL DEFAULT ''"),
                         ("char_acc", "TEXT NOT NULL DEFAULT ''"), ("char_bio", "TEXT NOT NULL DEFAULT ''")):
            if col not in have_rp:
                await db.execute(f"ALTER TABLE rp_player ADD COLUMN {col} {ddl}")
        cursor = await db.execute("PRAGMA table_info(rp_player)")
        have_law = {r[1] for r in await cursor.fetchall()}
        for col, ddl in (("weapon", "INTEGER NOT NULL DEFAULT 0"), ("guard", "INTEGER NOT NULL DEFAULT 0")):
            if col not in have_law:
                try:
                    await db.execute(f"ALTER TABLE rp_player ADD COLUMN {col} {ddl}")
                except Exception:
                    pass
        cursor = await db.execute("PRAGMA table_info(gang_member)")
        have_gm = {r[1] for r in await cursor.fetchall()}
        if "spec" not in have_gm:
            try:
                await db.execute("ALTER TABLE gang_member ADD COLUMN spec TEXT NOT NULL DEFAULT ''")
            except Exception:
                pass
        if "lawyer_until" not in have_law:
            try:
                await db.execute("ALTER TABLE rp_player ADD COLUMN lawyer_until INTEGER NOT NULL DEFAULT 0")
            except Exception:  # второй процесс успел раньше
                pass
        cursor = await db.execute("PRAGMA table_info(pet)")
        have_pet = {r[1] for r in await cursor.fetchall()}
        for col, ddl in (("diary", "TEXT NOT NULL DEFAULT '[]'"), ("diary_ts", "INTEGER NOT NULL DEFAULT 0"),
                         ("stage_n", "INTEGER NOT NULL DEFAULT 0")):
            if col not in have_pet:
                await db.execute(f"ALTER TABLE pet ADD COLUMN {col} {ddl}")
        # ОПГ-плюс: общак, авторитет, разборки, дело о крысе
        for table, cols in (
            ("gang", (("treasury", "INTEGER NOT NULL DEFAULT 0"), ("last_act", "INTEGER NOT NULL DEFAULT 0"), ("respect", "INTEGER NOT NULL DEFAULT 0"), ("fear", "INTEGER NOT NULL DEFAULT 0"), ("emblem", "TEXT NOT NULL DEFAULT ''"), ("color1", "TEXT NOT NULL DEFAULT ''"), ("color2", "TEXT NOT NULL DEFAULT ''"), ("motto", "TEXT NOT NULL DEFAULT ''"), ("heir", "INTEGER NOT NULL DEFAULT 0"), ("legacy", "INTEGER NOT NULL DEFAULT 0"), ("street", "TEXT NOT NULL DEFAULT ''"))),
            ("gang_member", (("merit", "INTEGER NOT NULL DEFAULT 0"),)),
            ("gang_betray", (("reward", "INTEGER NOT NULL DEFAULT 0"), ("leaks", "INTEGER NOT NULL DEFAULT 0"),
                             ("last_leak", "INTEGER NOT NULL DEFAULT 0"), ("attempts", "INTEGER NOT NULL DEFAULT 0"),
                             ("framed", "INTEGER NOT NULL DEFAULT 0"), ("frame_fell", "INTEGER NOT NULL DEFAULT 0"))),
        ):
            cursor = await db.execute(f"PRAGMA table_info({table})")
            have_t = {r[1] for r in await cursor.fetchall()}
            for col, ddl in cols:
                if col not in have_t:
                    await db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
        # Стратегический слой: контроль района и полицейское давление
        cursor = await db.execute("PRAGMA table_info(territory)")
        have_t = {r[1] for r in await cursor.fetchall()}
        for col, ddl in (("control", "INTEGER NOT NULL DEFAULT 50"), ("police", "INTEGER NOT NULL DEFAULT 0")):
            if col not in have_t:
                try:
                    await db.execute(f"ALTER TABLE territory ADD COLUMN {col} {ddl}")
                except Exception:
                    pass
        await db.execute("""CREATE TABLE IF NOT EXISTS gang_coup (
            id INTEGER PRIMARY KEY AUTOINCREMENT, gang_id INTEGER NOT NULL, chat_id INTEGER NOT NULL, challenger INTEGER NOT NULL,
            deposit INTEGER NOT NULL, started INTEGER NOT NULL, ends INTEGER NOT NULL, votes TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'open')""")
        await db.execute("""CREATE TABLE IF NOT EXISTS rat_clue (
            id INTEGER PRIMARY KEY AUTOINCREMENT, case_id INTEGER NOT NULL, idx INTEGER NOT NULL, attr TEXT NOT NULL,
            value TEXT NOT NULL, text TEXT NOT NULL, fake INTEGER NOT NULL DEFAULT 0, subject INTEGER NOT NULL DEFAULT 0,
            unlock_ts INTEGER NOT NULL DEFAULT 0)""")
        # ОПГ-плюс 2: взносы, заговоры, роли в налёте, допрос, раскаяние, сходки, недельный итог, двойной агент
        for table, cols in (
            ("rp_player", (("beaten_until", "INTEGER NOT NULL DEFAULT 0"), ("cop_rep", "INTEGER NOT NULL DEFAULT 0"),
                           ("agent", "INTEGER NOT NULL DEFAULT 0"), ("agent_ts", "INTEGER NOT NULL DEFAULT 0"))),
            ("gang_member", (("dues_due", "INTEGER NOT NULL DEFAULT 0"), ("probation_until", "INTEGER NOT NULL DEFAULT 0"))),
            ("gang_betray", (("interrogations", "INTEGER NOT NULL DEFAULT 0"), ("last_interrogation", "INTEGER NOT NULL DEFAULT 0"))),
            ("gang_coup", (("kind", "TEXT NOT NULL DEFAULT 'merit'"),)),
        ):
            cursor = await db.execute(f"PRAGMA table_info({table})")
            have_t = {r[1] for r in await cursor.fetchall()}
            for col, ddl in cols:
                if col not in have_t:
                    await db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
        for ddl in (
            """CREATE TABLE IF NOT EXISTS gang_plot (id INTEGER PRIMARY KEY AUTOINCREMENT, gang_id INTEGER NOT NULL, chat_id INTEGER NOT NULL,
               plotter INTEGER NOT NULL, method TEXT NOT NULL, step INTEGER NOT NULL DEFAULT 0, started INTEGER NOT NULL,
               next_ts INTEGER NOT NULL DEFAULT 0, data TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'active')""",
            """CREATE TABLE IF NOT EXISTS gang_rheist (id INTEGER PRIMARY KEY AUTOINCREMENT, gang_id INTEGER NOT NULL, chat_id INTEGER NOT NULL,
               created INTEGER NOT NULL, ends INTEGER NOT NULL, planner INTEGER NOT NULL DEFAULT 0, cracker INTEGER NOT NULL DEFAULT 0,
               lookout INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'plan')""",
            """CREATE TABLE IF NOT EXISTS gang_repent (id INTEGER PRIMARY KEY AUTOINCREMENT, gang_id INTEGER NOT NULL, chat_id INTEGER NOT NULL,
               uid INTEGER NOT NULL, name TEXT NOT NULL DEFAULT '', ts INTEGER NOT NULL, paid INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'open')""",
            """CREATE TABLE IF NOT EXISTS gang_meet (id INTEGER PRIMARY KEY AUTOINCREMENT, gang_id INTEGER NOT NULL, chat_id INTEGER NOT NULL,
               created INTEGER NOT NULL, ends INTEGER NOT NULL, attendees TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'open')""",
            """CREATE TABLE IF NOT EXISTS gang_log (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, gang_id INTEGER NOT NULL,
               uid INTEGER NOT NULL, kind TEXT NOT NULL, amount INTEGER NOT NULL DEFAULT 0, ts INTEGER NOT NULL)""",
            "CREATE INDEX IF NOT EXISTS idx_gang_log ON gang_log (gang_id, ts)",
            """CREATE TABLE IF NOT EXISTS gang_pact (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, a INTEGER NOT NULL, b INTEGER NOT NULL,
               status TEXT NOT NULL DEFAULT 'proposed', created INTEGER NOT NULL, ends INTEGER NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS rp_attack (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, attacker INTEGER NOT NULL,
               kind TEXT NOT NULL, tkind TEXT NOT NULL, target INTEGER NOT NULL, terr TEXT NOT NULL DEFAULT '', price INTEGER NOT NULL DEFAULT 0,
               created INTEGER NOT NULL, expires INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'open')""",
            """CREATE TABLE IF NOT EXISTS gang_watch (gang_id INTEGER NOT NULL, uid INTEGER NOT NULL, until INTEGER NOT NULL, PRIMARY KEY (gang_id, uid))""",
            """CREATE TABLE IF NOT EXISTS gang_pot (gang_id INTEGER NOT NULL, week TEXT NOT NULL, total INTEGER NOT NULL DEFAULT 0, done INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (gang_id, week))""",
            """CREATE TABLE IF NOT EXISTS gang_tourney (gang_id INTEGER NOT NULL, week TEXT NOT NULL, uid INTEGER NOT NULL, pts INTEGER NOT NULL DEFAULT 0, fights INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (gang_id, week, uid))""",
            """CREATE TABLE IF NOT EXISTS gang_stock (gang_id INTEGER NOT NULL, res TEXT NOT NULL, qty INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (gang_id, res))""",
            """CREATE TABLE IF NOT EXISTS gang_deal (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, a INTEGER NOT NULL, b INTEGER NOT NULL,
               kind TEXT NOT NULL, item TEXT NOT NULL DEFAULT '', qty INTEGER NOT NULL DEFAULT 0, price INTEGER NOT NULL DEFAULT 0, created INTEGER NOT NULL,
               expires INTEGER NOT NULL, ends INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'open')""",
            """CREATE TABLE IF NOT EXISTS gang_op (id INTEGER PRIMARY KEY AUTOINCREMENT, gang_id INTEGER NOT NULL, chat_id INTEGER NOT NULL, kind TEXT NOT NULL,
               stage INTEGER NOT NULL DEFAULT 0, quality REAL NOT NULL DEFAULT 0, doers TEXT NOT NULL DEFAULT '[]', started INTEGER NOT NULL, ends INTEGER NOT NULL,
               status TEXT NOT NULL DEFAULT 'active')""",
            """CREATE TABLE IF NOT EXISTS gang_mole (id INTEGER PRIMARY KEY AUTOINCREMENT, gang_id INTEGER NOT NULL, chat_id INTEGER NOT NULL, ai_id INTEGER NOT NULL,
               planted INTEGER NOT NULL, found INTEGER NOT NULL DEFAULT 0, last_leak INTEGER NOT NULL DEFAULT 0, leaks INTEGER NOT NULL DEFAULT 0,
               status TEXT NOT NULL DEFAULT 'active')""",
            """CREATE TABLE IF NOT EXISTS live_event (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, kind TEXT NOT NULL, started INTEGER NOT NULL,
               ends INTEGER NOT NULL, progress INTEGER NOT NULL DEFAULT 0, goal INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'open', msg_id INTEGER NOT NULL DEFAULT 0)""",
            """CREATE TABLE IF NOT EXISTS live_event_part (event_id INTEGER NOT NULL, user_id INTEGER NOT NULL, n INTEGER NOT NULL DEFAULT 0, amount INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (event_id, user_id))""",
            """CREATE TABLE IF NOT EXISTS law_vote (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, week TEXT NOT NULL, opens INTEGER NOT NULL, closes INTEGER NOT NULL,
               status TEXT NOT NULL DEFAULT 'open', result TEXT NOT NULL DEFAULT '', msg_id INTEGER NOT NULL DEFAULT 0)""",
            """CREATE TABLE IF NOT EXISTS law_ballot (vote_id INTEGER NOT NULL, user_id INTEGER NOT NULL, law TEXT NOT NULL, PRIMARY KEY (vote_id, user_id))""",
            """CREATE TABLE IF NOT EXISTS flat (chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL, theme TEXT NOT NULL DEFAULT 'gray', allow_steal INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (chat_id, user_id))""",
            """CREATE TABLE IF NOT EXISTS flat_item (chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL, slot INTEGER NOT NULL, item TEXT NOT NULL, ts INTEGER NOT NULL, PRIMARY KEY (chat_id, user_id, slot))""",
            """CREATE TABLE IF NOT EXISTS flat_note (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, owner INTEGER NOT NULL, author INTEGER NOT NULL, text TEXT NOT NULL, ts INTEGER NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS flat_visit (chat_id INTEGER NOT NULL, owner INTEGER NOT NULL, visitor INTEGER NOT NULL, day TEXT NOT NULL, PRIMARY KEY (chat_id, owner, visitor, day))""",
            """CREATE TABLE IF NOT EXISTS contract (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL, day TEXT NOT NULL,
               kind TEXT NOT NULL, rkind TEXT NOT NULL, reward INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'offer', ends INTEGER NOT NULL DEFAULT 0, npc INTEGER NOT NULL DEFAULT 0)""",
            """CREATE TABLE IF NOT EXISTS brigade (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, gang_id INTEGER NOT NULL, role TEXT NOT NULL, name TEXT NOT NULL,
               nick TEXT NOT NULL DEFAULT '', trait TEXT NOT NULL DEFAULT '', level INTEGER NOT NULL DEFAULT 1, loyalty INTEGER NOT NULL DEFAULT 50, status TEXT NOT NULL DEFAULT 'free',
               busy_until INTEGER NOT NULL DEFAULT 0, task TEXT NOT NULL DEFAULT '', injured_until INTEGER NOT NULL DEFAULT 0, hired INTEGER NOT NULL DEFAULT 0)""",
            """CREATE TABLE IF NOT EXISTS racket (chat_id INTEGER NOT NULL, key TEXT NOT NULL, owner INTEGER NOT NULL DEFAULT 0, greed INTEGER NOT NULL DEFAULT 2,
               pending INTEGER NOT NULL DEFAULT 0, last_ts INTEGER NOT NULL DEFAULT 0, rebel_until INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (chat_id, key))""",
            """CREATE TABLE IF NOT EXISTS debt (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, gang_id INTEGER NOT NULL, debtor INTEGER NOT NULL,
               principal INTEGER NOT NULL, owed INTEGER NOT NULL, due INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'open', created INTEGER NOT NULL, fined INTEGER NOT NULL DEFAULT 0)""",
            """CREATE TABLE IF NOT EXISTS gang_hall (id INTEGER PRIMARY KEY AUTOINCREMENT, gang_id INTEGER NOT NULL, chat_id INTEGER NOT NULL, kind TEXT NOT NULL,
               uid INTEGER NOT NULL DEFAULT 0, name TEXT NOT NULL DEFAULT '', text TEXT NOT NULL DEFAULT '', ts INTEGER NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS gang_rule (gang_id INTEGER NOT NULL, idx INTEGER NOT NULL, text TEXT NOT NULL, PRIMARY KEY (gang_id, idx))""",
            """CREATE TABLE IF NOT EXISTS gang_trial (id INTEGER PRIMARY KEY AUTOINCREMENT, gang_id INTEGER NOT NULL, chat_id INTEGER NOT NULL, accuser INTEGER NOT NULL,
               defendant INTEGER NOT NULL, rule INTEGER NOT NULL, evidence TEXT NOT NULL DEFAULT '', created INTEGER NOT NULL, ends INTEGER NOT NULL,
               status TEXT NOT NULL DEFAULT 'open', votes TEXT NOT NULL DEFAULT '{}')""",
            """CREATE TABLE IF NOT EXISTS season_ctl (chat_id INTEGER NOT NULL, season TEXT NOT NULL, gang_id INTEGER NOT NULL, pts INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (chat_id, season, gang_id))""",
            """CREATE TABLE IF NOT EXISTS defect (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, from_gang INTEGER NOT NULL, to_user INTEGER NOT NULL,
               old_gang INTEGER NOT NULL, offer INTEGER NOT NULL DEFAULT 0, role TEXT NOT NULL DEFAULT 'grunt', status TEXT NOT NULL DEFAULT 'open', expires INTEGER NOT NULL,
               loyalty INTEGER NOT NULL DEFAULT 50, created INTEGER NOT NULL, bounty INTEGER NOT NULL DEFAULT 0)""",
            """CREATE TABLE IF NOT EXISTS jail_cig (chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL, cigs INTEGER NOT NULL DEFAULT 0, rep INTEGER NOT NULL DEFAULT 0,
               work_ts INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (chat_id, user_id))""",
            """CREATE TABLE IF NOT EXISTS council_vote (chat_id INTEGER NOT NULL, week TEXT NOT NULL, voter INTEGER NOT NULL, cand INTEGER NOT NULL, PRIMARY KEY (chat_id, week, voter))""",
            """CREATE TABLE IF NOT EXISTS peace (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, a INTEGER NOT NULL, b INTEGER NOT NULL, kind TEXT NOT NULL,
               amount INTEGER NOT NULL DEFAULT 0, key_a TEXT NOT NULL DEFAULT '', key_b TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'open', expires INTEGER NOT NULL, ends INTEGER NOT NULL DEFAULT 0)""",
            """CREATE TABLE IF NOT EXISTS police_case (chat_id INTEGER NOT NULL, gang_id INTEGER NOT NULL, pts REAL NOT NULL DEFAULT 0, wiretap_until INTEGER NOT NULL DEFAULT 0,
               updated INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (chat_id, gang_id))""",
            """CREATE TABLE IF NOT EXISTS police_force (chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL, rank TEXT NOT NULL DEFAULT 'officer', joined INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (chat_id, user_id))""",
            """CREATE TABLE IF NOT EXISTS police_bribe (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, gang_id INTEGER NOT NULL, amount INTEGER NOT NULL,
               status TEXT NOT NULL DEFAULT 'open', created INTEGER NOT NULL, expires INTEGER NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS police_alert (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, gang_id INTEGER NOT NULL DEFAULT 0, kind TEXT NOT NULL, text TEXT NOT NULL, ts INTEGER NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS police_summon (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, target INTEGER NOT NULL, by_uid INTEGER NOT NULL, created INTEGER NOT NULL, expires INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'open')""",
            """CREATE TABLE IF NOT EXISTS police_lawyer (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, target INTEGER NOT NULL, by_uid INTEGER NOT NULL, created INTEGER NOT NULL, expires INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'open')""",
            """CREATE TABLE IF NOT EXISTS hit_order (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, payer INTEGER NOT NULL, payer_gang INTEGER NOT NULL DEFAULT 0, target INTEGER NOT NULL,
               hired_gang INTEGER NOT NULL, price INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'offer', created INTEGER NOT NULL, expires INTEGER NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS stash (chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL, amount INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (chat_id, user_id))""",
            """CREATE TABLE IF NOT EXISTS protection (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL, gang_id INTEGER NOT NULL, price INTEGER NOT NULL,
               status TEXT NOT NULL DEFAULT 'open', until INTEGER NOT NULL DEFAULT 0, expires INTEGER NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS gang_project (gang_id INTEGER NOT NULL, key TEXT NOT NULL, level INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (gang_id, key))""",
            """CREATE TABLE IF NOT EXISTS rp_visit (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL DEFAULT 0,
               gang_id INTEGER NOT NULL DEFAULT 0, kind TEXT NOT NULL, amount INTEGER NOT NULL DEFAULT 0, created INTEGER NOT NULL, expires INTEGER NOT NULL,
               status TEXT NOT NULL DEFAULT 'open', result TEXT NOT NULL DEFAULT '')""",
            """CREATE TABLE IF NOT EXISTS rp_guest (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, key TEXT NOT NULL,
               started INTEGER NOT NULL, ends INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'open')""",
            """CREATE TABLE IF NOT EXISTS rp_guest_claim (guest_id INTEGER NOT NULL, user_id INTEGER NOT NULL, offer TEXT NOT NULL, ts INTEGER NOT NULL,
               PRIMARY KEY (guest_id, user_id))""",
            """CREATE TABLE IF NOT EXISTS rat_answer (id INTEGER PRIMARY KEY AUTOINCREMENT, case_id INTEGER NOT NULL, ts INTEGER NOT NULL,
               target INTEGER NOT NULL, text TEXT NOT NULL)""",
        ):
            await db.execute(ddl)
        # Три ресурса вместо пяти: старые склады и сделки переезжают в goods/arms/intel.
        cur = await db.execute("SELECT gang_id, res, qty FROM gang_stock WHERE res IN ('cargo', 'grain', 'parts', 'spirits')")
        old_stock = await cur.fetchall()
        res_map = {"cargo": "goods", "grain": "goods", "parts": "arms", "spirits": "intel"}
        for gid, res, qty in old_stock:
            new_res = res_map.get(res)
            if not new_res:
                continue
            await db.execute(
                "INSERT INTO gang_stock (gang_id, res, qty) VALUES (?, ?, ?) "
                "ON CONFLICT(gang_id, res) DO UPDATE SET qty = MAX(0, qty + ?)",
                (gid, new_res, max(0, int(qty or 0)), int(qty or 0)),
            )
            await db.execute("DELETE FROM gang_stock WHERE gang_id = ? AND res = ?", (gid, res))
        await db.execute("UPDATE gang_deal SET item = 'goods' WHERE item IN ('cargo', 'grain')")
        await db.execute("UPDATE gang_deal SET item = 'arms' WHERE item = 'parts'")
        await db.execute("UPDATE gang_deal SET item = 'intel' WHERE item = 'spirits'")
        await db.commit()


async def _log(db, chat_id: int, user_id: int, delta: int, reason: str) -> None:
    """Пишет изменение размера в историю (для графиков и достижений мини-аппа)."""
    await db.execute(
        """
        INSERT INTO size_log (chat_id, user_id, ts, delta, reason, size_after)
        SELECT chat_id, user_id, ?, ?, ?, size FROM users WHERE chat_id = ? AND user_id = ?
        """,
        (int(time.time()), delta, reason, chat_id, user_id),
    )


async def ensure_user(user_id: int, chat_id: int, username: str) -> Tuple[int, int, int, int]:
    """
    Гарантирует наличие пользователя в БД для данного чата и обновляет его актуальное имя.
    Возвращает кортеж: (size, rep, last_roll, last_rob).
    """
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        await db.execute(
            """
            INSERT INTO users (user_id, chat_id, username)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, chat_id) DO UPDATE SET username = excluded.username
            """,
            (user_id, chat_id, username),
        )
        await db.commit()

        cursor = await db.execute(
            "SELECT size, rep, last_roll, last_rob FROM users WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id),
        )
        row = await cursor.fetchone()
        return row if row else (0, 0, 0, 0)


async def update_dick_roll(user_id: int, chat_id: int, username: str, delta: int, now_ts: int) -> int:
    """Обновляет размер и время последнего замера (/dick), возвращает новый размер."""
    await ensure_user(user_id, chat_id, username)
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        await db.execute(
            """
            UPDATE users
            SET size = size + ?, last_roll = ?, username = ?
            WHERE user_id = ? AND chat_id = ?
            """,
            (delta, now_ts, username, user_id, chat_id),
        )
        await _log(db, chat_id, user_id, delta, "dick")
        await db.commit()

        cursor = await db.execute(
            "SELECT size FROM users WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id),
        )
        row = await cursor.fetchone()
        return row[0] if row else 0


async def update_last_rob(user_id: int, chat_id: int, now_ts: int) -> None:
    """Обновляет timestamp последней попытки ограбления (/rob) для инициатора."""
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        await db.execute(
            "UPDATE users SET last_rob = ? WHERE user_id = ? AND chat_id = ?",
            (now_ts, user_id, chat_id),
        )
        await db.commit()


async def transfer_size(
    user1_id: int,
    user2_id: int,
    chat_id: int,
    delta1: int,
    delta2: int,
    rob_ts_for_user1: Optional[int] = None,
    reason: str = "",
) -> Tuple[int, int]:
    """
    Атомарно изменяет размер двух пользователей (для /rob и /duel).
    Если передан rob_ts_for_user1, одновременно обновляет last_rob первого игрока.
    Возвращает новые размеры: (new_size1, new_size2).
    """
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        if rob_ts_for_user1 is not None:
            await db.execute(
                "UPDATE users SET size = size + ?, last_rob = ? WHERE user_id = ? AND chat_id = ?",
                (delta1, rob_ts_for_user1, user1_id, chat_id),
            )
        else:
            await db.execute(
                "UPDATE users SET size = size + ? WHERE user_id = ? AND chat_id = ?",
                (delta1, user1_id, chat_id),
            )

        await db.execute(
            "UPDATE users SET size = size + ? WHERE user_id = ? AND chat_id = ?",
            (delta2, user2_id, chat_id),
        )
        await _log(db, chat_id, user1_id, delta1, reason)
        await _log(db, chat_id, user2_id, delta2, reason)
        await db.commit()

        c1 = await db.execute(
            "SELECT size FROM users WHERE user_id = ? AND chat_id = ?",
            (user1_id, chat_id),
        )
        r1 = await c1.fetchone()

        c2 = await db.execute(
            "SELECT size FROM users WHERE user_id = ? AND chat_id = ?",
            (user2_id, chat_id),
        )
        r2 = await c2.fetchone()

        return (r1[0] if r1 else 0, r2[0] if r2 else 0)


async def get_top_titans(chat_id: int, limit: int = 5) -> List[Tuple[str, int, int, str]]:
    """Возвращает Топ пользователей по size DESC: (username, size, rep, активный_титул)."""
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        cursor = await db.execute(
            """
            SELECT username, size, rep,
                   CASE WHEN title_until > ? THEN title ELSE '' END
            FROM users
            WHERE chat_id = ?
            ORDER BY size DESC
            LIMIT ?
            """,
            (int(time.time()), chat_id, limit),
        )
        return await cursor.fetchall()


async def get_black_holes(chat_id: int, limit: int = 3) -> List[Tuple[str, int, int]]:
    """Возвращает Топ-3 пользователей с отрицательным размером (size < 0) по size ASC."""
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        cursor = await db.execute(
            """
            SELECT username, size, rep
            FROM users
            WHERE chat_id = ? AND size < 0
            ORDER BY size ASC
            LIMIT ?
            """,
            (chat_id, limit),
        )
        return await cursor.fetchall()


async def change_rep(user_id: int, chat_id: int, username: str, delta: int) -> int:
    """Изменяет репутацию пользователя на delta (+1 или -1) и возвращает новое значение rep."""
    await ensure_user(user_id, chat_id, username)
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        await db.execute(
            """
            UPDATE users
            SET rep = rep + ?, username = ?
            WHERE user_id = ? AND chat_id = ?
            """,
            (delta, username, user_id, chat_id),
        )
        await db.commit()

        cursor = await db.execute(
            "SELECT rep FROM users WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id),
        )
        row = await cursor.fetchone()
        return row[0] if row else 0


async def get_random_user(chat_id: int) -> Optional[Tuple[int, str]]:
    """Выбирает случайного активного участника чата (ORDER BY RANDOM() LIMIT 1)."""
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        cursor = await db.execute(
            """
            SELECT user_id, username
            FROM users
            WHERE chat_id = ?
            ORDER BY RANDOM()
            LIMIT 1
            """,
            (chat_id,),
        )
        return await cursor.fetchone()


async def add_size(user_id: int, chat_id: int, username: str, delta: int, reason: str = "") -> int:
    """Меняет размер одного игрока на delta и возвращает новый размер."""
    await ensure_user(user_id, chat_id, username)
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        await db.execute(
            "UPDATE users SET size = size + ? WHERE user_id = ? AND chat_id = ?",
            (delta, user_id, chat_id),
        )
        if delta:
            await _log(db, chat_id, user_id, delta, reason)
        await db.commit()
        cursor = await db.execute(
            "SELECT size FROM users WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id),
        )
        row = await cursor.fetchone()
        return row[0] if row else 0


async def claim_daily(
    user_id: int, chat_id: int, username: str, today: str, yesterday: str
) -> Tuple[bool, int, int, int]:
    """
    Ежедневный бонус. Возвращает (получен, серия_дней, бонус, новый_размер).
    Серия растёт, если бонус брали вчера, иначе начинается заново.
    """
    await ensure_user(user_id, chat_id, username)
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        await db.execute("BEGIN IMMEDIATE")
        cursor = await db.execute(
            "SELECT last_daily, streak, size FROM users WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id),
        )
        last_daily, streak, size = await cursor.fetchone()
        if last_daily == today:
            await db.execute("ROLLBACK")
            return (False, streak, 0, size)

        streak = streak + 1 if last_daily == yesterday else 1
        bonus = random.randint(1, 4) + min(streak - 1, 5)
        await db.execute(
            "UPDATE users SET last_daily = ?, streak = ?, size = size + ? WHERE user_id = ? AND chat_id = ?",
            (today, streak, bonus, user_id, chat_id),
        )
        await _log(db, chat_id, user_id, bonus, "daily")
        await db.commit()
        return (True, streak, bonus, size + bonus)


async def buy_title(user_id: int, chat_id: int, price: int, title: str, until_ts: int) -> Optional[int]:
    """Покупка титула за сантиметры. Возвращает новый размер или None, если не хватает."""
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        cursor = await db.execute(
            """
            UPDATE users SET size = size - ?, title = ?, title_until = ?
            WHERE user_id = ? AND chat_id = ? AND size >= ?
            """,
            (price, title, until_ts, user_id, chat_id, price),
        )
        if cursor.rowcount:
            await _log(db, chat_id, user_id, -price, "shop")
        await db.commit()
        if cursor.rowcount == 0:
            return None
        cursor = await db.execute(
            "SELECT size FROM users WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id),
        )
        row = await cursor.fetchone()
        return row[0] if row else None


async def get_random_users(chat_id: int, n: int) -> List[Tuple[int, str]]:
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        cursor = await db.execute(
            "SELECT user_id, username FROM users WHERE chat_id = ? ORDER BY RANDOM() LIMIT ?",
            (chat_id, n),
        )
        return await cursor.fetchall()


async def get_chats() -> List[int]:
    """Все известные групповые чаты (у групп chat_id отрицательный)."""
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        cursor = await db.execute("SELECT DISTINCT chat_id FROM users WHERE chat_id < 0")
        return [r[0] for r in await cursor.fetchall()]


GIVE_COOLDOWN = 2 * 3600  # передавать свои см другому можно раз в 2 часа


async def give_size(chat_id: int, from_id: int, to_id: int, amount: int) -> Tuple[str, int, int]:
    """
    Атомарно передаёт amount см. Возвращает (статус, новый_размер_отправителя, секунд_до_следующей_передачи).
    Статус: ok | cooldown | funds | self | amount | nouser.
    """
    if from_id == to_id:
        return "self", 0, 0
    if amount < 1:
        return "amount", 0, 0
    now = int(time.time())
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        cur = await db.execute("SELECT 1 FROM users WHERE user_id = ? AND chat_id = ?", (to_id, chat_id))
        if not await cur.fetchone():
            return "nouser", 0, 0
        cur = await db.execute(
            "UPDATE users SET size = size - ?, last_drip = ? WHERE user_id = ? AND chat_id = ? AND size >= ? AND last_drip <= ?",
            (amount, now, from_id, chat_id, amount, now - GIVE_COOLDOWN),
        )
        if cur.rowcount == 0:
            await db.rollback()
            cur = await db.execute("SELECT size, last_drip FROM users WHERE user_id = ? AND chat_id = ?", (from_id, chat_id))
            row = await cur.fetchone()
            size, last = row if row else (0, 0)
            if last + GIVE_COOLDOWN > now:
                return "cooldown", size, last + GIVE_COOLDOWN - now
            return "funds", size, 0
        await db.execute("UPDATE users SET size = size + ? WHERE user_id = ? AND chat_id = ?", (amount, to_id, chat_id))
        await _log(db, chat_id, from_id, -amount, "give_out")
        await _log(db, chat_id, to_id, amount, "give_in")
        await db.commit()
        cur = await db.execute("SELECT size FROM users WHERE user_id = ? AND chat_id = ?", (from_id, chat_id))
        return "ok", (await cur.fetchone())[0], GIVE_COOLDOWN


async def find_user_by_name(chat_id: int, name: str) -> Optional[Tuple[int, str]]:
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        cur = await db.execute(
            "SELECT user_id, username FROM users WHERE chat_id = ? AND lower(username) = lower(?)",
            (chat_id, name if name.startswith("@") else "@" + name),
        )
        return await cur.fetchone()


async def _top_streak(chat_id: int):
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        cur = await db.execute(
            "SELECT username, streak FROM users WHERE chat_id = ? AND streak >= 2 ORDER BY streak DESC LIMIT 1", (chat_id,)
        )
        return await cur.fetchone()


async def count_command(user_id: int, cmd: str, day: str) -> None:
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        await db.execute(
            """INSERT INTO cmd_stats (day, cmd, user_id, cnt) VALUES (?, ?, ?, 1)
               ON CONFLICT(day, cmd, user_id) DO UPDATE SET cnt = cnt + 1""",
            (day, cmd, user_id),
        )
        await db.commit()


async def count_message(chat_id: int, user_id: int, day: str) -> None:
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        await db.execute(
            """
            INSERT INTO msg_stats (chat_id, user_id, day, cnt) VALUES (?, ?, ?, 1)
            ON CONFLICT(chat_id, user_id, day) DO UPDATE SET cnt = cnt + 1
            """,
            (chat_id, user_id, day),
        )
        await db.commit()


async def top_talkers(chat_id: int, since_day: str, limit: int = 3) -> List[Tuple[str, int]]:
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        cursor = await db.execute(
            """
            SELECT u.username, SUM(m.cnt) AS c
            FROM msg_stats m
            JOIN users u ON u.user_id = m.user_id AND u.chat_id = m.chat_id
            WHERE m.chat_id = ? AND m.day >= ?
            GROUP BY m.user_id
            ORDER BY c DESC
            LIMIT ?
            """,
            (chat_id, since_day, limit),
        )
        return await cursor.fetchall()


async def total_messages(chat_id: int, since_day: str) -> int:
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        cursor = await db.execute(
            "SELECT COALESCE(SUM(cnt), 0) FROM msg_stats WHERE chat_id = ? AND day >= ?",
            (chat_id, since_day),
        )
        return (await cursor.fetchone())[0]


async def hero_leader(chat_id: int, since_day: str) -> Optional[Tuple[str, int]]:
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        cursor = await db.execute(
            """
            SELECT u.username, COUNT(*) AS c
            FROM heroes h
            JOIN users u ON u.user_id = h.user_id AND u.chat_id = h.chat_id
            WHERE h.chat_id = ? AND h.day >= ?
            GROUP BY h.user_id
            ORDER BY c DESC
            LIMIT 1
            """,
            (chat_id, since_day),
        )
        return await cursor.fetchone()


async def get_hero(chat_id: int, day: str) -> Optional[Tuple[int, str, str]]:
    """Герой дня: (user_id, username, title) либо None."""
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        cursor = await db.execute(
            """
            SELECT h.user_id, u.username, h.title
            FROM heroes h
            JOIN users u ON u.user_id = h.user_id AND u.chat_id = h.chat_id
            WHERE h.chat_id = ? AND h.day = ?
            """,
            (chat_id, day),
        )
        return await cursor.fetchone()


async def set_hero(chat_id: int, day: str, user_id: int, title: str) -> None:
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        await db.execute(
            "INSERT OR IGNORE INTO heroes (chat_id, day, user_id, title) VALUES (?, ?, ?, ?)",
            (chat_id, day, user_id, title),
        )
        await db.commit()


async def add_quote(chat_id: int, author_id: int, author_name: str, text: str, added_by: int) -> int:
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        cursor = await db.execute(
            """
            INSERT INTO quotes (chat_id, author_id, author_name, text, added_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (chat_id, author_id, author_name, text, added_by, int(time.time())),
        )
        await db.commit()
        return cursor.lastrowid


async def random_quote(chat_id: int) -> Optional[Tuple[str, str, int]]:
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        cursor = await db.execute(
            "SELECT text, author_name, created_at FROM quotes WHERE chat_id = ? ORDER BY RANDOM() LIMIT 1",
            (chat_id,),
        )
        return await cursor.fetchone()


async def kv_get(key: str) -> Optional[str]:
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        cursor = await db.execute("SELECT value FROM kv WHERE key = ?", (key,))
        row = await cursor.fetchone()
        return row[0] if row else None


async def kv_set(key: str, value: str) -> None:
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        await db.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await db.commit()


async def top_rep(chat_id: int) -> Optional[Tuple[str, int]]:
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        cursor = await db.execute(
            "SELECT username, rep FROM users WHERE chat_id = ? AND rep > 0 ORDER BY rep DESC LIMIT 1",
            (chat_id,),
        )
        return await cursor.fetchone()


# ───────── лотерея ─────────

async def buy_ticket(user_id: int, chat_id: int, day: str, cost: int, cap: int) -> Tuple[str, int]:
    """
    Покупка билета. Возвращает (статус, билетов_у_игрока):
    'ok' | 'poor' (не хватает см) | 'cap' (достигнут лимит билетов).
    """
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        await db.execute("BEGIN IMMEDIATE")
        cursor = await db.execute(
            "SELECT tickets FROM lottery WHERE chat_id = ? AND day = ? AND user_id = ?",
            (chat_id, day, user_id),
        )
        row = await cursor.fetchone()
        have = row[0] if row else 0
        if have >= cap:
            await db.execute("ROLLBACK")
            return ("cap", have)
        cursor = await db.execute(
            "UPDATE users SET size = size - ? WHERE user_id = ? AND chat_id = ? AND size >= ?",
            (cost, user_id, chat_id, cost),
        )
        if cursor.rowcount == 0:
            await db.execute("ROLLBACK")
            return ("poor", have)
        await db.execute(
            """
            INSERT INTO lottery (chat_id, day, user_id, tickets) VALUES (?, ?, ?, 1)
            ON CONFLICT(chat_id, day, user_id) DO UPDATE SET tickets = tickets + 1
            """,
            (chat_id, day, user_id),
        )
        await _log(db, chat_id, user_id, -cost, "lottery_ticket")
        await db.commit()
        return ("ok", have + 1)


async def lottery_state(chat_id: int, day: str, user_id: int) -> Tuple[int, int, int]:
    """(мои билеты, всего билетов, участников)."""
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        cursor = await db.execute(
            "SELECT COALESCE(SUM(tickets), 0), COUNT(*) FROM lottery WHERE chat_id = ? AND day = ?",
            (chat_id, day),
        )
        total, players = await cursor.fetchone()
        cursor = await db.execute(
            "SELECT tickets FROM lottery WHERE chat_id = ? AND day = ? AND user_id = ?",
            (chat_id, day, user_id),
        )
        mine = await cursor.fetchone()
        return (mine[0] if mine else 0, total, players)


async def lottery_due(max_day: str) -> List[Tuple[int, str]]:
    """Чаты и дни с неразыгранными билетами (день не позже max_day)."""
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        cursor = await db.execute(
            "SELECT DISTINCT chat_id, day FROM lottery WHERE day <= ?", (max_day,)
        )
        return await cursor.fetchall()


async def lottery_take(chat_id: int, day: str) -> List[Tuple[int, str, int]]:
    """Забирает (и удаляет) все билеты розыгрыша: [(user_id, username, билетов)]."""
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        await db.execute("BEGIN IMMEDIATE")
        cursor = await db.execute(
            """
            SELECT l.user_id, COALESCE(u.username, ''), l.tickets
            FROM lottery l LEFT JOIN users u ON u.user_id = l.user_id AND u.chat_id = l.chat_id
            WHERE l.chat_id = ? AND l.day = ?
            """,
            (chat_id, day),
        )
        rows = await cursor.fetchall()
        await db.execute("DELETE FROM lottery WHERE chat_id = ? AND day = ?", (chat_id, day))
        await db.commit()
        return rows


# ───────── вопрос дня ─────────

async def get_daily_q(chat_id: int, day: str) -> Optional[Tuple[int, str, int]]:
    """(message_id, вопрос, подведены_ли_итоги) либо None."""
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        cursor = await db.execute(
            "SELECT message_id, question, summarized FROM daily_q WHERE chat_id = ? AND day = ?",
            (chat_id, day),
        )
        return await cursor.fetchone()


async def set_daily_q(chat_id: int, day: str, message_id: int, question: str) -> None:
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        await db.execute(
            "INSERT OR REPLACE INTO daily_q (chat_id, day, message_id, question, summarized) VALUES (?, ?, ?, ?, 0)",
            (chat_id, day, message_id, question),
        )
        await db.commit()


async def mark_q_summarized(chat_id: int, day: str) -> None:
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        await db.execute(
            "UPDATE daily_q SET summarized = 1 WHERE chat_id = ? AND day = ?", (chat_id, day)
        )
        await db.commit()


async def open_questions(since_day: str) -> List[Tuple[int, int, str]]:
    """[(chat_id, message_id, day)] неподытоженных вопросов начиная с since_day."""
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        cursor = await db.execute(
            "SELECT chat_id, message_id, day FROM daily_q WHERE summarized = 0 AND day >= ?",
            (since_day,),
        )
        return await cursor.fetchall()


async def add_answer(chat_id: int, day: str, user_id: int, text: str) -> bool:
    """Сохраняет первый ответ игрока; False, если он уже отвечал."""
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        cursor = await db.execute(
            "INSERT OR IGNORE INTO daily_answers (chat_id, day, user_id, text) VALUES (?, ?, ?, ?)",
            (chat_id, day, user_id, text),
        )
        await db.commit()
        return cursor.rowcount > 0


async def get_answers(chat_id: int, day: str) -> List[Tuple[str, str]]:
    """[(username, text)]"""
    async with aiosqlite.connect(DB_PATH, timeout=10) as db:
        cursor = await db.execute(
            """
            SELECT COALESCE(u.username, '?'), a.text
            FROM daily_answers a LEFT JOIN users u ON u.user_id = a.user_id AND u.chat_id = a.chat_id
            WHERE a.chat_id = ? AND a.day = ?
            """,
            (chat_id, day),
        )
        return await cursor.fetchall()
