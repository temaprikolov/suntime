#!/usr/bin/env python3
"""
Модуль напоминаний:
1. Брошенная корзина — через 1 час после добавления товара
2. Долгое ожидание заказа — через 2.5 часа после оформления если статус всё ещё pending
"""
import asyncio
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.exceptions import TelegramForbiddenError

from database import Session, CartItem, Order
import config
from access_guard import ALLOWED_USER_IDS

CHECK_INTERVAL_SECONDS     = 1800  # проверка каждые 30 минут

CART_REMINDER_MINUTES      = 60    # напомнить о корзине через 1 час
ORDER_REMINDER_MINUTES     = 150   # напомнить об ожидании через 2.5 часа

# user_id тех кому уже отправили напоминание о корзине
_reminded_cart: set[int] = set()

# order_id тех кому уже отправили напоминание об ожидании
_reminded_order: set[int] = set()


async def _send_cart_reminders(bot: Bot):
    """Напоминание о брошенной корзине."""
    # ВАЖНО: раньше здесь стоял datetime.utcnow(), а все временные метки
    # в БД (added_at, created_at и т.д. — см. database.py) пишутся через
    # datetime.now(), то есть локальным временем сервера. На большинстве
    # хостингов (в т.ч. Beget) сервер настроен НЕ на UTC, а на локальный
    # часовой пояс (например, МСК = UTC+3) — из-за этого сравнение
    # "UTC сейчас минус 60 минут" с локальным added_at было сдвинуто на
    # разницу часовых поясов, и напоминания срабатывали не через час,
    # а с опозданием/опережением на этот сдвиг. Используем datetime.now(),
    # чтобы сравнивать одинаковые часовые пояса.
    threshold = datetime.now() - timedelta(minutes=CART_REMINDER_MINUTES)
    session = Session()
    try:
        rows = (
            session.query(CartItem.user_id)
            .filter(CartItem.added_at <= threshold)
            .distinct()
            .all()
        )
        for (user_id,) in rows:
            if user_id in _reminded_cart:
                continue

            items = session.query(CartItem).filter_by(user_id=user_id).all()
            if not items:
                continue

            text = "🛒 <b>Вы забыли оформить заказ!</b>\n\nВ вашей корзине:\n"
            for item in items:
                text += f"• {item.product_name} — {item.flavor_name} x{item.quantity}\n"
            text += "\nОформить заказ или очистить корзину 👇"

            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ ЗАКАЗАТЬ",  callback_data="place_order_no_discount")],
                [InlineKeyboardButton(text="🗑 ОЧИСТИТЬ",  callback_data="clear_cart")],
                [InlineKeyboardButton(text="🏪 В МАГАЗИН", callback_data="back_to_menu")],
            ])

            if user_id not in ALLOWED_USER_IDS:  # закрытый доступ: чужим не пишем
                continue

            try:
                await bot.send_message(user_id, text, reply_markup=keyboard, parse_mode="HTML")
                _reminded_cart.add(user_id)
                print(f"⏰ Напоминание о корзине → {user_id}")
            except TelegramForbiddenError:
                # Настоящий Forbidden от Telegram — пользователь сам заблокировал бота
                # или удалил аккаунт. Это НЕ бан — просто статистическая пометка,
                # доступ в бот не ограничивается. Бан выставляется только админом вручную.
                _reminded_cart.add(user_id)
                from database import Session, User as DbUser
                s = Session()
                try:
                    u = s.query(DbUser).filter_by(user_id=user_id).first()
                    if u and not u.left_bot:
                        u.left_bot = True
                        s.commit()
                except Exception:
                    s.rollback()
                finally:
                    s.close()
            except Exception as e:
                # Сетевые/временные ошибки — НЕ блокируем пользователя.
                print(f"⚠️ Не удалось отправить напоминание о корзине {user_id}: {e}")
    except Exception as e:
        print(f"cart reminder error: {e}")
    finally:
        session.close()


async def _send_order_reminders(bot: Bot):
    """Напоминание об ожидании заказа через 2.5 часа."""
    threshold = datetime.now() - timedelta(minutes=ORDER_REMINDER_MINUTES)
    session = Session()
    try:
        pending_orders = (
            session.query(Order)
            .filter(Order.status == "pending")
            .filter(Order.created_at <= threshold)
            .all()
        )
        for order in pending_orders:
            if order.id in _reminded_order:
                continue

            manager = (
                config.config.SHOP_MANAGER_ZAOZERNOE
                if getattr(order, "district", None) == "zaozernoe"
                else config.config.SHOP_MANAGER
            )
            text = (
                f"⏳ <b>Ваш заказ #{order.id} всё ещё обрабатывается.</b>\n\n"
                f"Если долго ждёте ответа — напишите напрямую:\n"
                f"👤 @{manager}"
            )

            if order.user_id not in ALLOWED_USER_IDS:  # закрытый доступ: чужим не пишем
                continue

            try:
                await bot.send_message(order.user_id, text, parse_mode="HTML")
                _reminded_order.add(order.id)
                print(f"⏰ Напоминание об ожидании → user {order.user_id}, order #{order.id}")
            except Exception as e:
                print(f"⚠️ Не удалось отправить напоминание об ожидании {order.user_id}: {e}")
    except Exception as e:
        print(f"order reminder error: {e}")
    finally:
        session.close()


async def reminder_loop(bot: Bot):
    """Запускается как фоновая задача в main.py"""
    print("⏰ Система напоминаний запущена")
    last_backup_day = -1
    while True:
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
        try:
            await _send_cart_reminders(bot)
            await _send_order_reminders(bot)
        except Exception as e:
            print(f"⚠️ reminder_loop error: {e}")

        # Автобэкап — один раз в сутки в ~03:00 по времени сервера
        now = datetime.now()
        if now.hour == 3 and now.day != last_backup_day:
            last_backup_day = now.day
            try:
                from admin import send_daily_backup
                await send_daily_backup(bot)
                print("🗄 Автобэкап отправлен")
            except Exception as e:
                print(f"⚠️ Автобэкап ошибка: {e}")


def clear_reminder(user_id: int):
    """Сбросить напоминание о корзине (при оформлении или очистке)."""
    _reminded_cart.discard(user_id)
