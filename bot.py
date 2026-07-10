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

STARS_RATE = 1.5  # 1 звезда = 1.5 руб
DB_PATH = "shop.db"
BANNER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "banner.png")
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
CRYPTO_API = "https://pay.crypt.bot/api"

# ---------------------------------------------------------------- premium emoji
E = {
    "stats": "5870921681735781843",
    "profile": "5870994129244131212",
    "info": "6028435952299413210",
    "crypto": "5260752406890711732",
    "balance": "5769126056262898415",
    "catalog": "5778672437122045013",
    "lang": "5345906554510012647",
    "down": "5893057118545646106",
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
            ref TEXT,
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
            content TEXT
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
        """
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


def add_user(user_id: int, username: str, ref: str = None):
    conn = db()
    conn.execute(
        "INSERT OR IGNORE INTO users(user_id, username, ref, created_at) VALUES(?,?,?,?)",
        (user_id, username, ref, int(time.time())),
    )
    conn.commit()
    conn.close()


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


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
        [btn("Каталог", cb="catalog", style="success", icon="catalog")],
        [btn("Профиль", cb="profile", style="primary", icon="profile")],
        [
            btn("О магазине", cb="about", style="success", icon="info"),
            btn("Поддержка", url=SUPPORT_URL, style="primary", icon="support"),
        ],
        [
            btn("Информация", cb="terms", style="primary", icon="info"),
            btn("Язык", cb="lang", style="success", icon="lang"),
        ],
    ]


def profile_kb():
    return [
        [btn("Пополнить баланс", cb="topup", style="success", icon="topup")],
        [btn("Промокод", cb="promo", style="primary")],
        [btn("Главное меню", cb="menu", style="primary")],
    ]


def back_menu_kb():
    return [[btn("Главное меню", cb="menu", style="primary")]]


WELCOME_TEXT = (
    f'{tge("welcome", "🛍")} <b>Добро пожаловать в THEBENUA SHOP!</b>\n\n'
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
    ref = None
    parts = message.text.split(maxsplit=1)
    if len(parts) > 1:
        ref = parts[1].strip()
    add_user(message.from_user.id, message.from_user.username or message.from_user.first_name, ref)
    await send_banner(message.chat.id, WELCOME_TEXT, main_menu_kb())


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
    await callback.answer()
    await send_banner(callback.message.chat.id, WELCOME_TEXT, main_menu_kb())


# ---------------------------------------------------------------- catalog


@dp.callback_query(F.data == "catalog")
async def cb_catalog(callback: CallbackQuery):
    conn = db()
    cats = conn.execute("SELECT * FROM categories ORDER BY id").fetchall()
    conn.close()
    kb = [[btn(c["name"], cb=f"cat:{c['id']}", style="success")] for c in cats]
    kb.append([btn("Главное меню", cb="menu", style="primary")])
    await callback.answer()
    await send_banner(
        callback.message.chat.id,
        f'{tge("catalog", "🎁")} <b>Выберите категорию:</b>',
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
    await callback.answer()
    if not prods:
        await send_msg(
            callback.message.chat.id,
            "😔 В этой категории пока нет товаров в наличии.",
            [[btn("Назад", cb="catalog", style="primary")]],
        )
        return
    kb = [
        [btn(f"{p['name']} — {p['price']:g} ₽ ({p['qty']} шт.)", cb=f"prod:{p['id']}", style="success")]
        for p in prods
    ]
    kb.append([btn("Назад", cb="catalog", style="primary")])
    await send_msg(
        callback.message.chat.id,
        f'{tge("catalog", "🎁")} <b>{cat["name"]}</b>\n\nВыберите товар:',
        kb,
    )


@dp.callback_query(F.data.startswith("prod:"))
async def cb_product(callback: CallbackQuery):
    prod_id = int(callback.data.split(":")[1])
    conn = db()
    p = conn.execute("SELECT * FROM products WHERE id=?", (prod_id,)).fetchone()
    conn.close()
    await callback.answer()
    if not p or p["qty"] <= 0:
        await send_msg(callback.message.chat.id, "😔 Товар закончился.", back_menu_kb())
        return
    stars = math.ceil(p["price"] / STARS_RATE)
    text = (
        f'{tge("catalog", "🎁")} <b>{p["name"]}</b>\n\n'
        f'💵 Цена: <b>{p["price"]:g} ₽</b> / <b>{stars} ⭐</b>\n'
        f'📦 В наличии: <b>{p["qty"]} шт.</b>'
    )
    kb = [
        [btn("Купить", cb=f"buy:{p['id']}", style="success")],
        [btn("Назад", cb=f"cat:{p['category_id']}", style="primary")],
    ]
    await send_msg(callback.message.chat.id, text, kb)


@dp.callback_query(F.data.startswith("buy:"))
async def cb_buy(callback: CallbackQuery):
    prod_id = int(callback.data.split(":")[1])
    conn = db()
    p = conn.execute("SELECT * FROM products WHERE id=?", (prod_id,)).fetchone()
    conn.close()
    await callback.answer()
    if not p or p["qty"] <= 0:
        await send_msg(callback.message.chat.id, "😔 Товар закончился.", back_menu_kb())
        return
    stars = math.ceil(p["price"] / STARS_RATE)
    text = (
        f'💳 <b>Оплата: {p["name"]}</b>\n\n'
        f'Сумма: <b>{p["price"]:g} ₽</b> / <b>{stars} ⭐</b>\n\n'
        "Выберите способ оплаты:"
    )
    kb = [
        [btn("CryptoBot", cb=f"paycb:{p['id']}", style="primary", icon="crypto")],
        [btn(f"Звёзды ({stars} ⭐)", cb=f"paystars:{p['id']}", style="success")],
        [btn("Баланс", cb=f"paybal:{p['id']}", style="success", icon="balance")],
        [btn("Отмена", cb=f"prod:{p['id']}", style="danger")],
    ]
    await send_msg(callback.message.chat.id, text, kb)


# ---------------------------------------------------------------- delivery


async def deliver(user_id: int, chat_id: int, product_id: int, method: str):
    conn = db()
    p = conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
    if not p or p["qty"] <= 0:
        conn.close()
        await send_msg(chat_id, "😔 Товар закончился. Обратитесь в поддержку.", back_menu_kb())
        return False
    conn.execute("UPDATE products SET qty=qty-1 WHERE id=?", (product_id,))
    conn.execute("UPDATE users SET spent=spent+? WHERE user_id=?", (p["price"], user_id))
    conn.execute(
        "INSERT INTO purchases(user_id, product_id, product_name, amount, method, created_at) VALUES(?,?,?,?,?,?)",
        (user_id, product_id, p["name"], p["price"], method, int(time.time())),
    )
    if method != "balance":
        conn.execute(
            "INSERT INTO payments(user_id, amount, method, kind, created_at) VALUES(?,?,?,?,?)",
            (user_id, p["price"], method, "purchase", int(time.time())),
        )
    conn.commit()
    conn.close()
    text = (
        f'✅ <b>Оплата прошла успешно!</b>\n\n'
        f'{tge("catalog", "🎁")} Ваш товар: <b>{p["name"]}</b>\n\n'
        f'{p["content"]}'
    )
    await send_msg(chat_id, text, back_menu_kb())
    return True


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
    if not user or user["balance"] < p["price"]:
        await callback.answer("Недостаточно средств на балансе", show_alert=True)
        return
    conn = db()
    conn.execute(
        "UPDATE users SET balance=balance-? WHERE user_id=?", (p["price"], callback.from_user.id)
    )
    conn.commit()
    conn.close()
    await callback.answer()
    await deliver(callback.from_user.id, callback.message.chat.id, prod_id, "balance")


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
    stars = math.ceil(p["price"] / STARS_RATE)
    await callback.answer()
    await bot.send_invoice(
        chat_id=callback.message.chat.id,
        title=p["name"][:32],
        description=f"Покупка: {p['name']}",
        payload=f"buy:{prod_id}",
        currency="XTR",
        prices=[LabeledPrice(label=p["name"][:32], amount=stars)],
    )


@dp.callback_query(F.data.startswith("topstars:"))
async def cb_topup_stars(callback: CallbackQuery):
    amount = float(callback.data.split(":")[1])
    stars = math.ceil(amount / STARS_RATE)
    await callback.answer()
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
        prod_id = int(payload.split(":")[1])
        await deliver(user_id, message.chat.id, prod_id, "stars")
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
            f'✅ Баланс пополнен на <b>{amount:g} ₽</b>!',
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


async def watch_crypto_invoice(invoice_id: int, user_id: int, chat_id: int, kind: str, value: float):
    """Ждём оплату счёта CryptoBot и сразу выдаём товар / пополняем баланс."""
    for _ in range(360):  # до 30 минут, каждые 5 секунд
        await asyncio.sleep(5)
        inv = await crypto_check_invoice(invoice_id)
        if inv is None:
            continue
        if inv["status"] == "paid":
            if kind == "buy":
                await deliver(user_id, chat_id, int(value), "cryptobot")
            else:
                conn = db()
                conn.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (value, user_id))
                conn.execute(
                    "INSERT INTO payments(user_id, amount, method, kind, created_at) VALUES(?,?,?,?,?)",
                    (user_id, value, "cryptobot", "topup", int(time.time())),
                )
                conn.commit()
                conn.close()
                await send_msg(chat_id, f'✅ Баланс пополнен на <b>{value:g} ₽</b>!', back_menu_kb())
            return
        if inv["status"] == "expired":
            await send_msg(chat_id, "⏰ Счёт истёк. Попробуйте ещё раз.", back_menu_kb())
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
    await callback.answer()
    inv = await crypto_create_invoice(p["price"], f"Покупка: {p['name']}", f"buy:{prod_id}")
    if not inv:
        await send_msg(callback.message.chat.id, "❌ Не удалось создать счёт. Попробуйте позже.", back_menu_kb())
        return
    text = (
        f'{tge("crypto", "🪙")} <b>Счёт создан!</b>\n\n'
        f'Сумма: <b>{p["price"]:g} ₽</b>\n'
        "Оплатите по кнопке ниже — после оплаты бот сразу пришлёт вам товар."
    )
    kb = [
        [btn("Оплатить", url=inv["bot_invoice_url"], style="success", icon="crypto")],
        [btn("Отмена", cb=f"prod:{prod_id}", style="danger")],
    ]
    await send_msg(callback.message.chat.id, text, kb)
    asyncio.create_task(
        watch_crypto_invoice(inv["invoice_id"], callback.from_user.id, callback.message.chat.id, "buy", prod_id)
    )


@dp.callback_query(F.data.startswith("topcb:"))
async def cb_topup_crypto(callback: CallbackQuery):
    amount = float(callback.data.split(":")[1])
    await callback.answer()
    inv = await crypto_create_invoice(amount, "Пополнение баланса", f"topup:{amount:g}")
    if not inv:
        await send_msg(callback.message.chat.id, "❌ Не удалось создать счёт. Попробуйте позже.", back_menu_kb())
        return
    text = (
        f'{tge("crypto", "🪙")} <b>Счёт создан!</b>\n\n'
        f'Сумма: <b>{amount:g} ₽</b>\n'
        "Оплатите по кнопке ниже — баланс будет зачислен автоматически."
    )
    kb = [
        [btn("Оплатить", url=inv["bot_invoice_url"], style="success", icon="crypto")],
        [btn("Отмена", cb="profile", style="danger")],
    ]
    await send_msg(callback.message.chat.id, text, kb)
    asyncio.create_task(
        watch_crypto_invoice(inv["invoice_id"], callback.from_user.id, callback.message.chat.id, "topup", amount)
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
        f'👤 Имя: <b>{callback.from_user.first_name}</b>\n'
        f'🆔 ID: <code>{user["user_id"]}</code>\n'
        f'{tge("balance", "💰")} Баланс: <b>{user["balance"]:g} ₽</b>\n'
        f'🎁 Потрачено: <b>{user["spent"]:g} ₽</b>'
    )
    await callback.answer()
    await send_banner(callback.message.chat.id, text, profile_kb())


@dp.callback_query(F.data == "topup")
async def cb_topup(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(UserStates.topup_amount)
    await send_msg(
        callback.message.chat.id,
        f'{tge("topup", "💸")} Введите сумму пополнения в рублях (например: <code>100</code>):',
        [[btn("Отмена", cb="profile", style="danger")]],
    )


@dp.message(UserStates.topup_amount)
async def st_topup_amount(message: Message, state: FSMContext):
    try:
        amount = float(message.text.replace(",", "."))
        assert 10 <= amount <= 100000
    except (ValueError, AssertionError):
        await send_msg(message.chat.id, "❌ Введите сумму от 10 до 100000 ₽.")
        return
    await state.clear()
    stars = math.ceil(amount / STARS_RATE)
    kb = [
        [btn("CryptoBot", cb=f"topcb:{amount:g}", style="primary", icon="crypto")],
        [btn(f"Звёзды ({stars} ⭐)", cb=f"topstars:{amount:g}", style="success")],
        [btn("Отмена", cb="profile", style="danger")],
    ]
    await send_msg(
        message.chat.id,
        f'💳 Пополнение на <b>{amount:g} ₽</b>\n\nВыберите способ оплаты:',
        kb,
    )


@dp.callback_query(F.data == "promo")
async def cb_promo(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(UserStates.promo_enter)
    await send_msg(
        callback.message.chat.id,
        "🎟 Введите промокод:",
        [[btn("Отмена", cb="profile", style="danger")]],
    )


@dp.message(UserStates.promo_enter)
async def st_promo_enter(message: Message, state: FSMContext):
    code = message.text.strip()
    conn = db()
    promo = conn.execute("SELECT * FROM promocodes WHERE code=?", (code,)).fetchone()
    if not promo or promo["uses_left"] <= 0:
        conn.close()
        await send_msg(message.chat.id, "❌ Промокод не найден или закончился.", back_menu_kb())
        await state.clear()
        return
    used = conn.execute(
        "SELECT 1 FROM promo_activations WHERE code=? AND user_id=?", (code, message.from_user.id)
    ).fetchone()
    if used:
        conn.close()
        await send_msg(message.chat.id, "❌ Вы уже активировали этот промокод.", back_menu_kb())
        await state.clear()
        return
    conn.execute("UPDATE promocodes SET uses_left=uses_left-1 WHERE code=?", (code,))
    conn.execute("INSERT INTO promo_activations(code, user_id) VALUES(?,?)", (code, message.from_user.id))
    conn.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (promo["amount"], message.from_user.id))
    conn.commit()
    conn.close()
    await state.clear()
    await send_msg(
        message.chat.id,
        f'✅ Промокод активирован! Баланс пополнен на <b>{promo["amount"]:g} ₽</b>.',
        back_menu_kb(),
    )


# ---------------------------------------------------------------- info / about / lang


@dp.callback_query(F.data == "about")
async def cb_about(callback: CallbackQuery):
    text = (
        f'{tge("info", "ℹ️")} <b>О магазине</b>\n\n'
        "<b>THEBENUA SHOP</b> — магазин цифровых товаров.\n"
        "Моментальная выдача после оплаты 24/7.\n\n"
        f'{tge("crypto", "🪙")} Оплата: CryptoBot и Telegram Stars\n'
        f'{tge("support", "🆘")} Поддержка: {SUPPORT_URL}'
    )
    await callback.answer()
    await send_msg(callback.message.chat.id, text, back_menu_kb())


@dp.callback_query(F.data == "terms")
async def cb_terms(callback: CallbackQuery):
    text = (
        f'{tge("info", "ℹ️")} <b>Информация</b>\n\n'
        "Пользовательское соглашение:\n"
        "https://telegra.ph/Polzovatelskoe-soglashenie-04-01-19"
    )
    await callback.answer()
    await send_msg(callback.message.chat.id, text, back_menu_kb())


@dp.callback_query(F.data == "lang")
async def cb_lang(callback: CallbackQuery):
    await callback.answer()
    kb = [
        [
            btn("🇷🇺 Русский", cb="setlang:ru", style="success"),
            btn("🇬🇧 English", cb="setlang:en", style="primary"),
        ],
        [btn("Главное меню", cb="menu", style="primary")],
    ]
    await send_msg(callback.message.chat.id, f'{tge("lang", "🌐")} <b>Выберите язык:</b>', kb)


@dp.callback_query(F.data.startswith("setlang:"))
async def cb_setlang(callback: CallbackQuery):
    lang = callback.data.split(":")[1]
    conn = db()
    conn.execute("UPDATE users SET lang=? WHERE user_id=?", (lang, callback.from_user.id))
    conn.commit()
    conn.close()
    await callback.answer("✅ Язык сохранён" if lang == "ru" else "✅ Language saved")
    await send_banner(callback.message.chat.id, WELCOME_TEXT, main_menu_kb())


# ---------------------------------------------------------------- admin


def admin_kb():
    return [
        [btn("Создать категорию", cb="adm:newcat", style="success")],
        [btn("Создать товар", cb="adm:newprod", style="success")],
        [btn("Создать промокод", cb="adm:newpromo", style="primary")],
        [btn("Статистика", cb="adm:stats", style="primary", icon="stats")],
        [btn("Рассылка", cb="adm:broadcast", style="danger")],
    ]


@dp.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    await send_msg(message.chat.id, "🛠 <b>Админ-панель</b>\n\nВыберите действие:", admin_kb())


@dp.callback_query(F.data == "adm:panel")
async def cb_admin_panel(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.clear()
    await callback.answer()
    await send_msg(callback.message.chat.id, "🛠 <b>Админ-панель</b>\n\nВыберите действие:", admin_kb())


ADMIN_CANCEL_KB = [[btn("Отмена", cb="adm:panel", style="danger")]]


@dp.callback_query(F.data == "adm:newcat")
async def cb_adm_newcat(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await callback.answer()
    await state.set_state(AdminStates.cat_name)
    await send_msg(callback.message.chat.id, "📁 Введите название категории:", ADMIN_CANCEL_KB)


@dp.message(AdminStates.cat_name)
async def st_cat_name(message: Message, state: FSMContext):
    conn = db()
    conn.execute("INSERT OR IGNORE INTO categories(name) VALUES(?)", (message.text.strip(),))
    conn.commit()
    conn.close()
    await state.clear()
    await send_msg(message.chat.id, f"✅ Категория <b>{message.text.strip()}</b> создана.", admin_kb())


@dp.callback_query(F.data == "adm:newprod")
async def cb_adm_newprod(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await callback.answer()
    conn = db()
    cats = conn.execute("SELECT * FROM categories ORDER BY id").fetchall()
    conn.close()
    if not cats:
        await send_msg(callback.message.chat.id, "❌ Сначала создайте категорию.", admin_kb())
        return
    kb = [[btn(c["name"], cb=f"adm:prodcat:{c['id']}", style="success")] for c in cats]
    kb.append([btn("Отмена", cb="adm:panel", style="danger")])
    await state.set_state(AdminStates.prod_category)
    await send_msg(callback.message.chat.id, "📁 Выберите категорию товара:", kb)


@dp.callback_query(F.data.startswith("adm:prodcat:"))
async def cb_adm_prodcat(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await callback.answer()
    await state.update_data(category_id=int(callback.data.split(":")[2]))
    await state.set_state(AdminStates.prod_name)
    await send_msg(callback.message.chat.id, "📝 Введите название товара:", ADMIN_CANCEL_KB)


@dp.message(AdminStates.prod_name)
async def st_prod_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await state.set_state(AdminStates.prod_price)
    await send_msg(message.chat.id, "💵 Введите цену в рублях (например: <code>100</code>):", ADMIN_CANCEL_KB)


@dp.message(AdminStates.prod_price)
async def st_prod_price(message: Message, state: FSMContext):
    try:
        price = float(message.text.replace(",", "."))
        assert price > 0
    except (ValueError, AssertionError):
        await send_msg(message.chat.id, "❌ Введите корректную цену.")
        return
    await state.update_data(price=price)
    await state.set_state(AdminStates.prod_qty)
    await send_msg(message.chat.id, "📦 Введите количество товара:", ADMIN_CANCEL_KB)


@dp.message(AdminStates.prod_qty)
async def st_prod_qty(message: Message, state: FSMContext):
    try:
        qty = int(message.text)
        assert qty >= 0
    except (ValueError, AssertionError):
        await send_msg(message.chat.id, "❌ Введите корректное количество.")
        return
    await state.update_data(qty=qty)
    await state.set_state(AdminStates.prod_content)
    await send_msg(
        message.chat.id,
        "🔗 Введите содержимое товара (ссылка/данные, которые получит покупатель):",
        ADMIN_CANCEL_KB,
    )


@dp.message(AdminStates.prod_content)
async def st_prod_content(message: Message, state: FSMContext):
    data = await state.get_data()
    conn = db()
    conn.execute(
        "INSERT INTO products(category_id, name, price, qty, content) VALUES(?,?,?,?,?)",
        (data["category_id"], data["name"], data["price"], data["qty"], message.text.strip()),
    )
    conn.commit()
    conn.close()
    await state.clear()
    await send_msg(
        message.chat.id,
        f'✅ Товар <b>{data["name"]}</b> создан — {data["price"]:g} ₽, {data["qty"]} шт.',
        admin_kb(),
    )


@dp.callback_query(F.data == "adm:newpromo")
async def cb_adm_newpromo(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await callback.answer()
    await state.set_state(AdminStates.promo_code)
    await send_msg(callback.message.chat.id, "🎟 Введите код промокода:", ADMIN_CANCEL_KB)


@dp.message(AdminStates.promo_code)
async def st_promo_code(message: Message, state: FSMContext):
    await state.update_data(code=message.text.strip())
    await state.set_state(AdminStates.promo_amount)
    await send_msg(message.chat.id, "💵 Введите сумму промокода в рублях:", ADMIN_CANCEL_KB)


@dp.message(AdminStates.promo_amount)
async def st_promo_amount(message: Message, state: FSMContext):
    try:
        amount = float(message.text.replace(",", "."))
        assert amount > 0
    except (ValueError, AssertionError):
        await send_msg(message.chat.id, "❌ Введите корректную сумму.")
        return
    await state.update_data(amount=amount)
    await state.set_state(AdminStates.promo_uses)
    await send_msg(message.chat.id, "🔢 Введите количество активаций:", ADMIN_CANCEL_KB)


@dp.message(AdminStates.promo_uses)
async def st_promo_uses(message: Message, state: FSMContext):
    try:
        uses = int(message.text)
        assert uses > 0
    except (ValueError, AssertionError):
        await send_msg(message.chat.id, "❌ Введите корректное число.")
        return
    data = await state.get_data()
    conn = db()
    conn.execute(
        "INSERT OR REPLACE INTO promocodes(code, amount, uses_left) VALUES(?,?,?)",
        (data["code"], data["amount"], uses),
    )
    conn.commit()
    conn.close()
    await state.clear()
    await send_msg(
        message.chat.id,
        f'✅ Промокод <code>{data["code"]}</code> на {data["amount"]:g} ₽ ({uses} активаций) создан.',
        admin_kb(),
    )


# ---------------------------------------------------------------- stats


@dp.callback_query(F.data == "adm:stats")
async def cb_adm_stats(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    await callback.answer()
    now = int(time.time())
    conn = db()

    def income(since):
        row = conn.execute(
            "SELECT COALESCE(SUM(amount),0) s FROM payments WHERE created_at>=?", (since,)
        ).fetchone()
        return row["s"]

    users_count = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
    stock = conn.execute("SELECT COALESCE(SUM(qty),0) s FROM products").fetchone()["s"]
    refs = conn.execute(
        "SELECT ref, COUNT(*) c FROM users WHERE ref IS NOT NULL GROUP BY ref ORDER BY c DESC LIMIT 20"
    ).fetchall()
    ref_users = conn.execute("SELECT COUNT(*) c FROM users WHERE ref IS NOT NULL").fetchone()["c"]
    day, week, month, year = income(now - 86400), income(now - 604800), income(now - 2592000), income(now - 31536000)
    total = income(0)
    conn.close()
    ref_lines = "\n".join(f'  • <code>{r["ref"]}</code> — {r["c"]}' for r in refs) or "  —"
    text = (
        f'{tge("stats", "📊")} <b>Статистика бота</b>\n\n'
        f'👥 Пользователей: <b>{users_count}</b>\n'
        f'🔗 Пришли по ссылке: <b>{ref_users}</b>\n{ref_lines}\n\n'
        f'💵 Доход за день: <b>{day:g} ₽</b>\n'
        f'💵 Доход за неделю: <b>{week:g} ₽</b>\n'
        f'💵 Доход за месяц: <b>{month:g} ₽</b>\n'
        f'💵 Доход за год: <b>{year:g} ₽</b>\n'
        f'💰 Доход за всё время: <b>{total:g} ₽</b>\n\n'
        f'📦 Товаров в наличии: <b>{stock}</b>'
    )
    await send_msg(callback.message.chat.id, text, [[btn("Назад", cb="adm:panel", style="primary")]])


# ---------------------------------------------------------------- broadcast


@dp.callback_query(F.data == "adm:broadcast")
async def cb_adm_broadcast(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await callback.answer()
    await state.set_state(AdminStates.bc_message)
    await send_msg(
        callback.message.chat.id,
        "📢 Отправьте сообщение для рассылки.\n\n"
        "Поддерживаются премиум-эмодзи, любые шрифты (HTML) и фото.",
        ADMIN_CANCEL_KB,
    )


@dp.message(AdminStates.bc_message)
async def st_bc_message(message: Message, state: FSMContext):
    await state.update_data(bc_chat_id=message.chat.id, bc_message_id=message.message_id)
    await state.set_state(AdminStates.bc_button)
    await send_msg(
        message.chat.id,
        "🔘 Добавить кнопку-ссылку?\n\n"
        "Отправьте в формате: <code>Название кнопки | https://ссылка</code>\n"
        "или нажмите «Пропустить».",
        [
            [btn("Пропустить", cb="bc:skip", style="primary")],
            [btn("Отмена", cb="adm:panel", style="danger")],
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
    await send_msg(
        chat_id,
        "👆 Предпросмотр рассылки. Отправить всем пользователям?",
        [
            [btn("Отправить", cb="bc:send", style="success")],
            [btn("Отмена", cb="adm:panel", style="danger")],
        ],
    )


@dp.callback_query(F.data == "bc:skip", AdminStates.bc_button)
async def cb_bc_skip(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await bc_preview(callback.message.chat.id, state)


@dp.message(AdminStates.bc_button)
async def st_bc_button(message: Message, state: FSMContext):
    if "|" not in message.text:
        await send_msg(message.chat.id, "❌ Формат: <code>Название | https://ссылка</code>")
        return
    name, url = [x.strip() for x in message.text.split("|", 1)]
    await state.update_data(bc_btn_text=name, bc_btn_url=url)
    await bc_preview(message.chat.id, state)


@dp.callback_query(F.data == "bc:send", AdminStates.bc_confirm)
async def cb_bc_send(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await callback.answer()
    data = await state.get_data()
    await state.clear()
    kb = None
    if data.get("bc_btn_text"):
        kb = {"inline_keyboard": [[{"text": data["bc_btn_text"], "url": data["bc_btn_url"]}]]}
    conn = db()
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
        f"✅ Рассылка завершена.\n\nОтправлено: <b>{sent}</b>\nНе доставлено: <b>{failed}</b>",
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
