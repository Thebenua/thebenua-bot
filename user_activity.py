"""Admin user directory and activity journal. No message bodies or product secrets."""

from contextlib import closing
from datetime import datetime, timezone, timedelta
from html import escape
import logging
import time

from aiogram import BaseMiddleware, F
from aiogram.types import CallbackQuery, Message

PAGE_SIZE = 10
SAMARA = timezone(timedelta(hours=4))


def migrate(conn):
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
    for name, definition in (
        ("full_name", "TEXT"),
        ("last_seen", "INTEGER"),
        ("telegram_username", "TEXT"),
    ):
        if name not in columns:
            conn.execute(f"ALTER TABLE users ADD COLUMN {name} {definition}")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS user_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            description TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_user_events_history
            ON user_events(user_id, created_at DESC, id DESC);
        CREATE INDEX IF NOT EXISTS idx_users_last_seen
            ON users(last_seen DESC, user_id DESC);
        CREATE INDEX IF NOT EXISTS idx_purchases_user ON purchases(user_id);
        CREATE INDEX IF NOT EXISTS idx_payments_user ON payments(user_id);
    """)


def describe(event, conn):
    if isinstance(event, Message):
        command = (event.text or "").split(maxsplit=1)[0:1]
        command = command[0].split("@")[0] if command else ""
        return {"/start": "Запустил бота /start", "/help": "Открыл помощь"}.get(command)
    data = event.data or ""
    sections = {
        "menu": "Открыл главное меню", "catalog": "Открыл каталог",
        "profile": "Открыл профиль", "topup": "Открыл пополнение баланса",
        "mypurchases": "Открыл свои покупки", "promo": "Открыл ввод промокода",
        "support": "Открыл раздел поддержки", "terms": "Открыл условия",
    }
    if data in sections:
        return sections[data]
    prefix, _, value = data.partition(":")
    products = {
        "prod": "Нажал на товар", "buy": "Нажал «Купить»",
        "paybal": "Выбрал оплату балансом", "paystars": "Выбрал оплату Stars",
        "paycb": "Выбрал оплату CryptoBot",
    }
    if prefix in products or prefix == "cat":
        item_id = value.split(":")[0]
        if not item_id.isdigit():
            return None
        table = "categories" if prefix == "cat" else "products"
        row = conn.execute(f"SELECT name FROM {table} WHERE id=?", (item_id,)).fetchone()
        name = row["name"] if row else f"ID {item_id} (удалён)"
        return f'{products.get(prefix, "Нажал на категорию")}: {name[:160]}'
    if prefix in ("topcb", "topstars"):
        return "Выбрал пополнение через " + ("CryptoBot" if prefix == "topcb" else "Stars")
    if prefix == "rate":
        return "Нажал оценку покупки"
    return None


def record(conn, user, description):
    now = int(time.time())
    conn.execute(
        "INSERT OR IGNORE INTO users(user_id, username, created_at) VALUES(?,?,?)",
        (user.id, user.username or user.first_name, now),
    )
    conn.execute(
        "UPDATE users SET full_name=?, telegram_username=?, username=?, last_seen=? WHERE user_id=?",
        (user.full_name, user.username, user.username or user.first_name, now, user.id),
    )
    if description:
        conn.execute(
            "INSERT INTO user_events(user_id, description, created_at) VALUES(?,?,?)",
            (user.id, description, now),
        )
    conn.commit()


def stamp(value):
    return datetime.fromtimestamp(value, SAMARA).strftime("%d.%m.%Y %H:%M") if value else "нет данных"


def history(conn, user_id, offset):
    return conn.execute("""
        SELECT description, created_at FROM (
            SELECT description, created_at, id, 0 AS source FROM user_events WHERE user_id=?
            UNION ALL
            SELECT 'Покупка: ' || substr(product_name,1,160) || ' — ' || amount || ' ₽ (' || method || ')',
                   created_at, id, 1 FROM purchases WHERE user_id=?
            UNION ALL
            SELECT 'Пополнение: ' || amount || ' ₽ (' || method || ')',
                   created_at, id, 2 FROM payments WHERE user_id=? AND kind='topup'
        ) ORDER BY created_at DESC, source DESC, id DESC LIMIT ? OFFSET ?
    """, (user_id, user_id, user_id, PAGE_SIZE + 1, offset)).fetchall()


def register(dp, db, is_admin, send_msg, btn, nav):
    class ActivityMiddleware(BaseMiddleware):
        async def __call__(self, handler, event, data):
            # Let /start create the user first so existing new-user alerts still work.
            user = event.from_user
            description = None
            if user and not user.is_bot:
                try:
                    with closing(db()) as conn:
                        description = describe(event, conn)
                except Exception:
                    logging.exception("Cannot describe user activity")
            try:
                return await handler(event, data)
            finally:
                if user and not user.is_bot:
                    try:
                        with closing(db()) as conn:
                            record(conn, user, description)
                    except Exception:
                        logging.exception("Cannot save user activity")

    dp.message.outer_middleware(ActivityMiddleware())
    dp.callback_query.outer_middleware(ActivityMiddleware())

    @dp.callback_query(F.data.startswith("adm:users:"))
    async def users(callback: CallbackQuery, state):
        if not is_admin(callback.from_user.id):
            await callback.answer("Нет доступа", show_alert=True)
            return
        try:
            page = max(0, int(callback.data.split(":")[2]))
        except (ValueError, IndexError):
            await callback.answer("Некорректная страница")
            return
        await state.clear()
        with closing(db()) as conn:
            total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            page = min(page, max(0, (total - 1) // PAGE_SIZE))
            rows = conn.execute(
                "SELECT * FROM users ORDER BY last_seen DESC, user_id DESC LIMIT ? OFFSET ?",
                (PAGE_SIZE, page * PAGE_SIZE),
            ).fetchall()
        kb = []
        for row in rows:
            label = row["full_name"] or row["username"] or "Без имени"
            kb.append([btn(f'{label[:35]} · {row["user_id"]}', cb=f'adm:user:{row["user_id"]}:{page}:0')])
        arrows = []
        if page:
            arrows.append(btn("← Назад", cb=f"adm:users:{page-1}"))
        if (page + 1) * PAGE_SIZE < total:
            arrows.append(btn("Далее →", cb=f"adm:users:{page+1}"))
        if arrows:
            kb.append(arrows)
        kb.append([btn("Админ-панель", cb="adm:panel")])
        await nav(callback)
        await send_msg(callback.message.chat.id,
                       f"<b>Пользователи: {total}</b>\nПо последней активности. Страница {page+1}.\n"
                       + ("Выберите пользователя:" if rows else "Пользователей пока нет."), kb)

    @dp.callback_query(F.data.startswith("adm:user:"))
    async def user_card(callback: CallbackQuery, state):
        if not is_admin(callback.from_user.id):
            await callback.answer("Нет доступа", show_alert=True)
            return
        try:
            uid, list_page, page = map(int, callback.data.split(":")[2:])
            if min(uid, list_page, page) < 0 or page > 1000000:
                raise ValueError
        except ValueError:
            await callback.answer("Некорректная карточка")
            return
        await state.clear()
        with closing(db()) as conn:
            user = conn.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
            if not user:
                await callback.answer("Пользователь не найден", show_alert=True)
                return
            count = conn.execute("SELECT COUNT(*) FROM purchases WHERE user_id=?", (uid,)).fetchone()[0]
            events = history(conn, uid, page * PAGE_SIZE)
        name = escape(user["full_name"] or user["username"] or "Нет данных")
        username = ("@" + escape(user["telegram_username"])) if user["telegram_username"] else "нет данных"
        text = (
            f'<b>Пользователь</b>\nИмя: {name}\nID: <code>{uid}</code>\nЮзернейм: {username}\n'
            f'Первый запуск: {stamp(user["created_at"])}\nПоследняя активность: {stamp(user["last_seen"])}\n'
            f'Баланс: {user["balance"]:g} ₽\nПотрачено: {user["spent"]:g} ₽ · Покупок: {count}\n\n'
            f'<b>История · страница {page+1}</b>\nВремя: Самара (UTC+4)\n'
        )
        for event in events[:PAGE_SIZE]:
            text += f'\n{stamp(event["created_at"])} — {escape(event["description"])}'
        if not events:
            text += "\nЗаписей пока нет."
        text += "\n\nПросмотры записываются с обновления бота; нажатие оплаты ещё не означает покупку."
        arrows = []
        if page:
            arrows.append(btn("← Новее", cb=f"adm:user:{uid}:{list_page}:{page-1}"))
        if len(events) > PAGE_SIZE:
            arrows.append(btn("Старее →", cb=f"adm:user:{uid}:{list_page}:{page+1}"))
        kb = [arrows] if arrows else []
        kb.extend([
            [btn("Обновить", cb=f"adm:user:{uid}:{list_page}:0")],
            [btn("Профиль Telegram", url=f"tg://user?id={uid}")],
            [btn("Пользователи", cb=f"adm:users:{list_page}")],
        ])
        await nav(callback)
        await send_msg(callback.message.chat.id, text, kb)
