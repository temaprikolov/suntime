#!/usr/bin/env python3
"""
Антиспам система для бота SUNTIME SHOP
Ограничение: не более 10 сообщений за 2 секунды
"""

import time
from collections import defaultdict
from typing import Dict, Tuple
from functools import wraps
from aiogram.types import Message
from aiogram import Router
from aiogram.filters import Filter

# Настройки антиспама
MAX_MESSAGES = 10      # максимум сообщений
TIME_WINDOW = 2.0      # за 2 секунды
BAN_DURATION = 60      # блокировка на 60 секунд (в секундах)

# Хранилище: user_id -> список времён сообщений
_user_messages: Dict[int, list] = defaultdict(list)

# Заблокированные пользователи: user_id -> время разблокировки
_banned_users: Dict[int, float] = {}


def is_spamming(user_id: int) -> Tuple[bool, int]:
    """
    Проверяет, спамит ли пользователь.
    Возвращает (is_spam, seconds_left) — если is_spam=True, то секунды до разблокировки.
    """
    now = time.time()

    # Проверяем, не заблокирован ли пользователь
    if user_id in _banned_users:
        unban_time = _banned_users[user_id]
        if now < unban_time:
            seconds_left = int(unban_time - now) + 1
            return True, seconds_left
        else:
            # Блокировка истекла
            del _banned_users[user_id]
            _user_messages[user_id] = []

    # Очищаем старые сообщения
    messages = _user_messages[user_id]
    messages = [t for t in messages if now - t < TIME_WINDOW]
    _user_messages[user_id] = messages

    # Проверяем лимит
    if len(messages) >= MAX_MESSAGES:
        # Блокируем пользователя
        _banned_users[user_id] = now + BAN_DURATION
        return True, BAN_DURATION

    # Добавляем текущее сообщение
    messages.append(now)
    _user_messages[user_id] = messages
    return False, 0


def reset_user(user_id: int):
    """Сбросить историю сообщений пользователя"""
    if user_id in _user_messages:
        del _user_messages[user_id]
    if user_id in _banned_users:
        del _banned_users[user_id]


class AntiSpamFilter(Filter):
    """Фильтр для aiogram, блокирующий спам"""
    async def __call__(self, message: Message) -> bool:
        is_spam, seconds = is_spamming(message.from_user.id)
        if is_spam:
            await message.answer(
                f"🚫 <b>Полегче, отдохни минуту!</b>\n\n"
                f"Ты слишком часто отправляешь сообщения.\n"
                f"Подожди <b>{seconds}</b> секунд и попробуй снова.",
                parse_mode="HTML"
            )
            return False
        return True


def antispam_middleware():
    """
    Декоратор для ручной проверки антиспама в обработчиках.
    Используется как альтернатива фильтру.
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(message: Message, *args, **kwargs):
            is_spam, seconds = is_spamming(message.from_user.id)
            if is_spam:
                await message.answer(
                    f"🚫 <b>Полегче, отдохни минуту!</b>\n\n"
                    f"Подожди <b>{seconds}</b> секунд.",
                    parse_mode="HTML"
                )
                return
            return await func(message, *args, **kwargs)
        return wrapper
    return decorator


class AntiSpamMiddleware:
    """Middleware для aiogram — проверяет каждое сообщение"""
    async def __call__(self, handler, event: Message, data: dict):
        if isinstance(event, Message) and event.text and not event.text.startswith('/'):  # Не проверяем команды
            is_spam, seconds = is_spamming(event.from_user.id)
            if is_spam:
                await event.answer(
                    f"🚫 <b>Полегче, отдохни минуту!</b>\n\n"
                    f"Ты слишком часто отправляешь сообщения.\n"
                    f"Подожди <b>{seconds}</b> секунд и попробуй снова.",
                    parse_mode="HTML"
                )
                return
        return await handler(event, data)
