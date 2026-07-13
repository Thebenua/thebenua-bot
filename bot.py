# -*- coding: utf-8 -*-
"""THEBENUA SHOP — Telegram-бот продажи цифровых товаров.

aiogram 3.x + SQLite. Оплата: CryptoBot (RUB) и Telegram Stars (1⭐ = 1.5₽).
Цветные кнопки (style) и премиум-эмодзи (icon_custom_emoji_id / tg-emoji)
отправляются напрямую через Telegram Bot API.
"""

import asyncio
import json
import math
import os
import sqlite3
import time

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommand,
    BotCommandScopeChat,
    BotCommandScopeDefault,
    CallbackQuery,
    FSInputFile,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
CRYPTOBOT_TOKEN = os.getenv("CRYPTOBOT_TOKEN", "")
SUPPORT_URL = os.getenv("SUPPORT_URL", "https://t.me/")
SUPPORT_CONTACT = os.getenv("SUPPORT_CONTACT", "@thebenua")

STARS_RATE = 1.5  # 1 звезда = 1.5 руб
DB_PATH = os.getenv("DB_PATH", "shop.db")
if os.path.dirname(DB_PATH):
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
BANNER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "banner.png")
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
CRYPTO_API = "https://pay.crypt.bot/api"

# ---------------------------------------------------------------- premium emoji
E = {
    "settings": "5870982283724328568",
    "profile": "5870994129244131212",
    "people": "5870772616305839506",
    "user_check": "5891207662678317861",
    "user_x": "5893192487324880883",
    "file": "5870528606328852614",
    "smile": "5870764288364252592",
    "growth": "5870930636742595124",
    "stats": "5870921681735781843",
    "home": "5873147866364514353",
    "lock": "6037249452824072506",
    "unlock": "6037496202990194718",
    "megaphone": "6039422865189638057",
    "check": "5870633910337015697",
    "cross": "5870657884844462243",
    "pencil": "5870676941614354370",
    "trash": "5870875489362513438",
    "down": "5893057118545646106",
    "clip": "6039451237743595514",
    "link": "5769289093221454192",
    "info": "6028435952299413210",
    "bot": "6030400221232501136",
    "eye": "6037397706505195857",
    "hidden": "6037243349675544634",
    "send": "5963103826075456248",
    "download": "6039802767931871481",
    "bell": "6039486778597970865",
    "gift": "6032644646587338669",
    "clock": "5983150113483134607",
    "party": "6041731551845159060",
    "font": "5870801517140775623",
    "write": "5870753782874246579",
    "media": "6035128606563241721",
    "geo": "6042011682497106307",
    "wallet": "5769126056262898415",
    "box": "5884479287171485878",
    "crypto": "5260752406890711732",
    "calendar": "5890937706803894250",
    "tag": "5886285355279193209",
    "expired": "5775896410780079073",
    "apps": "5778672437122045013",
    "brush": "6050679691004612757",
    "addtext": "5771851822897566479",
    "format": "5778479949572738874",
    "money": "5904462880941545555",
    "send_money": "5890848474563352982",
    "receive_money": "5879814368572478751",
    "code": "5940433880585605708",
    "loading": "5345906554510012647",
    "gemini": "5262737124163100930",
    "chatgpt": "5262535526988165767",
    # алиасы для обратной совместимости
    "balance": "5769126056262898415",
    "catalog": "5778672437122045013",
    "lang": "5345906554510012647",
    "welcome": "6041731551845159060",
    "support": "5870772616305839506",
    "topup": "5890848474563352982",
}


def tge(key: str, fallback: str) -> str:
    return f'<tg-emoji emoji-id="{E[key]}">{fallback}</tg-emoji>'


def http_session() -> aiohttp.ClientSession:
    # ssl=False: обход подмены сертификатов антивирусом/прокси на Windows
    return aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False))


