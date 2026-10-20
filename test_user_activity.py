import os
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from unittest.mock import AsyncMock

os.environ["BOT_TOKEN"] = "123456:TEST_TOKEN_FOR_LOCAL_TESTS_ONLY"

import bot as shop
import user_activity as activity
from aiogram import Dispatcher
from aiogram.types import CallbackQuery, Chat, Message, Update, User


class ActivityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = shop.DB_PATH
        shop.DB_PATH = os.path.join(self.tmp.name, "test.db")
        shop.init_db()
        self.user = User(id=42, first_name="<Артём>", username="test", is_bot=False)

    def tearDown(self):
        shop.DB_PATH = self.old_path
        self.tmp.cleanup()

    def test_migration_preserves_existing_data_and_is_repeatable(self):
        shop.add_user(42, "old")
        with closing(shop.db()) as conn:
            conn.execute("UPDATE users SET balance=150, spent=50 WHERE user_id=42")
            for column in ("full_name", "last_seen", "telegram_username"):
                if column == "last_seen":
                    conn.execute("DROP INDEX idx_users_last_seen")
                conn.execute(f"ALTER TABLE users DROP COLUMN {column}")
            conn.commit()
        shop.init_db()
        shop.init_db()
        self.assertEqual(shop.get_user(42)["balance"], 150)
        self.assertEqual(shop.get_user(42)["spent"], 50)

    def test_history_pagination_and_legacy_purchases(self):
        with closing(shop.db()) as conn:
            for n in range(12):
                activity.record(conn, self.user, f"Событие {n}")
            conn.execute(
                "INSERT INTO purchases(user_id,product_name,amount,method,created_at) VALUES(42,'Gemini',100,'stars',1)"
            )
            conn.commit()
            first = activity.history(conn, 42, 0)
            second = activity.history(conn, 42, 10)
        self.assertEqual(len(first), 11)
        self.assertEqual(first[0]["description"], "Событие 11")
        self.assertEqual(len(second), 3)
        self.assertIn("Покупка: Gemini", second[-1]["description"])

    def test_does_not_store_message_contents(self):
        event = Message(message_id=1, date=datetime.now(timezone.utc),
                        chat=Chat(id=42, type="private"), from_user=self.user,
                        text="secret password or promo")
        with closing(shop.db()) as conn:
            self.assertIsNone(activity.describe(event, conn))
            event = event.model_copy(update={"text": "/start referral"})
            self.assertEqual(activity.describe(event, conn), "Запустил бота /start")
            activity.record(conn, self.user, None)
        self.assertEqual(shop.get_user(42)["full_name"], "<Артём>")

    async def test_admin_access_escaping_and_directory(self):
        dp = Dispatcher()
        send = AsyncMock()
        nav = AsyncMock()
        activity.register(dp, shop.db, lambda uid: uid == 1, send, shop.btn, nav)
        with closing(shop.db()) as conn:
            activity.record(conn, self.user, "Нажал на товар: <Gemini>")
        async def request(uid, data):
            callback = CallbackQuery(
                id="test", from_user=self.user if uid == 42 else User(id=uid, first_name="Tester", is_bot=False),
                chat_instance="test", data=data,
                message=Message(message_id=1, date=datetime.now(timezone.utc),
                                chat=Chat(id=uid, type="private")),
            )
            # Denials answer through Bot; replace its session to avoid network.
            original = shop.bot.session
            shop.bot.session = AsyncMock()
            try:
                await dp.feed_update(shop.bot, Update(update_id=1, callback_query=callback))
            finally:
                shop.bot.session = original
        await request(42, "adm:user:42:0:0")
        send.assert_not_awaited()
        await request(1, "adm:user:42:0:0")
        self.assertIn("&lt;Артём&gt;", send.call_args.args[1])
        self.assertIn("&lt;Gemini&gt;", send.call_args.args[1])
        await request(1, "adm:users:0")
        self.assertIn("Пользователи:", send.call_args.args[1])
        await request(42, "adm:users:0")
        self.assertEqual(send.await_count, 2)
        await dp.storage.close()

    async def test_start_remains_new_once_and_logs_each_visit(self):
        dp = Dispatcher()
        seen = []

        @dp.message()
        async def start(message):
            seen.append(shop.add_user(message.from_user.id, message.from_user.username))

        activity.register(dp, shop.db, lambda uid: False, AsyncMock(), shop.btn, AsyncMock())
        message = Message(message_id=1, date=datetime.now(timezone.utc),
                          chat=Chat(id=42, type="private"), from_user=self.user, text="/start")
        for n in range(2):
            await dp.feed_update(shop.bot, Update(update_id=n, message=message))
        self.assertEqual(seen, [True, False])
        with closing(shop.db()) as conn:
            self.assertEqual(len(activity.history(conn, 42, 0)), 2)
        await dp.storage.close()


if __name__ == "__main__":
    unittest.main()
