#!/usr/bin/env python3
"""
Закрытый доступ к боту.

Бот работает ТОЛЬКО для пользователей из ALLOWED_USER_IDS. Для всех остальных
любое обновление (сообщение, команда, нажатие кнопки, inline-запрос,
изменение статуса чата и т.д.) молча отбрасывается: бот не отвечает,
не показывает «часики» на кнопках и вообще ничего не делает.

Список можно переопределить в .env:  ALLOWED_USER_IDS=6815560227,8033943956
"""
import os

from aiogram import BaseMiddleware
from aiogram.types import Update

# @ShopManager45 и @artemiyanekdotov
_DEFAULT_ALLOWED = (6815560227, 8033943956)


def _load_allowed() -> frozenset[int]:
    raw = os.getenv("ALLOWED_USER_IDS", "").strip()
    if not raw:
        return frozenset(_DEFAULT_ALLOWED)
    ids = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            ids.add(int(part))
    return frozenset(ids) if ids else frozenset(_DEFAULT_ALLOWED)


ALLOWED_USER_IDS: frozenset[int] = _load_allowed()


class AccessGuardMiddleware(BaseMiddleware):
    """Внешняя middleware на уровне Update — срабатывает раньше любых хендлеров."""

    async def __call__(self, handler, event: Update, data: dict):
        user = data.get("event_from_user")
        if user is None or user.id not in ALLOWED_USER_IDS:
            return None  # молча игнорируем
        return await handler(event, data)
