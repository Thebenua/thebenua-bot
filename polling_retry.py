"""Respect Telegram's minimum cooldown before aiogram retries polling."""

import asyncio
import logging

from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import GetUpdates


class PollingRetryAfter(BaseRequestMiddleware):
    async def __call__(self, make_request, bot, method):
        try:
            return await make_request(bot, method)
        except TelegramRetryAfter as error:
            if isinstance(method, GetUpdates):
                delay = max(0, error.retry_after)
                logging.getLogger(__name__).warning(
                    "Telegram limited getUpdates; waiting %s seconds before polling backoff",
                    delay,
                )
                await asyncio.sleep(delay)
            # Keep aiogram's existing backoff, offset and cancellation behavior.
            raise
