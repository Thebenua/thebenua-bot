import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from aiogram.exceptions import TelegramRetryAfter, TelegramServerError
from aiogram.methods import GetUpdates, SendMessage

from polling_retry import PollingRetryAfter


class PollingRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_waits_full_cooldown_and_preserves_offset(self):
        method = GetUpdates(offset=123, timeout=30)
        error = TelegramRetryAfter(method=method, message="Too Many Requests", retry_after=5)
        request = AsyncMock(side_effect=error)
        with patch("polling_retry.asyncio.sleep", new_callable=AsyncMock) as sleep:
            with self.assertRaises(TelegramRetryAfter) as raised:
                await PollingRetryAfter()(request, None, method)
        self.assertIs(raised.exception, error)
        sleep.assert_awaited_once_with(5)
        request.assert_awaited_once_with(None, method)
        self.assertEqual(method.offset, 123)

    async def test_success_returns_updates(self):
        updates = [object()]
        result = await PollingRetryAfter()(AsyncMock(return_value=updates), None, GetUpdates())
        self.assertIs(result, updates)

    async def test_other_errors_and_methods_keep_existing_handling(self):
        for method, exception in (
            (GetUpdates(), TelegramServerError),
            (SendMessage(chat_id=1, text="test"), TelegramRetryAfter),
        ):
            kwargs = {"retry_after": 5} if exception is TelegramRetryAfter else {}
            error = exception(method=method, message="test", **kwargs)
            with patch("polling_retry.asyncio.sleep", new_callable=AsyncMock) as sleep:
                with self.assertRaises(exception):
                    await PollingRetryAfter()(AsyncMock(side_effect=error), None, method)
                sleep.assert_not_awaited()

    async def test_shutdown_can_cancel_cooldown(self):
        method = GetUpdates()
        error = TelegramRetryAfter(method=method, message="test", retry_after=5)
        with patch("polling_retry.asyncio.sleep", new_callable=AsyncMock,
                   side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError):
                await PollingRetryAfter()(AsyncMock(side_effect=error), None, method)