_aiogram_session = AiohttpSession()
_aiogram_session._connector_init["ssl"] = False
bot = Bot(
    token=BOT_TOKEN,
    session=_aiogram_session,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher(storage=MemoryStorage())

_banner_file_id = None

# ---------------------------------------------------------------- database


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            balance REAL DEFAULT 0,
            spent REAL DEFAULT 0,
            lang TEXT DEFAULT 'ru',
            created_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE
        );
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category_id INTEGER,
            name TEXT,
            price REAL,
            qty INTEGER,
            content TEXT,
            description TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            product_id INTEGER,
            product_name TEXT,
            amount REAL,
            method TEXT,
            created_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount REAL,
            method TEXT,
            kind TEXT,
            created_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS promocodes (
            code TEXT PRIMARY KEY,
            amount REAL,
            uses_left INTEGER
        );
        CREATE TABLE IF NOT EXISTS promo_activations (
            code TEXT,
            user_id INTEGER,
            PRIMARY KEY (code, user_id)
        );
        CREATE TABLE IF NOT EXISTS ratings (
            purchase_id INTEGER PRIMARY KEY,
            user_id INTEGER,
            stars INTEGER,
            created_at INTEGER
        );
        """
    )
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(products)").fetchall()]
    if "description" not in cols:
        conn.execute("ALTER TABLE products ADD COLUMN description TEXT DEFAULT ''")
    if "delivery" not in cols:
        conn.execute("ALTER TABLE products ADD COLUMN delivery TEXT DEFAULT 'same'")
    ucols = [r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
    if "discount" not in ucols:
        conn.execute("ALTER TABLE users ADD COLUMN discount REAL DEFAULT 0")
    pcols = [r["name"] for r in conn.execute("PRAGMA table_info(promocodes)").fetchall()]
    if "ptype" not in pcols:
        conn.execute("ALTER TABLE promocodes ADD COLUMN ptype TEXT DEFAULT 'balance'")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS product_items ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER, content TEXT, used INTEGER DEFAULT 0)"
    )
    for name in ("GEMINI", "Chat GPT"):
        conn.execute("INSERT OR IGNORE INTO categories(name) VALUES(?)", (name,))
    conn.commit()
    conn.close()


def get_user(user_id: int):
    conn = db()
    row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return row


def add_user(user_id: int, username: str) -> bool:
    """Добавляет пользователя; True — если это новый пользователь."""
    conn = db()
    cur = conn.execute(
        "INSERT OR IGNORE INTO users(user_id, username, created_at) VALUES(?,?,?)",
        (user_id, username, int(time.time())),
    )
    is_new = cur.rowcount > 0
    conn.commit()
    conn.close()
    return is_new


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def user_discount(user_id: int) -> float:
    u = get_user(user_id)
    if not u or "discount" not in u.keys():
        return 0
    return u["discount"] or 0


def price_after_discount(price: float, disc: float) -> float:
    return round(price * (1 - disc / 100), 2) if disc else price


def cat_icon(name: str) -> str:
    n = name.lower()
    if "gemini" in n:
        return "gemini"
    if "gpt" in n or "chat" in n:
        return "chatgpt"
    return "apps"


# ---------------------------------------------------------------- raw API helpers
# Прямые вызовы Bot API — для style ("primary"/"success"/"danger")
# и icon_custom_emoji_id у inline-кнопок.


def btn(text, cb=None, url=None, style=None, icon=None):
    b = {"text": text}
    if cb:
        b["callback_data"] = cb
    if url:
        b["url"] = url
    if style:
        b["style"] = style
    if icon:
        b["icon_custom_emoji_id"] = E.get(icon, icon)
    return b


async def api_call(method: str, payload: dict):
    async with http_session() as session:
        async with session.post(f"{API_URL}/{method}", json=payload) as resp:
            return await resp.json()


async def send_msg(chat_id: int, text: str, kb: list = None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if kb:
        payload["reply_markup"] = {"inline_keyboard": kb}
    return await api_call("sendMessage", payload)


async def send_banner(chat_id: int, caption: str, kb: list = None):
    """Отправка баннера THEBENUA SHOP с подписью и клавиатурой."""
    global _banner_file_id
    if not os.path.exists(BANNER_PATH):
        return await send_msg(chat_id, caption, kb)
    form = aiohttp.FormData()
    form.add_field("chat_id", str(chat_id))
    form.add_field("caption", caption)
    form.add_field("parse_mode", "HTML")
    if kb:
        form.add_field("reply_markup", json.dumps({"inline_keyboard": kb}))
    if _banner_file_id:
        form.add_field("photo", _banner_file_id)
    else:
        form.add_field("photo", open(BANNER_PATH, "rb"), filename="banner.png")
    async with http_session() as session:
        async with session.post(f"{API_URL}/sendPhoto", data=form) as resp:
            data = await resp.json()
    if data.get("ok") and not _banner_file_id:
        photos = data["result"].get("photo", [])
        if photos:
            _banner_file_id = photos[-1]["file_id"]
    return data


# ---------------------------------------------------------------- keyboards


def main_menu_kb():
    return [
        [btn("Каталог", cb="catalog", style="success", icon="apps")],
        [btn("Профиль", cb="profile", style="primary", icon="profile")],
        [
            btn("Поддержка", cb="support", style="success", icon="people"),
            btn("Информация", cb="terms", style="success", icon="info"),
        ],
    ]


def profile_kb():
    return [
        [btn("Пополнить баланс", cb="topup", style="success", icon="send_money")],
        [btn("Мои покупки", cb="mypurchases", style="primary", icon="box")],
        [btn("Промокод", cb="promo", style="primary", icon="tag")],
        [btn("Главное меню", cb="menu", style="primary", icon="home")],
    ]


def back_menu_kb():
    return [[btn("Главное меню", cb="menu", style="primary", icon="home")]]


async def nav(callback: CallbackQuery):
    """Закрывает колбэк и удаляет старое сообщение — вместо него придёт новое."""
    await callback.answer()
    try:
        await callback.message.delete()
    except Exception:
        pass


WELCOME_TEXT = (
    f'{tge("party", "🛍")} <b>Добро пожаловать в THEBENUA SHOP!</b>\n\n'
    f'Выберите раздел ниже {tge("down", "👇")}'
)

# ---------------------------------------------------------------- FSM states


class AdminStates(StatesGroup):
    cat_name = State()
    prod_category = State()
    prod_name = State()
    prod_price = State()
    prod_qty = State()
    prod_content = State()
    prod_desc = State()
    prod_items = State()
    prod_edit_value = State()
    give_user = State()
    give_amount = State()
    promo_code = State()
    promo_amount = State()
    promo_uses = State()
    bc_message = State()
    bc_button = State()
    bc_confirm = State()


class UserStates(StatesGroup):
    promo_enter = State()
    topup_amount = State()


# ---------------------------------------------------------------- /start /help


@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    is_new = add_user(
        message.from_user.id, message.from_user.username or message.from_user.first_name
    )
    if is_new:
        await notify_admins_new_user(message.from_user)
    await send_banner(message.chat.id, WELCOME_TEXT, main_menu_kb())


async def notify_admins_new_user(user):
    uname = f"@{user.username}" if user.username else "—"
    link = f"tg://user?id={user.id}"
    text = (
        f'{tge("bell", "🔔")} <b>Новый пользователь!</b>\n\n'
        f'{tge("profile", "👤")} Имя: <a href="{link}">{user.first_name}</a>\n'
        f'{tge("tag", "🆔")} ID: <code>{user.id}</code>\n'
        f'{tge("link", "🔗")} Юзернейм: {uname}'
    )
    kb = [[btn("Профиль", url=link, style="primary", icon="profile")]]
    for admin_id in ADMIN_IDS:
        try:
            await send_msg(admin_id, text, kb)
        except Exception:
            pass


@dp.message(Command("help"))
async def cmd_help(message: Message):
    text = (
        f'{tge("info", "ℹ️")} <b>Помощь</b>\n\n'
        "/start — главное меню\n"
        "/help — помощь\n\n"
        f'{tge("support", "🆘")} Поддержка: {SUPPORT_URL}'
    )
    await send_msg(message.chat.id, text, back_menu_kb())


@dp.callback_query(F.data == "menu")
async def cb_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await nav(callback)
    await send_banner(callback.message.chat.id, WELCOME_TEXT, main_menu_kb())


# ---------------------------------------------------------------- catalog


@dp.callback_query(F.data == "catalog")
async def cb_catalog(callback: CallbackQuery):
    conn = db()
    cats = conn.execute("SELECT * FROM categories ORDER BY id").fetchall()
    conn.close()
    kb = [[btn(c["name"], cb=f"cat:{c['id']}", style="success", icon=cat_icon(c["name"]))] for c in cats]
    kb.append([btn("Заказать разработку", url="https://thebenua.online/order.html", style="success", icon="code")])
    kb.append([btn("Главное меню", cb="menu", style="primary", icon="home")])
    await nav(callback)
    await send_banner(
        callback.message.chat.id,
        f'{tge("gift", "🎁")} <b>Выберите категорию:</b>',
        kb,
    )


@dp.callback_query(F.data.startswith("cat:"))
async def cb_category(callback: CallbackQuery):
    cat_id = int(callback.data.split(":")[1])
    conn = db()
    cat = conn.execute("SELECT * FROM categories WHERE id=?", (cat_id,)).fetchone()
    prods = conn.execute(
        "SELECT * FROM products WHERE category_id=? AND qty>0 ORDER BY id", (cat_id,)
    ).fetchall()
    conn.close()
    await nav(callback)
    if not prods:
        await send_msg(
            callback.message.chat.id,
            f'{tge("cross", "😔")} В этой категории пока нет товаров в наличии.',
            [[btn("Назад", cb="catalog", style="primary", icon="down")]],
        )
        return
    kb = [
        [btn(f"{p['name']} — {p['price']:g} ₽ ({p['qty']} шт.)", cb=f"prod:{p['id']}", style="success", icon="box")]
        for p in prods
    ]
    kb.append([btn("Назад", cb="catalog", style="primary", icon="down")])
    await send_msg(
        callback.message.chat.id,
        f'{tge("gift", "🎁")} <b>{cat["name"]}</b>\n\nВыберите товар:',
        kb,
    )


@dp.callback_query(F.data.startswith("prod:"))
async def cb_product(callback: CallbackQuery):
    prod_id = int(callback.data.split(":")[1])
    conn = db()
    p = conn.execute("SELECT * FROM products WHERE id=?", (prod_id,)).fetchone()
    conn.close()
    await nav(callback)
    if not p or p["qty"] <= 0:
        await send_msg(callback.message.chat.id, f'{tge("cross", "😔")} Товар закончился.', back_menu_kb())
        return
    stars = math.ceil(p["price"] / STARS_RATE)
    desc = p["description"] if "description" in p.keys() and p["description"] else ""
    text = (
        f'{tge("gift", "🎁")} <b>{p["name"]}</b>\n\n'
        + (f"{desc}\n\n" if desc else "")
        + f'{tge("money", "💵")} Цена: <b>{p["price"]:g} ₽</b> / <b>{stars} ⭐</b>\n'
        f'{tge("box", "📦")} В наличии: <b>{p["qty"]} шт.</b>'
    )
    kb = [
        [btn("Купить", cb=f"buy:{p['id']}", style="success", icon="receive_money")],
        [btn("Назад", cb=f"cat:{p['category_id']}", style="primary", icon="down")],
    ]
    await send_msg(callback.message.chat.id, text, kb)


@dp.callback_query(F.data.startswith("buy:"))
async def cb_buy(callback: CallbackQuery):
    prod_id = int(callback.data.split(":")[1])
    conn = db()
    p = conn.execute("SELECT * FROM products WHERE id=?", (prod_id,)).fetchone()
    conn.close()
    await nav(callback)
    if not p or p["qty"] <= 0:
        await send_msg(callback.message.chat.id, f'{tge("cross", "😔")} Товар закончился.', back_menu_kb())
        return
    disc = user_discount(callback.from_user.id)
    price = price_after_discount(p["price"], disc)
    user = get_user(callback.from_user.id)
    bal = user["balance"] if user else 0
    bal_part = round(min(bal, price), 2)
    remainder = round(price - bal_part, 2)
    stars = math.ceil(remainder / STARS_RATE) if remainder > 0 else 0
    text = f'{tge("send_money", "💳")} <b>Оплата: {p["name"]}</b>\n\n'
    if disc:
        text += f'{tge("tag", "🏷")} Скидка по промокоду: <b>{disc:g}%</b>\n'
    text += f'Сумма: <b>{price:g} ₽</b>\n'
    if bal_part > 0 and remainder > 0:
        text += (
            f'{tge("wallet", "💰")} С баланса спишется: <b>{bal_part:g} ₽</b>\n'
            f'К доплате: <b>{remainder:g} ₽</b> / <b>{stars} ⭐</b>\n'
        )
    text += "\nВыберите способ оплаты:"
    kb = []
    if remainder <= 0:
        kb.append([btn("Оплатить с баланса", cb=f"paybal:{p['id']}", style="success", icon="wallet")])
    else:
        cb_label = "CryptoBot" if bal_part <= 0 else f"Баланс + CryptoBot"
        st_label = f"Звёзды ({stars} ⭐)" if bal_part <= 0 else f"Баланс + Звёзды ({stars} ⭐)"
        kb.append([btn(cb_label, cb=f"paycb:{p['id']}", style="primary", icon="crypto")])
        kb.append([btn(st_label, cb=f"paystars:{p['id']}", style="success", icon="money")])
    kb.append([btn("Отмена", cb=f"prod:{p['id']}", style="danger", icon="cross")])
    await send_msg(callback.message.chat.id, text, kb)


# ---------------------------------------------------------------- delivery


async def deliver(user_id: int, chat_id: int, product_id: int, method: str, amount: float = None):
    conn = db()
    p = conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
    if not p or p["qty"] <= 0:
        conn.close()
        await send_msg(chat_id, f'{tge("cross", "😔")} Товар закончился. Обратитесь в поддержку.', back_menu_kb())
        return False
    if amount is None:
        amount = p["price"]
    content = p["content"]
    if "delivery" in p.keys() and p["delivery"] == "unique":
        item = conn.execute(
            "SELECT * FROM product_items WHERE product_id=? AND used=0 ORDER BY id LIMIT 1",
            (product_id,),
        ).fetchone()
        if not item:
            conn.close()
            await send_msg(chat_id, f'{tge("cross", "😔")} Товар закончился. Обратитесь в поддержку.', back_menu_kb())
            return False
        conn.execute("UPDATE product_items SET used=1 WHERE id=?", (item["id"],))
        content = item["content"]
    conn.execute("UPDATE products SET qty=qty-1 WHERE id=?", (product_id,))
    conn.execute("UPDATE users SET spent=spent+?, discount=0 WHERE user_id=?", (amount, user_id))
    cur = conn.execute(
        "INSERT INTO purchases(user_id, product_id, product_name, amount, method, created_at) VALUES(?,?,?,?,?,?)",
        (user_id, product_id, p["name"], amount, method, int(time.time())),
    )
    purchase_id = cur.lastrowid
    if method != "balance":
        conn.execute(
            "INSERT INTO payments(user_id, amount, method, kind, created_at) VALUES(?,?,?,?,?)",
            (user_id, amount, method, "purchase", int(time.time())),
        )
    conn.commit()
    qty_left = p["qty"] - 1
    conn.close()
    text = (
        f'{tge("check", "✅")} <b>Оплата прошла успешно!</b>\n\n'
        f'{tge("gift", "🎁")} Ваш товар: <b>{p["name"]}</b>\n\n'
        f'{content}'
    )
    await send_msg(chat_id, text, back_menu_kb())
    buyer = get_user(user_id)
    uname = f'@{buyer["username"]}' if buyer and buyer["username"] else "—"
    sale_text = (
        f'{tge("bell", "🔔")} <b>Новая продажа!</b>\n\n'
        f'{tge("gift", "🎁")} Товар: <b>{p["name"]}</b>\n'
        f'{tge("money", "💵")} Сумма: <b>{amount:g} ₽</b> ({method})\n'
        f'{tge("profile", "👤")} Покупатель: <a href="tg://user?id={user_id}">{uname}</a> (<code>{user_id}</code>)\n'
        f'{tge("box", "📦")} Остаток: <b>{qty_left} шт.</b>'
    )
    for admin_id in ADMIN_IDS:
        try:
            await send_msg(admin_id, sale_text)
            if qty_left <= 0:
                await send_msg(
                    admin_id,
                    f'{tge("cross", "⚠️")} <b>Товар закончился!</b>\n\n'
                    f'{tge("box", "📦")} <b>{p["name"]}</b> — остаток 0 шт.',
                )
        except Exception:
            pass
    asyncio.create_task(ask_rating(chat_id, user_id, purchase_id, p["name"]))
    return True


async def ask_rating(chat_id: int, user_id: int, purchase_id: int, product_name: str):
    """Через несколько минут после выдачи просим оценить покупку."""
    await asyncio.sleep(180)
    conn = db()
    rated = conn.execute("SELECT 1 FROM ratings WHERE purchase_id=?", (purchase_id,)).fetchone()
    conn.close()
    if rated:
        return
    kb = [[btn(f"{n} ⭐", cb=f"rate:{purchase_id}:{n}", style="primary") for n in range(1, 6)]]
    try:
        await send_msg(
            chat_id,
            f'{tge("smile", "🙂")} Как вам <b>{product_name}</b>?\nОцените покупку — это займёт одно нажатие:',
            kb,
        )
    except Exception:
        pass


@dp.callback_query(F.data.startswith("rate:"))
async def cb_rate(callback: CallbackQuery):
    _, purchase_id, stars = callback.data.split(":")
    purchase_id, stars = int(purchase_id), int(stars)
    if not 1 <= stars <= 5:
        return
    conn = db()
    purchase = conn.execute("SELECT * FROM purchases WHERE id=?", (purchase_id,)).fetchone()
    if not purchase or purchase["user_id"] != callback.from_user.id:
        conn.close()
        await callback.answer("Покупка не найдена", show_alert=True)
        return
    cur = conn.execute(
        "INSERT OR IGNORE INTO ratings(purchase_id, user_id, stars, created_at) VALUES(?,?,?,?)",
        (purchase_id, callback.from_user.id, stars, int(time.time())),
    )
    is_new = cur.rowcount > 0
    conn.commit()
    conn.close()
    if not is_new:
        await callback.answer("Вы уже оценили эту покупку")
        return
    await nav(callback)
    await send_msg(callback.message.chat.id, f'{tge("check", "✅")} Спасибо за оценку!', back_menu_kb())
    uname = f"@{callback.from_user.username}" if callback.from_user.username else callback.from_user.first_name
    rate_text = (
        f'{tge("stats", "⭐")} <b>Оценка покупки: {"⭐" * stars} ({stars}/5)</b>\n\n'
        f'{tge("gift", "🎁")} Товар: <b>{purchase["product_name"]}</b>\n'
        f'{tge("profile", "👤")} Покупатель: {uname} (<code>{callback.from_user.id}</code>)'
    )
    for admin_id in ADMIN_IDS:
        try:
            await send_msg(admin_id, rate_text)
        except Exception:
            pass


# ---------------------------------------------------------------- balance pay


@dp.callback_query(F.data.startswith("paybal:"))
async def cb_pay_balance(callback: CallbackQuery):
    prod_id = int(callback.data.split(":")[1])
    user = get_user(callback.from_user.id)
    conn = db()
    p = conn.execute("SELECT * FROM products WHERE id=?", (prod_id,)).fetchone()
    conn.close()
    if not p or p["qty"] <= 0:
        await callback.answer("Товар закончился", show_alert=True)
        return
    price = price_after_discount(p["price"], user_discount(callback.from_user.id))
    if not user or user["balance"] < price:
        await callback.answer("Недостаточно средств на балансе", show_alert=True)
        return
    conn = db()
    conn.execute(
        "UPDATE users SET balance=balance-? WHERE user_id=?", (price, callback.from_user.id)
    )
    conn.commit()
    conn.close()
    await nav(callback)
    await deliver(callback.from_user.id, callback.message.chat.id, prod_id, "balance", amount=price)


# ---------------------------------------------------------------- stars pay


@dp.callback_query(F.data.startswith("paystars:"))
async def cb_pay_stars(callback: CallbackQuery):
    prod_id = int(callback.data.split(":")[1])
    conn = db()
    p = conn.execute("SELECT * FROM products WHERE id=?", (prod_id,)).fetchone()
    conn.close()
    if not p or p["qty"] <= 0:
        await callback.answer("Товар закончился", show_alert=True)
        return
    user = get_user(callback.from_user.id)
    price = price_after_discount(p["price"], user_discount(callback.from_user.id))
    bal_part = round(min(user["balance"] if user else 0, price), 2)
    remainder = round(price - bal_part, 2)
    if remainder <= 0:
        await callback.answer("Хватает баланса — оплатите с баланса", show_alert=True)
        return
    stars = math.ceil(remainder / STARS_RATE)
    await nav(callback)
    await bot.send_invoice(
        chat_id=callback.message.chat.id,
        title=p["name"][:32],
        description=f"Покупка: {p['name']}",
        payload=f"buy:{prod_id}:{bal_part:g}:{price:g}",
        currency="XTR",
        prices=[LabeledPrice(label=p["name"][:32], amount=stars)],
    )


@dp.callback_query(F.data.startswith("topstars:"))
async def cb_topup_stars(callback: CallbackQuery):
    amount = float(callback.data.split(":")[1])
    stars = math.ceil(amount / STARS_RATE)
    await nav(callback)
    await bot.send_invoice(
        chat_id=callback.message.chat.id,
        title="Пополнение баланса",
        description=f"Пополнение баланса на {amount:g} ₽",
        payload=f"topup:{amount:g}",
        currency="XTR",
        prices=[LabeledPrice(label="Пополнение", amount=stars)],
    )


@dp.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    await query.answer(ok=True)


@dp.message(F.successful_payment)
async def on_successful_payment(message: Message):
    payload = message.successful_payment.invoice_payload
    user_id = message.from_user.id
    if payload.startswith("buy:"):
        parts = payload.split(":")
        prod_id = int(parts[1])
        bal_part = float(parts[2]) if len(parts) > 2 else 0
        total = float(parts[3]) if len(parts) > 3 else None
        if bal_part > 0:
            conn = db()
            conn.execute("UPDATE users SET balance=balance-? WHERE user_id=?", (bal_part, user_id))
            conn.commit()
            conn.close()
        await deliver(user_id, message.chat.id, prod_id, "stars", amount=total)
    elif payload.startswith("topup:"):
        amount = float(payload.split(":")[1])
        conn = db()
        conn.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (amount, user_id))
        conn.execute(
            "INSERT INTO payments(user_id, amount, method, kind, created_at) VALUES(?,?,?,?,?)",
            (user_id, amount, "stars", "topup", int(time.time())),
        )
        conn.commit()
        conn.close()
        await send_msg(
            message.chat.id,
            f'{tge("check", "✅")} Баланс пополнен на <b>{amount:g} ₽</b>!',
            back_menu_kb(),
        )


# ---------------------------------------------------------------- cryptobot pay


async def crypto_create_invoice(amount_rub: float, description: str, payload: str):
    async with http_session() as session:
        async with session.post(
            f"{CRYPTO_API}/createInvoice",
            headers={"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN},
            json={
                "currency_type": "fiat",
                "fiat": "RUB",
                "amount": f"{amount_rub:.2f}",
                "description": description,
                "payload": payload,
                "expires_in": 1800,
            },
        ) as resp:
            data = await resp.json()
    if not data.get("ok"):
        return None
    return data["result"]


async def crypto_check_invoice(invoice_id: int):
    async with http_session() as session:
        async with session.get(
            f"{CRYPTO_API}/getInvoices",
            headers={"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN},
            params={"invoice_ids": str(invoice_id)},
        ) as resp:
            data = await resp.json()
    if not data.get("ok"):
        return None
    items = data["result"].get("items", [])
    return items[0] if items else None


async def watch_crypto_invoice(invoice_id: int, user_id: int, chat_id: int, kind: str, value: float, bal_part: float = 0, total: float = None, pay_url: str = None):
    """Ждём оплату счёта CryptoBot и сразу выдаём товар / пополняем баланс."""
    reminded = False
    for i in range(360):  # до 30 минут, каждые 5 секунд
        await asyncio.sleep(5)
        inv = await crypto_check_invoice(invoice_id)
        if inv is None:
            continue
        # одно напоминание через ~10 минут: возвращает часть «брошенных» оплат
        if not reminded and i >= 120 and inv["status"] == "active":
            reminded = True
            kb = [[btn("Оплатить", url=pay_url, style="success", icon="crypto")]] if pay_url else None
            await send_msg(
                chat_id,
                f'{tge("bell", "🔔")} Напоминаем: счёт ещё активен. '
                "После оплаты бот всё пришлёт автоматически.",
                kb,
            )
        if inv["status"] == "paid":
            if kind == "buy":
                if bal_part > 0:
                    conn = db()
                    conn.execute("UPDATE users SET balance=balance-? WHERE user_id=?", (bal_part, user_id))
                    conn.commit()
                    conn.close()
                await deliver(user_id, chat_id, int(value), "cryptobot", amount=total)
            else:
                conn = db()
                conn.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (value, user_id))
                conn.execute(
                    "INSERT INTO payments(user_id, amount, method, kind, created_at) VALUES(?,?,?,?,?)",
                    (user_id, value, "cryptobot", "topup", int(time.time())),
                )
                conn.commit()
                conn.close()
                await send_msg(chat_id, f'{tge("check", "✅")} Баланс пополнен на <b>{value:g} ₽</b>!', back_menu_kb())
            return
        if inv["status"] == "expired":
            retry_cb = f"paycb:{int(value)}" if kind == "buy" else f"topcb:{value:g}"
            kb = [
                [btn("Создать новый счёт", cb=retry_cb, style="success", icon="crypto")],
                [btn("Главное меню", cb="menu", style="primary", icon="home")],
            ]
            await send_msg(chat_id, f'{tge("clock", "⏰")} Счёт истёк — ничего страшного, можно создать новый.', kb)
            return


@dp.callback_query(F.data.startswith("paycb:"))
async def cb_pay_crypto(callback: CallbackQuery):
    prod_id = int(callback.data.split(":")[1])
    conn = db()
    p = conn.execute("SELECT * FROM products WHERE id=?", (prod_id,)).fetchone()
    conn.close()
    if not p or p["qty"] <= 0:
        await callback.answer("Товар закончился", show_alert=True)
        return
    user = get_user(callback.from_user.id)
    price = price_after_discount(p["price"], user_discount(callback.from_user.id))
    bal_part = round(min(user["balance"] if user else 0, price), 2)
    remainder = round(price - bal_part, 2)
    if remainder <= 0:
        await callback.answer("Хватает баланса — оплатите с баланса", show_alert=True)
        return
    await nav(callback)
    inv = await crypto_create_invoice(remainder, f"Покупка: {p['name']}", f"buy:{prod_id}")
    if not inv:
        await send_msg(callback.message.chat.id, f'{tge("cross", "❌")} Не удалось создать счёт. Попробуйте позже.', back_menu_kb())
        return
    text = (
        f'{tge("crypto", "🪙")} <b>Счёт создан!</b>\n\n'
        f'Сумма: <b>{remainder:g} ₽</b>\n'
        + (f'{tge("wallet", "💰")} С баланса спишется: <b>{bal_part:g} ₽</b>\n' if bal_part > 0 else "")
        + "Оплатите по кнопке ниже — после оплаты бот сразу пришлёт вам товар."
    )
    kb = [
        [btn("Оплатить", url=inv["bot_invoice_url"], style="success", icon="crypto")],
        [btn("Отмена", cb=f"prod:{prod_id}", style="danger", icon="cross")],
    ]
    await send_msg(callback.message.chat.id, text, kb)
    asyncio.create_task(
        watch_crypto_invoice(inv["invoice_id"], callback.from_user.id, callback.message.chat.id, "buy", prod_id, bal_part=bal_part, total=price, pay_url=inv["bot_invoice_url"])
    )


@dp.callback_query(F.data.startswith("topcb:"))
async def cb_topup_crypto(callback: CallbackQuery):
    amount = float(callback.data.split(":")[1])
    await nav(callback)
    inv = await crypto_create_invoice(amount, "Пополнение баланса", f"topup:{amount:g}")
    if not inv:
        await send_msg(callback.message.chat.id, f'{tge("cross", "❌")} Не удалось создать счёт. Попробуйте позже.', back_menu_kb())
        return
    text = (
        f'{tge("crypto", "🪙")} <b>Счёт создан!</b>\n\n'
        f'Сумма: <b>{amount:g} ₽</b>\n'
        "Оплатите по кнопке ниже — баланс будет зачислен автоматически."
    )
    kb = [
        [btn("Оплатить", url=inv["bot_invoice_url"], style="success", icon="crypto")],
        [btn("Отмена", cb="profile", style="danger", icon="cross")],
    ]
    await send_msg(callback.message.chat.id, text, kb)
    asyncio.create_task(
        watch_crypto_invoice(inv["invoice_id"], callback.from_user.id, callback.message.chat.id, "topup", amount, pay_url=inv["bot_invoice_url"])
    )


# ---------------------------------------------------------------- profile


@dp.callback_query(F.data == "profile")
async def cb_profile(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    user = get_user(callback.from_user.id)
    if not user:
        add_user(callback.from_user.id, callback.from_user.username or callback.from_user.first_name)
        user = get_user(callback.from_user.id)
    text = (
        f'{tge("profile", "👤")} <b>Профиль</b>\n\n'
        f'{tge("profile", "👤")} Имя: <b>{callback.from_user.first_name}</b>\n'
        f'{tge("tag", "🆔")} ID: <code>{user["user_id"]}</code>\n'
        f'{tge("wallet", "💰")} Баланс: <b>{user["balance"]:g} ₽</b>\n'
        f'{tge("gift", "🎁")} Потрачено: <b>{user["spent"]:g} ₽</b>'
        + (f'\n{tge("tag", "🏷")} Скидка на следующую покупку: <b>{user["discount"]:g}%</b>' if "discount" in user.keys() and user["discount"] else "")
    )
    await nav(callback)
    await send_banner(callback.message.chat.id, text, profile_kb())


@dp.callback_query(F.data == "topup")
async def cb_topup(callback: CallbackQuery, state: FSMContext):
    await nav(callback)
    await state.set_state(UserStates.topup_amount)
    await send_msg(
        callback.message.chat.id,
        f'{tge("send_money", "💸")} Введите сумму пополнения в рублях (например: <code>100</code>):',
        [[btn("Отмена", cb="profile", style="danger", icon="cross")]],
    )


@dp.message(UserStates.topup_amount)
async def st_topup_amount(message: Message, state: FSMContext):
    try:
        amount = float(message.text.replace(",", "."))
        assert 10 <= amount <= 100000
    except (ValueError, AssertionError):
        await send_msg(message.chat.id, f'{tge("cross", "❌")} Введите сумму от 10 до 100000 ₽.')
        return
    await state.clear()
    stars = math.ceil(amount / STARS_RATE)
    kb = [
        [btn("CryptoBot", cb=f"topcb:{amount:g}", style="primary", icon="crypto")],
        [btn(f"Звёзды ({stars} ⭐)", cb=f"topstars:{amount:g}", style="success", icon="money")],
        [btn("Отмена", cb="profile", style="danger", icon="cross")],
    ]
    await send_msg(
        message.chat.id,
        f'{tge("send_money", "💳")} Пополнение на <b>{amount:g} ₽</b>\n\nВыберите способ оплаты:',
        kb,
    )


@dp.callback_query(F.data == "mypurchases")
async def cb_mypurchases(callback: CallbackQuery):
    conn = db()
    rows = conn.execute(
        "SELECT * FROM purchases WHERE user_id=? ORDER BY created_at DESC LIMIT 30",
        (callback.from_user.id,),
    ).fetchall()
    conn.close()
    await nav(callback)
    if not rows:
        await send_msg(
            callback.message.chat.id,
            f'{tge("box", "📦")} У вас пока нет покупок.',
            [[btn("Назад", cb="profile", style="primary", icon="down")]],
        )
        return
    lines = "\n".join(
        f'• <b>{r["product_name"]}</b> — {r["amount"]:g} ₽ '
        f'({time.strftime("%d.%m.%Y %H:%M", time.localtime(r["created_at"]))})'
        for r in rows
    )
    await send_msg(
        callback.message.chat.id,
        f'{tge("box", "📦")} <b>Мои покупки</b>\n\n{lines}',
        [[btn("Назад", cb="profile", style="primary", icon="down")]],
    )


@dp.callback_query(F.data == "promo")
async def cb_promo(callback: CallbackQuery, state: FSMContext):
    await nav(callback)
    await state.set_state(UserStates.promo_enter)
    await send_msg(
        callback.message.chat.id,
        f'{tge("tag", "🎟")} Введите промокод:',
        [[btn("Отмена", cb="profile", style="danger", icon="cross")]],
    )


@dp.message(UserStates.promo_enter)
async def st_promo_enter(message: Message, state: FSMContext):
    code = message.text.strip()
    conn = db()
    promo = conn.execute("SELECT * FROM promocodes WHERE code=?", (code,)).fetchone()
    if not promo or promo["uses_left"] <= 0:
        conn.close()
        await send_msg(message.chat.id, f'{tge("cross", "❌")} Промокод не найден или закончился.', back_menu_kb())
        await state.clear()
        return
    used = conn.execute(
        "SELECT 1 FROM promo_activations WHERE code=? AND user_id=?", (code, message.from_user.id)
    ).fetchone()
    if used:
        conn.close()
        await send_msg(message.chat.id, f'{tge("cross", "❌")} Вы уже активировали этот промокод.', back_menu_kb())
        await state.clear()
        return
    conn.execute("UPDATE promocodes SET uses_left=uses_left-1 WHERE code=?", (code,))
    conn.execute("INSERT INTO promo_activations(code, user_id) VALUES(?,?)", (code, message.from_user.id))
    ptype = promo["ptype"] if "ptype" in promo.keys() and promo["ptype"] else "balance"
    if ptype == "percent":
        conn.execute("UPDATE users SET discount=? WHERE user_id=?", (promo["amount"], message.from_user.id))
        result = f'{tge("check", "✅")} Промокод активирован! Скидка <b>{promo["amount"]:g}%</b> применится к вашей следующей покупке.'
    else:
        conn.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (promo["amount"], message.from_user.id))
        result = f'{tge("check", "✅")} Промокод активирован! Баланс пополнен на <b>{promo["amount"]:g} ₽</b>.'
    conn.commit()
    conn.close()
    await state.clear()
    await send_msg(message.chat.id, result, back_menu_kb())


# ---------------------------------------------------------------- info / about / lang


@dp.callback_query(F.data == "support")
async def cb_support(callback: CallbackQuery):
    await nav(callback)
    await send_msg(
        callback.message.chat.id,
        f'{tge("support", "🆘")} Помощь: {SUPPORT_CONTACT}',
        back_menu_kb(),
    )


@dp.callback_query(F.data == "terms")
async def cb_terms(callback: CallbackQuery):
    text = (
        f'{tge("info", "ℹ️")} <b>Информация</b>\n\n'
        "Пользовательское соглашение:\n"
        "https://telegra.ph/Polzovatelskoe-soglashenie-04-01-19"
    )
    await nav(callback)
    await send_msg(callback.message.chat.id, text, back_menu_kb())


# ---------------------------------------------------------------- admin


def admin_kb():
    return [
        [btn("Создать категорию", cb="adm:newcat", style="success", icon="file")],
        [btn("Создать товар", cb="adm:newprod", style="success", icon="box")],
        [btn("Товары", cb="adm:prods", style="primary", icon="pencil")],
        [btn("Выдать баланс", cb="adm:give", style="success", icon="wallet")],
        [btn("Создать промокод", cb="adm:newpromo", style="primary", icon="tag")],
        [btn("Статистика", cb="adm:stats", style="primary", icon="stats")],
        [btn("Рассылка", cb="adm:broadcast", style="danger", icon="megaphone")],
    ]


@dp.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    await send_msg(message.chat.id, f'{tge("settings", "🛠")} <b>Админ-панель</b>\n\nВыберите действие:', admin_kb())


@dp.message(Command("backup"))
async def cmd_backup(message: Message):
    """Резервная копия базы: вся экономика бота лежит в одном SQLite-файле."""
    if not is_admin(message.from_user.id):
        return
    if not os.path.exists(DB_PATH):
        await send_msg(message.chat.id, f'{tge("cross", "❌")} Файл базы не найден.')
        return
    stamp = time.strftime("%Y-%m-%d_%H-%M")
    await message.answer_document(
        FSInputFile(DB_PATH, filename=f"shop-backup-{stamp}.db"),
        caption=f'{tge("download", "💾")} Резервная копия базы — {stamp}',
    )


@dp.callback_query(F.data == "adm:panel")
async def cb_admin_panel(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.clear()
    await nav(callback)
    await send_msg(
        callback.message.chat.id,
        f'{tge("settings", "🛠")} <b>Админ-панель</b>\n\nВыберите действие:',
        admin_kb(),
    )


ADMIN_CANCEL_KB = [[btn("Отмена", cb="adm:panel", style="danger", icon="cross")]]


@dp.callback_query(F.data == "adm:newcat")
async def cb_adm_newcat(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await nav(callback)
    await state.set_state(AdminStates.cat_name)
    await send_msg(callback.message.chat.id, f'{tge("file", "📁")} Введите название категории:', ADMIN_CANCEL_KB)


@dp.message(AdminStates.cat_name)
async def st_cat_name(message: Message, state: FSMContext):
    conn = db()
    conn.execute("INSERT OR IGNORE INTO categories(name) VALUES(?)", (message.text.strip(),))
    conn.commit()
    conn.close()
    await state.clear()
    await send_msg(message.chat.id, f'{tge("check", "✅")} Категория <b>{message.text.strip()}</b> создана.', admin_kb())


@dp.callback_query(F.data == "adm:newprod")
async def cb_adm_newprod(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await nav(callback)
    conn = db()
    cats = conn.execute("SELECT * FROM categories ORDER BY id").fetchall()
    conn.close()
    if not cats:
        await send_msg(callback.message.chat.id, f'{tge("cross", "❌")} Сначала создайте категорию.', admin_kb())
        return
    kb = [[btn(c["name"], cb=f"adm:prodcat:{c['id']}", style="success", icon="file")] for c in cats]
    kb.append([btn("Отмена", cb="adm:panel", style="danger", icon="cross")])
    await state.set_state(AdminStates.prod_category)
    await send_msg(callback.message.chat.id, f'{tge("file", "📁")} Выберите категорию товара:', kb)


@dp.callback_query(F.data.startswith("adm:prodcat:"))
async def cb_adm_prodcat(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await nav(callback)
    await state.update_data(category_id=int(callback.data.split(":")[2]))
    await state.set_state(AdminStates.prod_name)
    await send_msg(callback.message.chat.id, f'{tge("pencil", "📝")} Введите название товара:', ADMIN_CANCEL_KB)


@dp.message(AdminStates.prod_name)
async def st_prod_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await state.set_state(AdminStates.prod_price)
    await send_msg(message.chat.id, f'{tge("money", "💵")} Введите цену в рублях (например: <code>100</code>):', ADMIN_CANCEL_KB)


@dp.message(AdminStates.prod_price)
async def st_prod_price(message: Message, state: FSMContext):
    try:
        price = float(message.text.replace(",", "."))
        assert price > 0
    except (ValueError, AssertionError):
        await send_msg(message.chat.id, f'{tge("cross", "❌")} Введите корректную цену.')
        return
    await state.update_data(price=price)
    kb = [
        [btn("Одинаковый товар", cb="adm:dlv:same", style="success", icon="box")],
        [btn("Разные товары", cb="adm:dlv:uniq", style="primary", icon="apps")],
        [btn("Отмена", cb="adm:panel", style="danger", icon="cross")],
    ]
    await send_msg(
        message.chat.id,
        f'{tge("box", "📦")} <b>Тип выдачи</b>\n\n'
        "• <b>Одинаковый товар</b> — все покупатели получают одно и то же содержимое\n"
        "• <b>Разные товары</b> — каждый покупатель получает свой уникальный товар по очереди",
        kb,
    )


@dp.callback_query(F.data.startswith("adm:dlv:"))
async def cb_adm_delivery(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    mode = "unique" if callback.data.endswith("uniq") else "same"
    await state.update_data(delivery=mode)
    await nav(callback)
    if mode == "same":
        await state.set_state(AdminStates.prod_qty)
        await send_msg(callback.message.chat.id, f'{tge("box", "📦")} Введите количество товара:', ADMIN_CANCEL_KB)
    else:
        await state.set_state(AdminStates.prod_desc)
        await send_msg(
            callback.message.chat.id,
            f'{tge("pencil", "📝")} Введите описание товара (или <code>-</code>, чтобы пропустить):',
            ADMIN_CANCEL_KB,
        )


@dp.message(AdminStates.prod_qty)
async def st_prod_qty(message: Message, state: FSMContext):
    try:
        qty = int(message.text)
        assert qty >= 0
    except (ValueError, AssertionError):
        await send_msg(message.chat.id, f'{tge("cross", "❌")} Введите корректное количество.')
        return
    await state.update_data(qty=qty)
    await state.set_state(AdminStates.prod_desc)
    await send_msg(
        message.chat.id,
        f'{tge("pencil", "📝")} Введите описание товара (или <code>-</code>, чтобы пропустить):',
        ADMIN_CANCEL_KB,
    )


@dp.message(AdminStates.prod_desc)
async def st_prod_desc(message: Message, state: FSMContext):
    desc = message.text.strip()
    if desc == "-":
        desc = ""
    await state.update_data(description=desc)
    data = await state.get_data()
    if data.get("delivery") == "unique":
        await state.set_state(AdminStates.prod_items)
        await send_msg(
            message.chat.id,
            f'{tge("apps", "📦")} Отправьте товары для выдачи — <b>каждый с новой строки</b>.\n'
            "Количество товара = число строк. Покупатели будут получать их по очереди.",
            ADMIN_CANCEL_KB,
        )
        return
    await state.set_state(AdminStates.prod_content)
    await send_msg(
        message.chat.id,
        f'{tge("link", "🔗")} Введите содержимое товара (ссылка/данные, которые получит покупатель):',
        ADMIN_CANCEL_KB,
    )


@dp.message(AdminStates.prod_content)
async def st_prod_content(message: Message, state: FSMContext):
    data = await state.get_data()
    conn = db()
    conn.execute(
        "INSERT INTO products(category_id, name, price, qty, content, description, delivery) VALUES(?,?,?,?,?,?,?)",
        (data["category_id"], data["name"], data["price"], data["qty"], message.text.strip(), data.get("description", ""), "same"),
    )
    conn.commit()
    conn.close()
    await state.clear()
    await send_msg(
        message.chat.id,
        f'{tge("check", "✅")} Товар <b>{data["name"]}</b> создан — {data["price"]:g} ₽, {data["qty"]} шт.',
        admin_kb(),
    )


@dp.message(AdminStates.prod_items)
async def st_prod_items(message: Message, state: FSMContext):
    items = [l.strip() for l in message.text.split("\n") if l.strip()]
    if not items:
        await send_msg(message.chat.id, f'{tge("cross", "❌")} Отправьте хотя бы один товар.')
        return
    data = await state.get_data()
    conn = db()
    cur = conn.execute(
        "INSERT INTO products(category_id, name, price, qty, content, description, delivery) VALUES(?,?,?,?,?,?,?)",
        (data["category_id"], data["name"], data["price"], len(items), "", data.get("description", ""), "unique"),
    )
    prod_id = cur.lastrowid
    conn.executemany(
        "INSERT INTO product_items(product_id, content) VALUES(?,?)",
        [(prod_id, it) for it in items],
    )
    conn.commit()
    conn.close()
    await state.clear()
    await send_msg(
        message.chat.id,
        f'{tge("check", "✅")} Товар <b>{data["name"]}</b> создан — {data["price"]:g} ₽, {len(items)} шт. (разная выдача).',
        admin_kb(),
    )


# ---------------------------------------------------------------- admin: manage products


EDIT_FIELDS = {
    "name": ("название", "pencil", "📝"),
    "price": ("цену в рублях", "money", "💵"),
    "qty": ("количество", "box", "📦"),
    "description": ("описание (или - чтобы убрать)", "pencil", "📝"),
    "content": ("содержимое (то, что получает покупатель; для разной выдачи — каждый товар с новой строки)", "link", "🔗"),
}


@dp.callback_query(F.data == "adm:prods")
async def cb_adm_prods(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.clear()
    conn = db()
    prods = conn.execute(
        "SELECT p.*, c.name cat FROM products p LEFT JOIN categories c ON c.id=p.category_id ORDER BY p.id"
    ).fetchall()
    conn.close()
    await nav(callback)
    if not prods:
        await send_msg(callback.message.chat.id, f'{tge("cross", "❌")} Товаров пока нет.', admin_kb())
        return
    kb = [
        [btn(f"{p['name']} — {p['price']:g} ₽ ({p['qty']} шт.)", cb=f"adm:prod:{p['id']}", style="primary", icon="box")]
        for p in prods
    ]
    kb.append([btn("Назад", cb="adm:panel", style="primary", icon="down")])
    await send_msg(callback.message.chat.id, f'{tge("box", "📦")} <b>Товары</b>\n\nВыберите товар:', kb)


@dp.callback_query(F.data.startswith("adm:prod:"))
async def cb_adm_prod(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.clear()
    prod_id = int(callback.data.split(":")[2])
    conn = db()
    p = conn.execute("SELECT * FROM products WHERE id=?", (prod_id,)).fetchone()
    conn.close()
    await nav(callback)
    if not p:
        await send_msg(callback.message.chat.id, f'{tge("cross", "❌")} Товар не найден.', admin_kb())
        return
    desc = p["description"] or "—"
    is_unique = "delivery" in p.keys() and p["delivery"] == "unique"
    if is_unique:
        conn2 = db()
        items = conn2.execute(
            "SELECT content FROM product_items WHERE product_id=? AND used=0 ORDER BY id", (p["id"],)
        ).fetchall()
        conn2.close()
        content_line = "\n".join(f"• <code>{it['content']}</code>" for it in items) or "—"
        content_block = f'{tge("link", "🔗")} Товары к выдаче (разная выдача):\n{content_line}'
    else:
        content_block = f'{tge("link", "🔗")} Содержимое: <code>{p["content"]}</code>'
    text = (
        f'{tge("box", "📦")} <b>{p["name"]}</b>\n\n'
        f'{tge("money", "💵")} Цена: <b>{p["price"]:g} ₽</b>\n'
        f'{tge("box", "📦")} Количество: <b>{p["qty"]} шт.</b>\n'
        f'{tge("pencil", "📝")} Описание: {desc}\n'
        f'{content_block}'
    )
    kb = [
        [
            btn("Название", cb=f"adm:edit:name:{p['id']}", style="primary", icon="pencil"),
            btn("Цена", cb=f"adm:edit:price:{p['id']}", style="primary", icon="money"),
        ],
        [
            btn("Количество", cb=f"adm:edit:qty:{p['id']}", style="primary", icon="box"),
            btn("Описание", cb=f"adm:edit:description:{p['id']}", style="primary", icon="pencil"),
        ],
        [btn("Содержимое", cb=f"adm:edit:content:{p['id']}", style="primary", icon="link")],
        [btn("Удалить товар", cb=f"adm:delprod:{p['id']}", style="danger", icon="trash")],
        [btn("Назад", cb="adm:prods", style="primary", icon="down")],
    ]
    await send_msg(callback.message.chat.id, text, kb)


@dp.callback_query(F.data.startswith("adm:delprod:"))
async def cb_adm_delprod(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    prod_id = int(callback.data.split(":")[2])
    await nav(callback)
    kb = [
        [btn("Да, удалить", cb=f"adm:delprod2:{prod_id}", style="danger", icon="trash")],
        [btn("Отмена", cb=f"adm:prod:{prod_id}", style="primary", icon="down")],
    ]
    await send_msg(callback.message.chat.id, f'{tge("trash", "🗑")} Удалить товар?', kb)


@dp.callback_query(F.data.startswith("adm:delprod2:"))
async def cb_adm_delprod2(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    prod_id = int(callback.data.split(":")[2])
    conn = db()
    conn.execute("DELETE FROM products WHERE id=?", (prod_id,))
    conn.commit()
    conn.close()
    await nav(callback)
    await send_msg(callback.message.chat.id, f'{tge("check", "✅")} Товар удалён.', admin_kb())


@dp.callback_query(F.data.startswith("adm:edit:"))
async def cb_adm_edit(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    _, _, field, prod_id = callback.data.split(":")
    if field not in EDIT_FIELDS:
        return
    await nav(callback)
    label, icon, fb = EDIT_FIELDS[field]
    await state.set_state(AdminStates.prod_edit_value)
    await state.update_data(edit_field=field, edit_prod_id=int(prod_id))
    await send_msg(
        callback.message.chat.id,
        f'{tge(icon, fb)} Введите новое {label}:',
        [[btn("Отмена", cb=f"adm:prod:{prod_id}", style="danger", icon="cross")]],
    )


@dp.message(AdminStates.prod_edit_value)
async def st_prod_edit_value(message: Message, state: FSMContext):
    data = await state.get_data()
    field, prod_id = data["edit_field"], data["edit_prod_id"]
    value = message.text.strip()
    if field == "price":
        try:
            value = float(value.replace(",", "."))
            assert value > 0
        except (ValueError, AssertionError):
            await send_msg(message.chat.id, f'{tge("cross", "❌")} Введите корректную цену.')
            return
    elif field == "qty":
        try:
            value = int(value)
            assert value >= 0
        except (ValueError, AssertionError):
            await send_msg(message.chat.id, f'{tge("cross", "❌")} Введите корректное количество.')
            return
    elif field == "description" and value == "-":
        value = ""
    conn = db()
    p = conn.execute("SELECT * FROM products WHERE id=?", (prod_id,)).fetchone()
    if field == "content" and p and "delivery" in p.keys() and p["delivery"] == "unique":
        items = [l.strip() for l in message.text.split("\n") if l.strip()]
        conn.execute("DELETE FROM product_items WHERE product_id=? AND used=0", (prod_id,))
        conn.executemany(
            "INSERT INTO product_items(product_id, content) VALUES(?,?)",
            [(prod_id, it) for it in items],
        )
        conn.execute("UPDATE products SET qty=? WHERE id=?", (len(items), prod_id))
    else:
        conn.execute(f"UPDATE products SET {field}=? WHERE id=?", (value, prod_id))
    conn.commit()
    conn.close()
    await state.clear()
    await send_msg(
        message.chat.id,
        f'{tge("check", "✅")} Сохранено.',
        [[btn("К товару", cb=f"adm:prod:{prod_id}", style="primary", icon="box")]],
    )


# ---------------------------------------------------------------- admin: give balance


@dp.callback_query(F.data == "adm:give")
async def cb_adm_give(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await nav(callback)
    await state.set_state(AdminStates.give_user)
    await send_msg(
        callback.message.chat.id,
        f'{tge("profile", "👤")} Введите ID пользователя:',
        ADMIN_CANCEL_KB,
    )


@dp.message(AdminStates.give_user)
async def st_give_user(message: Message, state: FSMContext):
    try:
        uid = int(message.text.strip())
    except ValueError:
        await send_msg(message.chat.id, f'{tge("cross", "❌")} Введите числовой ID.')
        return
    if not get_user(uid):
        await send_msg(message.chat.id, f'{tge("cross", "❌")} Пользователь не найден в боте.')
        return
    await state.update_data(give_uid=uid)
    await state.set_state(AdminStates.give_amount)
    await send_msg(
        message.chat.id,
        f'{tge("money", "💵")} Введите сумму в рублях (можно отрицательную, чтобы списать):',
        ADMIN_CANCEL_KB,
    )


@dp.message(AdminStates.give_amount)
async def st_give_amount(message: Message, state: FSMContext):
    try:
        amount = float(message.text.replace(",", "."))
    except ValueError:
        await send_msg(message.chat.id, f'{tge("cross", "❌")} Введите корректную сумму.')
        return
    data = await state.get_data()
    uid = data["give_uid"]
    conn = db()
    conn.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (amount, uid))
    conn.commit()
    user = conn.execute("SELECT balance FROM users WHERE user_id=?", (uid,)).fetchone()
    conn.close()
    await state.clear()
    await send_msg(
        message.chat.id,
        f'{tge("check", "✅")} Баланс пользователя <code>{uid}</code> изменён на <b>{amount:g} ₽</b>. Текущий баланс: <b>{user["balance"]:g} ₽</b>.',
        admin_kb(),
    )
    try:
        await send_msg(uid, f'{tge("gift", "🎁")} Вам начислено <b>{amount:g} ₽</b> на баланс!' if amount > 0 else f'{tge("info", "ℹ️")} Ваш баланс изменён на <b>{amount:g} ₽</b>.', back_menu_kb())
    except Exception:
        pass


@dp.callback_query(F.data == "adm:newpromo")
async def cb_adm_newpromo(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await nav(callback)
    kb = [
        [btn("На баланс (₽)", cb="adm:ptype:balance", style="success", icon="wallet")],
        [btn("Скидка (%)", cb="adm:ptype:percent", style="primary", icon="tag")],
        [btn("Отмена", cb="adm:panel", style="danger", icon="cross")],
    ]
    await send_msg(callback.message.chat.id, f'{tge("tag", "🎟")} <b>Тип промокода:</b>', kb)


@dp.callback_query(F.data.startswith("adm:ptype:"))
async def cb_adm_ptype(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.update_data(ptype=callback.data.split(":")[2])
    await nav(callback)
    await state.set_state(AdminStates.promo_code)
    await send_msg(callback.message.chat.id, f'{tge("tag", "🎟")} Введите код промокода:', ADMIN_CANCEL_KB)


@dp.message(AdminStates.promo_code)
async def st_promo_code(message: Message, state: FSMContext):
    await state.update_data(code=message.text.strip())
    data = await state.get_data()
    await state.set_state(AdminStates.promo_amount)
    if data.get("ptype") == "percent":
        await send_msg(message.chat.id, f'{tge("tag", "🏷")} Введите размер скидки в процентах (1–100):', ADMIN_CANCEL_KB)
    else:
        await send_msg(message.chat.id, f'{tge("money", "💵")} Введите сумму промокода в рублях:', ADMIN_CANCEL_KB)


@dp.message(AdminStates.promo_amount)
async def st_promo_amount(message: Message, state: FSMContext):
    data = await state.get_data()
    try:
        amount = float(message.text.replace(",", "."))
        assert amount > 0
        if data.get("ptype") == "percent":
            assert amount <= 100
    except (ValueError, AssertionError):
        await send_msg(message.chat.id, f'{tge("cross", "❌")} Введите корректное значение.')
        return
    await state.update_data(amount=amount)
    await state.set_state(AdminStates.promo_uses)
    await send_msg(message.chat.id, f'{tge("addtext", "🔢")} Введите количество активаций:', ADMIN_CANCEL_KB)


@dp.message(AdminStates.promo_uses)
async def st_promo_uses(message: Message, state: FSMContext):
    try:
        uses = int(message.text)
        assert uses > 0
    except (ValueError, AssertionError):
        await send_msg(message.chat.id, f'{tge("cross", "❌")} Введите корректное число.')
        return
    data = await state.get_data()
    conn = db()
    ptype = data.get("ptype", "balance")
    conn.execute(
        "INSERT OR REPLACE INTO promocodes(code, amount, uses_left, ptype) VALUES(?,?,?,?)",
        (data["code"], data["amount"], uses, ptype),
    )
    conn.commit()
    conn.close()
    await state.clear()
    label = f'{data["amount"]:g}%' if ptype == "percent" else f'{data["amount"]:g} ₽'
    await send_msg(
        message.chat.id,
        f'{tge("check", "✅")} Промокод <code>{data["code"]}</code> на {label} ({uses} активаций) создан.',
        admin_kb(),
    )


# ---------------------------------------------------------------- stats


@dp.callback_query(F.data == "adm:stats")
async def cb_adm_stats(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    await nav(callback)
    now = int(time.time())
    conn = db()

    def income(since):
        row = conn.execute(
            "SELECT COALESCE(SUM(amount),0) s FROM payments WHERE created_at>=?", (since,)
        ).fetchone()
        return row["s"]

    users_count = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
    stock = conn.execute("SELECT COALESCE(SUM(qty),0) s FROM products").fetchone()["s"]
    day, week, month, year = income(now - 86400), income(now - 604800), income(now - 2592000), income(now - 31536000)
    total = income(0)
    conn.close()
    text = (
        f'{tge("stats", "📊")} <b>Статистика бота</b>\n\n'
        f'{tge("people", "👥")} Пользователей: <b>{users_count}</b>\n\n'
        f'{tge("money", "💵")} Доход за день: <b>{day:g} ₽</b>\n'
        f'{tge("money", "💵")} Доход за неделю: <b>{week:g} ₽</b>\n'
        f'{tge("money", "💵")} Доход за месяц: <b>{month:g} ₽</b>\n'
        f'{tge("money", "💵")} Доход за год: <b>{year:g} ₽</b>\n'
        f'{tge("wallet", "💰")} Доход за всё время: <b>{total:g} ₽</b>\n\n'
        f'{tge("box", "📦")} Товаров в наличии: <b>{stock}</b>'
    )
    await send_msg(callback.message.chat.id, text, [[btn("Назад", cb="adm:panel", style="primary", icon="down")]])


# ---------------------------------------------------------------- broadcast


@dp.callback_query(F.data == "adm:broadcast")
async def cb_adm_broadcast(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await nav(callback)
    kb = [
        [btn("Всем пользователям", cb="bc:seg:all", style="primary", icon="people")],
        [btn("Только покупателям", cb="bc:seg:buyers", style="success", icon="box")],
        [btn("Отмена", cb="adm:panel", style="danger", icon="cross")],
    ]
    await send_msg(callback.message.chat.id, f'{tge("megaphone", "📢")} <b>Кому отправить рассылку?</b>', kb)


@dp.callback_query(F.data.startswith("bc:seg:"))
async def cb_bc_segment(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await nav(callback)
    await state.update_data(bc_segment=callback.data.split(":")[2])
    await state.set_state(AdminStates.bc_message)
    await send_msg(
        callback.message.chat.id,
        f'{tge("megaphone", "📢")} Отправьте сообщение для рассылки.\n\n'
        "Поддерживаются премиум-эмодзи, любые шрифты (HTML) и фото.",
        ADMIN_CANCEL_KB,
    )


@dp.message(AdminStates.bc_message)
async def st_bc_message(message: Message, state: FSMContext):
    await state.update_data(bc_chat_id=message.chat.id, bc_message_id=message.message_id)
    await state.set_state(AdminStates.bc_button)
    await send_msg(
        message.chat.id,
        f'{tge("link", "🔘")} Добавить кнопку-ссылку?\n\n'
        "Отправьте в формате: <code>Название кнопки | https://ссылка</code>\n"
        "или нажмите «Пропустить».",
        [
            [btn("Пропустить", cb="bc:skip", style="primary", icon="down")],
            [btn("Отмена", cb="adm:panel", style="danger", icon="cross")],
        ],
    )


async def bc_preview(chat_id: int, state: FSMContext):
    data = await state.get_data()
    kb = None
    if data.get("bc_btn_text"):
        kb = {"inline_keyboard": [[{"text": data["bc_btn_text"], "url": data["bc_btn_url"]}]]}
    payload = {
        "chat_id": chat_id,
        "from_chat_id": data["bc_chat_id"],
        "message_id": data["bc_message_id"],
    }
    if kb:
        payload["reply_markup"] = kb
    await api_call("copyMessage", payload)
    await state.set_state(AdminStates.bc_confirm)
    seg_label = "только покупателям" if data.get("bc_segment") == "buyers" else "всем пользователям"
    await send_msg(
        chat_id,
        f'{tge("down", "👆")} Предпросмотр рассылки. Отправить <b>{seg_label}</b>?',
        [
            [btn("Отправить", cb="bc:send", style="success", icon="send")],
            [btn("Отмена", cb="adm:panel", style="danger", icon="cross")],
        ],
    )


@dp.callback_query(F.data == "bc:skip", AdminStates.bc_button)
async def cb_bc_skip(callback: CallbackQuery, state: FSMContext):
    await nav(callback)
    await bc_preview(callback.message.chat.id, state)


@dp.message(AdminStates.bc_button)
async def st_bc_button(message: Message, state: FSMContext):
    if "|" not in message.text:
        await send_msg(message.chat.id, f'{tge("cross", "❌")} Формат: <code>Название | https://ссылка</code>')
        return
    name, url = [x.strip() for x in message.text.split("|", 1)]
    await state.update_data(bc_btn_text=name, bc_btn_url=url)
    await bc_preview(message.chat.id, state)


@dp.callback_query(F.data == "bc:send", AdminStates.bc_confirm)
async def cb_bc_send(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await nav(callback)
    data = await state.get_data()
    await state.clear()
    kb = None
    if data.get("bc_btn_text"):
        kb = {"inline_keyboard": [[{"text": data["bc_btn_text"], "url": data["bc_btn_url"]}]]}
    conn = db()
    if data.get("bc_segment") == "buyers":
        users = conn.execute("SELECT DISTINCT user_id FROM purchases").fetchall()
    else:
        users = conn.execute("SELECT user_id FROM users").fetchall()
    conn.close()
    sent = failed = 0
    for u in users:
        payload = {
            "chat_id": u["user_id"],
            "from_chat_id": data["bc_chat_id"],
            "message_id": data["bc_message_id"],
        }
        if kb:
            payload["reply_markup"] = kb
        res = await api_call("copyMessage", payload)
        if res.get("ok"):
            sent += 1
        else:
            failed += 1
        await asyncio.sleep(0.05)
    await send_msg(
        callback.message.chat.id,
        f'{tge("check", "✅")} Рассылка завершена.\n\nОтправлено: <b>{sent}</b>\nНе доставлено: <b>{failed}</b>',
        admin_kb(),
    )


# ---------------------------------------------------------------- startup


async def setup_commands():
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Главное меню"),
            BotCommand(command="help", description="Помощь"),
        ],
        scope=BotCommandScopeDefault(),
    )
    for admin_id in ADMIN_IDS:
        try:
            await bot.set_my_commands(
                [
                    BotCommand(command="start", description="Главное меню"),
                    BotCommand(command="help", description="Помощь"),
                    BotCommand(command="admin", description="Админ-панель"),
                    BotCommand(command="backup", description="Резервная копия базы"),
                ],
                scope=BotCommandScopeChat(chat_id=admin_id),
            )
        except Exception:
            pass


async def main():
    init_db()
    await setup_commands()
    print("THEBENUA SHOP bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
