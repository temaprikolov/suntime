#!/usr/bin/env python3
"""
Обязательная подписка на канал.

Пользоваться ботом можно только подписавшись на канал (config.CHANNEL_ID /
CHANNEL_USERNAME). Бот должен быть администратором канала — иначе Telegram не
отдаёт статус участника (в этом случае, чтобы не заблокировать всех, пропускаем
и пишем предупреждение в лог).

Администраторы бота проверке не подлежат.
"""
import time

from aiogram import BaseMiddleware, Bot
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, TelegramObject,
)

import config
from database import Session, User

_MEMBER_TTL = 120          # сек: подтверждённую подписку не перепроверяем чаще
_GATE_COOLDOWN = 3         # сек: не спамим экраном подписки при быстрых тапах
_member_cache: dict[int, float] = {}
_gate_shown_at: dict[int, float] = {}
_warned_errors = 0

CHECK_CB = "check_subscription"


def channel_url() -> str:
    return f"https://t.me/{config.config.CHANNEL_USERNAME}"


def gate_text() -> str:
    return (
        "📢 <b>Для использования бота нужно подписаться на наш канал</b>\n\n"
        f"@{config.config.CHANNEL_USERNAME} — там акции, новинки и информация о завозе.\n\n"
        "1️⃣ Нажмите «Подписаться на канал»\n"
        "2️⃣ Вернитесь сюда и нажмите «✅ Я подписался»"
    )


def gate_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Подписаться на канал", url=channel_url())],
        [InlineKeyboardButton(text="✅ Я подписался", callback_data=CHECK_CB)],
    ])


def _is_bot_admin(user_id: int) -> bool:
    if user_id in config.config.ADMINS:
        return True
    session = Session()
    try:
        u = session.query(User).filter_by(user_id=user_id).first()
        return bool(u and u.is_admin)
    except Exception:
        return False
    finally:
        session.close()


async def is_subscribed(bot: Bot, user_id: int, use_cache: bool = True) -> bool:
    """True, если пользователь состоит в канале. Админы бота — всегда True."""
    global _warned_errors
    if _is_bot_admin(user_id):
        return True

    now = time.monotonic()
    if use_cache and now - _member_cache.get(user_id, -1e9) < _MEMBER_TTL:
        return True

    try:
        member = await bot.get_chat_member(chat_id=config.config.CHANNEL_ID, user_id=user_id)
    except Exception as e:
        # Бот не админ канала / Telegram недоступен — не запираем всех пользователей.
        _warned_errors += 1
        if _warned_errors <= 5 or _warned_errors % 100 == 0:
            print(f"⚠️ Не удалось проверить подписку {user_id} на канал "
                  f"{config.config.CHANNEL_ID}: {e}. Бот должен быть админом канала!")
        return True

    status = getattr(member, "status", None)
    ok = status in ("creator", "administrator", "member") or (
        status == "restricted" and bool(getattr(member, "is_member", True))
    )
    if ok:
        _member_cache[user_id] = now
    else:
        _member_cache.pop(user_id, None)
    return ok


async def show_gate(target: Message) -> None:
    """Показать экран «подпишитесь» (с антиспамом)."""
    uid = target.chat.id
    now = time.monotonic()
    if now - _gate_shown_at.get(uid, -1e9) < _GATE_COOLDOWN:
        return
    _gate_shown_at[uid] = now
    await target.answer(gate_text(), reply_markup=gate_kb(), parse_mode="HTML")


class SubscriptionMiddleware(BaseMiddleware):
    """Не пускает к обработчикам тех, кто не подписан на канал."""

    async def __call__(self, handler, event: TelegramObject, data: dict):
        user = None
        msg = None
        if isinstance(event, Message):
            user, msg = event.from_user, event
            if event.chat.type != "private":
                return await handler(event, data)
            # /start сам показывает экран подписки (после регистрации и реф-ссылки)
            _words = (event.text or "").split()
            if _words and _words[0].split("@")[0] == "/start":
                return await handler(event, data)
        elif isinstance(event, CallbackQuery):
            user, msg = event.from_user, event.message
            if event.data == CHECK_CB:
                return await handler(event, data)

        if user is None or user.is_bot:
            return await handler(event, data)

        bot: Bot = data["bot"]
        if await is_subscribed(bot, user.id):
            return await handler(event, data)

        # Не подписан
        if isinstance(event, CallbackQuery):
            try:
                await event.answer("📢 Сначала подпишитесь на канал", show_alert=True)
            except Exception:
                pass
        if msg is not None:
            try:
                await show_gate(msg)
            except Exception:
                pass
        return  # обработчик не вызывается
