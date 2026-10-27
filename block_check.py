#!/usr/bin/env python3
"""
Middleware блокировки пользователей.
Если администратор заблокировал пользователя через админку — он не сможет
пользоваться ботом (ни сообщениями, ни кнопками), пока его не разблокируют.
Администраторов блокировка не касается.
"""
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery

from database import Session, User
import config

# Простой in-memory кэш, чтобы не дёргать БД на каждое сообщение.
# Инвалидируется функциями block_user/unblock_user из admin.py.
_blocked_cache: dict[int, bool] = {}


def invalidate_block_cache(user_id: int):
    _blocked_cache.pop(user_id, None)


def _is_blocked_db(user_id: int) -> bool:
    session = Session()
    try:
        u = session.query(User).filter_by(user_id=user_id).first()
        return bool(u and u.is_blocked)
    finally:
        session.close()


def _get_block_reason(user_id: int) -> str | None:
    session = Session()
    try:
        u = session.query(User).filter_by(user_id=user_id).first()
        return u.blocked_reason if u else None
    finally:
        session.close()


def is_blocked(user_id: int) -> bool:
    if user_id in config.config.ADMINS:
        return False
    if user_id in _blocked_cache:
        return _blocked_cache[user_id]
    blocked = _is_blocked_db(user_id)
    _blocked_cache[user_id] = blocked
    return blocked


class BlockCheckMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: TelegramObject, data: dict):
        user = None
        if isinstance(event, Message):
            user = event.from_user
        elif isinstance(event, CallbackQuery):
            user = event.from_user

        if user and is_blocked(user.id):
            reason = _get_block_reason(user.id)
            reason_line = f"\n\n📝 Причина: {reason}" if reason else ""
            if isinstance(event, Message):
                try:
                    await event.answer(
                        "⛔ Вы заблокированы в этом боте."
                        f"{reason_line}\n\n"
                        "Если считаете это ошибкой — свяжитесь с менеджером."
                    )
                except Exception:
                    pass
            elif isinstance(event, CallbackQuery):
                try:
                    await event.answer(
                        f"⛔ Вы заблокированы.{reason_line}",
                        show_alert=True,
                    )
                except Exception:
                    pass
            return  # обработчик не вызывается

        return await handler(event, data)
