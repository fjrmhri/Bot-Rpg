import asyncio
import logging
from typing import Any, Dict, Optional

from telegram import InlineKeyboardMarkup
from telegram.error import RetryAfter, TelegramError, TimedOut

logger = logging.getLogger("legends_of_aruna.telegram")


async def safe_send_message(context, chat_id: int, **kwargs) -> Optional[Any]:
    for attempt in range(3):
        try:
            return await context.bot.send_message(chat_id=chat_id, **kwargs)
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after)
        except TimedOut:
            if attempt == 2:
                raise
            await asyncio.sleep(2 ** attempt)
        except TelegramError as exc:
            logger.warning("send_message failed: %s", exc)
            return None
    return None


async def safe_edit_message(
    context,
    chat_id: int,
    message_id: int,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    **kwargs,
) -> Optional[Any]:
    for attempt in range(3):
        try:
            return await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=reply_markup,
                **kwargs,
            )
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after)
        except TimedOut:
            if attempt == 2:
                raise
            await asyncio.sleep(2 ** attempt)
        except TelegramError as exc:
            logger.warning("edit_message failed: %s", exc)
            return None
    return None


async def safe_reply(update, context, text: str, **kwargs):
    if update.effective_chat is None:
        return None
    return await safe_send_message(context, update.effective_chat.id, text=text, **kwargs)
